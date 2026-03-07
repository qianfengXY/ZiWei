"""
ziwei/agents/l2/worker.py
L2 Worker 运行时 —— 权限检查门 + 四级上报梯
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, Optional

from ...core.enums import RiskLevel, SpecialMode, TaskStatus
from ...core.exceptions import (
    ManifestExpiredError, ManifestInvalidError,
    MaxEscalationError, PermissionDeniedError,
)
from ...core.models import (
    Action, ActionResult, Task, TaskManifest,
    TaskTimeDeclaration, WorkerHeartbeat,
)

logger = logging.getLogger("ziwei.l2.worker")

# 高危操作类型集合 —— 必须找 L0 决策
HIGH_RISK_OPS = {
    "file.delete", "file.execute",
    "db.delete", "db.ddl",
    "net.paid_api",
    "sys.modify_env", "sys.modify_config",
}

# 需要 L1 授权的操作类型集合
NEED_L1_OPS = {
    "file.write", "file.append",
    "db.insert", "db.update",
    "net.external_get", "net.external_post", "net.intranet_post",
    "code.read_host",
    "skill.new_request",
}


class L2Worker:
    """
    L2 Worker 运行时。

    职责：
    - 执行 L1 下发的子任务
    - 每步操作前通过权限门 classify_risk()
    - 按四级上报梯上报（ALLOWED / NEED_L1 / HIGH_RISK / FATAL）
    - 每 5s 向 L1 发送心跳
    - 任务完成后主动清空上下文
    """

    MAX_ESCALATIONS = 3

    def __init__(
        self,
        worker_id:  str,
        manifest:   TaskManifest,
        l1_client:  Any,   # L1 HTTP 客户端
        l0_client:  Any,   # L0 HTTP 客户端
        notifier:   Any,   # 人工通知客户端
        secret:     str,   # Manifest 签名密钥
    ):
        self.worker_id  = worker_id
        self.manifest   = manifest
        self.l1_client  = l1_client
        self.l0_client  = l0_client
        self.notifier   = notifier
        self.secret     = secret

        self._tokens_used     = 0
        self._step_name       = "init"
        self._escalation_count = 0
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._time_decl: Optional[TaskTimeDeclaration] = None

    # ── 启动 ─────────────────────────────────────────

    async def start(self, time_decl: TaskTimeDeclaration) -> None:
        """Worker 启动：验证 Manifest + 提交时间声明 + 启动心跳"""
        self._validate_manifest()
        self._time_decl = time_decl

        # 向 L1 提交时间声明（防止被误判呆死）
        await self.l1_client.post("/worker/declare", {
            "worker_id": self.worker_id,
            "declaration": time_decl,
        })

        # 启动后台心跳
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"[{self.worker_id}] 启动成功，特殊模式: {time_decl.special_mode}")

    async def stop(self, result: Any = None) -> None:
        """Worker 完成：上报结果 + 停止心跳 + 清空上下文"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        # 上报任务结果给 L1
        await self.l1_client.post("/worker/result", {
            "worker_id": self.worker_id,
            "task_id":   self.manifest.task_id,
            "result":    result,
        })
        logger.info(f"[{self.worker_id}] 任务完成，上下文已清空")

    # ── 权限检查门（核心）────────────────────────────

    async def do(self, action: Action, executor: Callable[..., Coroutine]) -> ActionResult:
        """
        每步操作必须经此门。
        1. 验证 Manifest 有效性
        2. 分类风险等级
        3. 按等级路由：执行 / L1授权 / L0决策 / 人工
        4. 执行后写版本快照
        """
        # 0. Manifest 有效性检查
        self._validate_manifest()

        action.task_id = self.manifest.task_id
        risk = self.classify_risk(action)

        try:
            if risk == RiskLevel.ALLOWED:
                result = await self._execute(action, executor)

            elif risk == RiskLevel.NEED_L1:
                grant = await self._request_l1_grant(action)
                if not grant.success:
                    return ActionResult.denied(grant.error or "L1 拒绝授权")
                result = await self._execute(action, executor, token=grant.token_id)

            elif risk == RiskLevel.HIGH_RISK:
                decision = await self._request_l0_decision(action)
                if not decision.success:
                    return ActionResult.denied(decision.error or "L0 拒绝高危操作")
                result = await self._execute(action, executor, token=decision.token_id)

            else:  # FATAL
                approval = await self._request_human(action)
                if not approval:
                    return ActionResult.pending_human()
                result = await self._execute(action, executor, token=approval)

        except Exception as e:
            logger.error(f"[{self.worker_id}] 执行失败: {e}")
            raise

        # 写版本快照（每步）
        await self._snapshot(action, result)
        return result

    # ── 风险分类器 ────────────────────────────────────

    def classify_risk(self, action: Action) -> RiskLevel:
        """
        四级风险判定：
        ALLOWED   → Manifest 范围内
        NEED_L1   → 能力不足，可申请
        HIGH_RISK → 高危，必须 L0 批准
        FATAL     → 未知/超限，推人工
        """
        # 已超出 Manifest 最大权限上界
        if action.exceeds_max_permission(self.manifest):
            return RiskLevel.HIGH_RISK

        # Manifest 明确允许
        if self.manifest.capabilities.permits(action):
            return RiskLevel.ALLOWED

        # 可向 L1 申请
        if action.type in NEED_L1_OPS:
            return RiskLevel.NEED_L1

        # 高危操作
        if action.type in HIGH_RISK_OPS:
            return RiskLevel.HIGH_RISK

        # 其余未知操作 → 致命卡点
        return RiskLevel.FATAL

    # ── 上报方法 ─────────────────────────────────────

    async def _request_l1_grant(self, action: Action) -> ActionResult:
        """向 L1 申请临时权限令牌"""
        self._escalation_count += 1
        if self._escalation_count > self.MAX_ESCALATIONS:
            raise MaxEscalationError(f"超出最大上报次数 {self.MAX_ESCALATIONS}")

        logger.info(f"[{self.worker_id}] → L1 申请授权: {action.type}")
        try:
            resp = await self.l1_client.post(
                self.manifest.escalation.l1_endpoint,
                {
                    "worker_id": self.worker_id,
                    "task_id":   self.manifest.task_id,
                    "action":    action,
                    "reason":    action.reason,
                },
            )
            return ActionResult(
                success=resp.get("approved", False),
                token_id=resp.get("token_id"),
                error=resp.get("reason"),
            )
        except Exception as e:
            logger.error(f"L1 授权请求失败: {e}")
            return ActionResult.denied(str(e))

    async def _request_l0_decision(self, action: Action) -> ActionResult:
        """向 L0 上报高危操作，等待决策"""
        logger.warning(f"[{self.worker_id}] ⚠ → L0 高危上报: {action.type}")
        try:
            resp = await self.l0_client.post(
                self.manifest.escalation.l0_endpoint,
                {
                    "worker_id": self.worker_id,
                    "task_id":   self.manifest.task_id,
                    "action":    action,
                    "reason":    action.reason,
                    "risk_context": self._build_risk_context(action),
                },
            )
            return ActionResult(
                success=resp.get("approved", False),
                token_id=resp.get("token_id"),
                error=resp.get("reason"),
            )
        except Exception as e:
            logger.error(f"L0 决策请求失败: {e}")
            return ActionResult.denied(str(e))

    async def _request_human(self, action: Action) -> Optional[str]:
        """
        致命卡点：推送多渠道通知，阻塞等待人工响应。
        超时 human_timeout_s 后返回 None（保存现场）。
        """
        logger.error(f"[{self.worker_id}] 🚨 → 人工决策: {action.type}")

        # 推送通知
        await self.notifier.push_urgent(
            channels=self.manifest.escalation.human_channels,
            payload={
                "worker_id":  self.worker_id,
                "task_id":    self.manifest.task_id,
                "action":     action,
                "reason":     action.reason,
                "step":       self._step_name,
            },
        )

        if not self.manifest.escalation.block_on_human:
            return "auto_notify_only"

        # 阻塞等待（轮询 Redis 中的审批结果）
        timeout = self.manifest.escalation.human_timeout_s
        interval = 10
        elapsed  = 0

        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval

            approval = await self.l1_client.get(
                f"/human/approval/{self.manifest.task_id}"
            )
            if approval and approval.get("approved"):
                logger.info(f"[{self.worker_id}] ✅ 人工已批准")
                return approval.get("token_id")
            if approval and approval.get("rejected"):
                logger.info(f"[{self.worker_id}] ❌ 人工已拒绝")
                return None

            # 每 10 分钟重推
            if elapsed % 600 == 0:
                await self.notifier.push_reminder(
                    channels=self.manifest.escalation.human_channels,
                    task_id=self.manifest.task_id,
                    elapsed_min=elapsed // 60,
                )

        logger.warning(f"[{self.worker_id}] 人工决策超时，保存现场")
        return None

    # ── 内部工具 ─────────────────────────────────────

    async def _execute(
        self, action: Action,
        executor: Callable[..., Coroutine],
        token: Optional[str] = None,
    ) -> ActionResult:
        self._step_name = action.type
        result = await executor(action, token=token)
        self._tokens_used += getattr(result, "tokens", 0)
        return result

    async def _snapshot(self, action: Action, result: ActionResult) -> None:
        """每步执行后向版本库写快照"""
        try:
            await self.l1_client.post("/version/snapshot", {
                "task_id":   self.manifest.task_id,
                "worker_id": self.worker_id,
                "action":    action.type,
                "success":   result.success,
            })
        except Exception as e:
            logger.warning(f"快照写入失败（不影响执行）: {e}")

    async def _heartbeat_loop(self) -> None:
        """每 5 秒向 L1 发送心跳"""
        while True:
            try:
                hb = WorkerHeartbeat(
                    task_id=self.manifest.task_id,
                    worker_id=self.worker_id,
                    step_name=self._step_name,
                    tokens_used=self._tokens_used,
                    tokens_budget=self.manifest.limits.max_tokens,
                    elapsed_s=0,         # 由调用方填充
                    estimated_total_s=self._time_decl.estimated_s if self._time_decl else 0,
                    special_mode=self._time_decl.special_mode if self._time_decl else None,
                )
                await self.l1_client.post("/worker/heartbeat", hb)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"心跳发送失败: {e}")
            await asyncio.sleep(5)

    def _validate_manifest(self) -> None:
        if self.manifest.is_expired:
            raise ManifestExpiredError(f"Manifest {self.manifest.task_id} 已过期")
        if not self.manifest.verify(self.secret):
            raise ManifestInvalidError("Manifest 签名校验失败")

    def _build_risk_context(self, action: Action) -> Dict:
        return {
            "step":           self._step_name,
            "tokens_used":    self._tokens_used,
            "escalation_cnt": self._escalation_count,
            "action_params":  action.params,
        }
