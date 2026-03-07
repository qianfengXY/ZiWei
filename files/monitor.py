"""
ziwei/infra/watchdog/monitor.py
健康看护系统 —— L0 看护 L1，L1 看护 L2
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ...core.enums import SpecialMode, StaleVerdict
from ...core.models import WorkerHeartbeat, TaskTimeDeclaration

logger = logging.getLogger("ziwei.watchdog")

# ── 判定阈值 ────────────────────────────────────────
HEARTBEAT_TIMEOUT_S    = 45      # 3 次心跳超时 = 45s
TOKEN_RATE_THRESHOLD   = 500     # tokens/min 异常阈值
RESOURCE_THRESHOLD     = 0.90    # CPU/内存 90%
RESOURCE_DURATION_S    = 60      # 持续超限 60s
TASK_OVERSHOOT_RATIO   = 2.0     # 超预估时长 2x → 预警
STEP_STALE_RATIO       = 3.0     # 单步超预估 3x → 呆死
MILESTONE_TIMEOUT_S    = 600     # 长任务 10min 无里程碑 → 预警
DOWNLOAD_STALE_S       = 30      # 下载任务 30s 无字节增长 → 呆死


@dataclass
class WorkerState:
    worker_id:      str
    task_id:        str
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)
    special_mode:   Optional[SpecialMode] = None

    # 进度信息
    step_name:      str = "init"
    step_started:   datetime = field(default_factory=datetime.utcnow)
    step_estimated_s: int = 60
    tokens_used:    int = 0
    token_rate_history: list = field(default_factory=list)  # 最近 N 次速率

    # 下载任务
    bytes_at_last_check: int = 0
    bytes_check_time:    datetime = field(default_factory=datetime.utcnow)

    # 长任务
    last_milestone_at: datetime = field(default_factory=datetime.utcnow)
    last_milestone:    str = ""

    # 资源
    cpu_high_since:    Optional[datetime] = None
    mem_high_since:    Optional[datetime] = None

    def update(self, hb: WorkerHeartbeat) -> None:
        self.last_heartbeat = hb.timestamp
        self.special_mode   = hb.special_mode
        self.step_name      = hb.step_name
        self.tokens_used    = hb.tokens_used
        if hb.last_milestone and hb.last_milestone != self.last_milestone:
            self.last_milestone    = hb.last_milestone
            self.last_milestone_at = hb.timestamp
        if hb.bytes_delta > 0:
            self.bytes_at_last_check = hb.bytes_delta
            self.bytes_check_time    = hb.timestamp


# ─────────────────────────────────────────
# L1 → L2 看护器
# ─────────────────────────────────────────

class L2Watchdog:
    """
    L1 Manager 用于看护旗下所有 L2 Workers。
    每 10s 检查心跳，每 20s 检查资源和进度。
    """

    def __init__(
        self,
        l0_client:    Any,
        worker_pool:  Any,
        notifier:     Any,
        version_store: Any,
    ):
        self.l0_client    = l0_client
        self.worker_pool  = worker_pool
        self.notifier     = notifier
        self.version_store = version_store
        self._states: Dict[str, WorkerState] = {}
        self._running = False

    async def start(self) -> None:
        self._running = True
        await asyncio.gather(
            self._heartbeat_loop(),
            self._progress_loop(),
        )

    async def stop(self) -> None:
        self._running = False

    def register(self, worker_id: str, task_id: str, decl: TaskTimeDeclaration) -> None:
        self._states[worker_id] = WorkerState(
            worker_id=worker_id,
            task_id=task_id,
            special_mode=decl.special_mode,
            step_estimated_s=decl.estimated_s,
        )

    def update_heartbeat(self, hb: WorkerHeartbeat) -> None:
        if hb.worker_id in self._states:
            self._states[hb.worker_id].update(hb)

    # ── 心跳检测（每 10s）────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while self._running:
            now = datetime.utcnow()
            for wid, state in list(self._states.items()):
                elapsed = (now - state.last_heartbeat).total_seconds()
                if elapsed > HEARTBEAT_TIMEOUT_S:
                    logger.error(f"[L2-WD] Worker {wid} 心跳超时 {elapsed:.0f}s，判定呆死")
                    await self._handle_stale(wid, state, reason="心跳超时")
            await asyncio.sleep(10)

    # ── 进度 & 资源检测（每 20s）─────────────────────

    async def _progress_loop(self) -> None:
        while self._running:
            for wid, state in list(self._states.items()):
                verdict = self._is_stale(state)
                if verdict == StaleVerdict.STALE:
                    await self._handle_stale(wid, state, reason="进度/资源异常")
                elif verdict == StaleVerdict.WARNING:
                    logger.warning(f"[L2-WD] Worker {wid} 预警，但继续执行")
                    await self.notifier.push_warning(wid, state)
            await asyncio.sleep(20)

    def _is_stale(self, state: WorkerState) -> StaleVerdict:
        """五维呆死判断（考虑特殊模式）"""

        # 等待人工：完全豁免
        if state.special_mode == SpecialMode.WAIT_HUMAN:
            return StaleVerdict.EXEMPT

        # 下载任务：只看字节速率
        if state.special_mode == SpecialMode.DOWNLOAD:
            elapsed = (datetime.utcnow() - state.bytes_check_time).total_seconds()
            if elapsed > DOWNLOAD_STALE_S and state.bytes_at_last_check == 0:
                return StaleVerdict.STALE
            return StaleVerdict.HEALTHY

        # 长任务：看里程碑
        if state.special_mode == SpecialMode.LONG_JOB:
            ms_elapsed = (datetime.utcnow() - state.last_milestone_at).total_seconds()
            if ms_elapsed > MILESTONE_TIMEOUT_S:
                return StaleVerdict.WARNING   # 先预警，不直接中断
            return StaleVerdict.HEALTHY

        # ── 普通任务：全维度检测 ──

        # 1. 单步超时（超预估 3x）
        step_elapsed = (datetime.utcnow() - state.step_started).total_seconds()
        if step_elapsed > state.step_estimated_s * STEP_STALE_RATIO:
            return StaleVerdict.STALE

        # 2. Token 消耗速率异常
        if len(state.token_rate_history) >= 3:
            avg_rate = sum(state.token_rate_history[-3:]) / 3
            if avg_rate > TOKEN_RATE_THRESHOLD:
                return StaleVerdict.STALE

        # 3. 资源超限（需持续超过阈值一段时间）
        if state.cpu_high_since:
            if (datetime.utcnow() - state.cpu_high_since).total_seconds() > RESOURCE_DURATION_S:
                return StaleVerdict.STALE
        if state.mem_high_since:
            if (datetime.utcnow() - state.mem_high_since).total_seconds() > RESOURCE_DURATION_S:
                return StaleVerdict.STALE

        return StaleVerdict.HEALTHY

    async def _handle_stale(self, worker_id: str, state: WorkerState, reason: str) -> None:
        logger.error(f"[L2-WD] 处理呆死 Worker {worker_id}: {reason}")

        # 1. 保存现场快照
        await self.version_store.snapshot_stale(state.task_id, worker_id, reason)

        # 2. 强制终止 Worker
        await self.worker_pool.kill(worker_id)

        # 3. 上报 L0
        await self.l0_client.post("/worker/stale", {
            "worker_id": worker_id,
            "task_id":   state.task_id,
            "reason":    reason,
            "step":      state.step_name,
        })

        # 4. 清理状态
        del self._states[worker_id]


# ─────────────────────────────────────────
# L0 → L1 看护器
# ─────────────────────────────────────────

class L1Watchdog:
    """
    L0 Brain 用于看护所有 L1 实例。
    检测到 L1 挂掉后触发重启，L2 Workers 不受影响。
    """

    HEARTBEAT_INTERVAL_S = 15
    CHECK_INTERVAL_S     = 15
    MAX_MISS             = 3     # 连续 3 次未收到 → 判定挂掉

    def __init__(self, supervisor_client: Any, redis_client: Any, notifier: Any):
        self.supervisor = supervisor_client
        self.redis      = redis_client
        self.notifier   = notifier
        self._miss_count: Dict[str, int] = {}
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._watch_loop()

    async def _watch_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.CHECK_INTERVAL_S)

            l1_instances = await self.redis.smembers("ziwei:l1:instances")
            for l1_id in l1_instances:
                last_hb = await self.redis.get(f"ziwei:l1:heartbeat:{l1_id}")
                if last_hb is None:
                    self._miss_count[l1_id] = self._miss_count.get(l1_id, 0) + 1
                    logger.warning(f"[L1-WD] L1 {l1_id} 心跳缺失 {self._miss_count[l1_id]} 次")
                    if self._miss_count[l1_id] >= self.MAX_MISS:
                        await self._restart_l1(l1_id)
                else:
                    self._miss_count[l1_id] = 0   # 收到心跳，重置计数

    async def _restart_l1(self, l1_id: str) -> None:
        logger.error(f"[L1-WD] L1 {l1_id} 确认挂掉，触发重启")

        # L2 Workers 不受影响，继续缓存心跳到 Redis
        # 重启 L1
        await self.supervisor.restart(l1_id)

        # 推送告警
        await self.notifier.push_warning(
            channels=["dingtalk", "slack"],
            payload={
                "level":   "WARNING",
                "message": f"L1 Manager {l1_id} 发生故障并已自动重启",
            },
        )
        self._miss_count[l1_id] = 0
