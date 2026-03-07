"""
ziwei/agents/l1/executor.py
L1-A 执行管理者 —— 并行派发 + 质检 + 版本提交
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from ...core.base_agent import BaseAgentAdapter
from ...core.enums import AgentRole, TaskStatus
from ...core.models import (
    AgentResult, ArtifactVersion, ArtifactType,
    Task, TaskManifest, WorkspaceConfig,
    CapabilitySet, ResourceLimits, EscalationConfig,
)

logger = logging.getLogger("ziwei.l1.executor")

QUALITY_THRESHOLD  = 0.75   # 低于此分重试
ESCALATE_THRESHOLD = 0.60   # 低于此分升级 L0
MAX_RETRIES        = 2


class L1Executor:
    """
    L1-A 执行管理者。

    职责：
    - 接收 L0 执行计划，拆解为子任务
    - asyncio.gather() 并行派发给 L2 Workers
    - 汇聚结果，多维质检评分
    - 低分重试 / 升级 L0
    - 版本提交 + 上下文清理
    - 向下看护所有 L2（心跳检测）
    """

    def __init__(
        self,
        instance_id:   str,
        adapter:       BaseAgentAdapter,   # MiniMax-2.5
        worker_pool:   Any,                # WorkerPool
        version_store: Any,                # VersionStore
        l0_client:     Any,
        notifier:      Any,
        manifest_secret: str,
        context_limit: int = 80_000,
        compress_at:   float = 0.70,
    ):
        self.instance_id    = instance_id
        self.adapter        = adapter
        self.worker_pool    = worker_pool
        self.version_store  = version_store
        self.l0_client      = l0_client
        self.notifier       = notifier
        self.manifest_secret = manifest_secret
        self.context_limit  = context_limit
        self.compress_at    = compress_at

    # ── 主入口 ────────────────────────────────────────

    async def execute(self, task: Task) -> AgentResult:
        """
        执行父任务的完整流程：
        拆解 → 并行派发 → 汇聚 → 质检 → 提交 → 清理
        """
        logger.info(f"[L1-A:{self.instance_id}] 开始执行任务 {task.id}")

        # 1. 拆解子任务
        subtasks = await self._decompose(task)
        logger.info(f"[L1-A] 拆解为 {len(subtasks)} 个子任务")

        # 2. 并行派发（带超时容错）
        raw_results = await asyncio.gather(
            *[self._dispatch(st) for st in subtasks],
            return_exceptions=True,
        )

        # 3. 质检评分
        scored: List[Tuple[Task, AgentResult]] = []
        for st, result in zip(subtasks, raw_results):
            if isinstance(result, Exception):
                logger.error(f"子任务 {st.id} 异常: {result}")
                result = AgentResult(
                    agent_id=self.instance_id, agent_role=AgentRole.L1_EXECUTOR,
                    task_id=st.id, content="", confidence=0.0, score=0.0,
                )
            scored.append((st, await self._score(result, st)))

        # 4. 低分处理（重试 or 升级）
        final_results = []
        for st, r in scored:
            if r.score < ESCALATE_THRESHOLD:
                await self._escalate_to_l0(st, r)
            elif r.score < QUALITY_THRESHOLD and st.escalation_count < MAX_RETRIES:
                st.escalation_count += 1
                r = await self._retry(st)
            final_results.append(r)

        # 5. 版本提交
        for st, r in zip(subtasks, final_results):
            if r.score >= QUALITY_THRESHOLD and r.metadata.get("artifact"):
                await self.version_store.commit(ArtifactVersion(
                    task_id=st.id,
                    agent_id=self.instance_id,
                    artifact_type=ArtifactType(r.metadata.get("artifact_type", "code")),
                    content=r.content,
                    score=r.score,
                    status="committed",
                ))

        # 6. 清理所有 Worker 上下文
        await asyncio.gather(*[
            self.worker_pool.clear_worker(st.id) for st in subtasks
        ])

        # 7. 上下文自检（Manager 自身）
        await self._maybe_compress_context()

        return await self._aggregate(task, final_results)

    # ── 子任务拆解 ────────────────────────────────────

    async def _decompose(self, task: Task) -> List[Task]:
        """调用 MiniMax 拆解父任务为结构化子任务列表"""
        prompt = f"""
你是任务分解专家。请将以下任务拆解为独立的子任务 JSON 列表。
每个子任务包含: id, instruction, worker_type(doc/code/search/data), estimated_s

任务: {task.instruction}
上下文: {task.context}

只输出 JSON 数组，不要其他内容。
"""
        resp = await self.adapter.invoke(prompt)

        import json, uuid
        try:
            items = json.loads(resp.content)
        except Exception:
            # 降级：单子任务
            items = [{"instruction": task.instruction, "worker_type": "code", "estimated_s": 60}]

        subtasks = []
        for item in items:
            st = Task(
                id=str(uuid.uuid4()),
                instruction=item.get("instruction", task.instruction),
                parent_task_id=task.id,
                context={**task.context, "worker_type": item.get("worker_type", "code")},
            )
            subtasks.append(st)
        return subtasks

    # ── 派发给 L2 ─────────────────────────────────────

    async def _dispatch(self, subtask: Task) -> AgentResult:
        """给子任务分配 Worker，下发 TaskManifest"""
        worker_type = subtask.context.get("worker_type", "code")
        manifest = self._build_manifest(subtask, worker_type)

        worker = await self.worker_pool.acquire(worker_type, manifest)
        try:
            result = await worker.run(subtask)
            return result
        finally:
            await self.worker_pool.release(worker)

    def _build_manifest(self, subtask: Task, worker_type: str) -> TaskManifest:
        """为子任务构建 TaskManifest，按 worker_type 设置权限"""
        from datetime import datetime, timedelta
        from ...core.models import FilePerms, NetPerms, DBPerms, CodePerms

        # 按类型定制权限
        perms_map = {
            "doc":    CapabilitySet(
                file=FilePerms(read=True, write=True),
                net=NetPerms(intranet_get=True),
                db=DBPerms(select=True),
                skills=["text_summary", "spell_check"],
            ),
            "code":   CapabilitySet(
                file=FilePerms(read=True, write=True, append=True),
                net=NetPerms(intranet_get=True, external_get=True),
                db=DBPerms(select=True, insert=True),
                code=CodePerms(sandbox=True),
                skills=["code_execute"],
            ),
            "search": CapabilitySet(
                file=FilePerms(read=True, write=True),
                net=NetPerms(intranet_get=True, external_get=True),
                db=DBPerms(select=True),
                skills=["web_search", "url_fetch"],
            ),
            "data":   CapabilitySet(
                file=FilePerms(read=True, write=True),
                net=NetPerms(intranet_get=True, intranet_post=True),
                db=DBPerms(select=True, insert=True, update=True),
                skills=["data_viz"],
            ),
        }

        manifest = TaskManifest(
            task_id=subtask.id,
            parent_id=subtask.parent_task_id or "",
            worker_model=self._model_for(worker_type),
            issued_by=self.instance_id,
            workspace=WorkspaceConfig(
                root_path=f"/workspace/{worker_type}/{subtask.id}",
            ),
            capabilities=perms_map.get(worker_type, CapabilitySet()),
            limits=ResourceLimits(max_tokens=32_000, max_exec_time_s=120),
            escalation=EscalationConfig(),
        )
        return manifest.sign(self.manifest_secret)

    def _model_for(self, worker_type: str) -> str:
        return {
            "doc":    "glm-4v",
            "code":   "deepseek-coder",
            "search": "glm-4",
            "data":   "qwen-max",
        }.get(worker_type, "glm-4")

    # ── 质检评分 ─────────────────────────────────────

    async def _score(self, result: AgentResult, subtask: Task) -> AgentResult:
        """多维质检：完整度 · 准确度 · 格式合规"""
        if not result.content:
            result.score = 0.0
            return result

        prompt = f"""
请对以下任务执行结果进行质检，输出 JSON:
{{"completeness": 0.0-1.0, "accuracy": 0.0-1.0, "format": 0.0-1.0, "overall": 0.0-1.0, "issues": []}}

任务: {subtask.instruction}
结果: {result.content[:2000]}

只输出 JSON。
"""
        resp = await self.adapter.invoke(prompt)
        import json
        try:
            scores = json.loads(resp.content)
            result.score = scores.get("overall", 0.5)
            result.metadata["quality_scores"] = scores
        except Exception:
            result.score = 0.5
        return result

    async def _retry(self, subtask: Task) -> AgentResult:
        logger.info(f"[L1-A] 子任务 {subtask.id} 重试 (第{subtask.escalation_count}次)")
        return await self._dispatch(subtask)

    async def _escalate_to_l0(self, subtask: Task, result: AgentResult) -> None:
        logger.warning(f"[L1-A] 子任务 {subtask.id} 质检分 {result.score:.2f} 过低，上报 L0")
        await self.l0_client.post("/escalate", {
            "task_id": subtask.id,
            "score":   result.score,
            "result":  result,
            "reason":  "质检分低于阈值",
        })

    async def _aggregate(self, task: Task, results: List[AgentResult]) -> AgentResult:
        """聚合所有子任务结果为最终执行报告"""
        contents = "\n\n".join(r.content for r in results if r.content)
        avg_score = sum(r.score for r in results) / max(len(results), 1)
        avg_conf  = sum(r.confidence for r in results) / max(len(results), 1)

        return AgentResult(
            agent_id=self.instance_id,
            agent_role=AgentRole.L1_EXECUTOR,
            task_id=task.id,
            content=contents,
            summary=f"共 {len(results)} 个子任务，平均质检分 {avg_score:.2f}",
            confidence=avg_conf,
            score=avg_score,
            passed=avg_score >= QUALITY_THRESHOLD,
        )

    # ── 上下文压缩 ────────────────────────────────────

    async def _maybe_compress_context(self) -> None:
        """检查 Token 用量，达阈值则压缩"""
        ratio = self.adapter.tokens_used / max(self.context_limit, 1)
        if ratio < self.compress_at:
            return

        logger.info(f"[L1-A] Token 用量 {ratio:.0%}，触发上下文压缩")

        # 生成摘要
        history_text = "\n".join(
            f"{m.role}: {m.content[:200]}"
            for m in self.adapter._history[-20:]
        )
        prompt = (
            "请将以下对话历史提炼为结构化摘要，保留：关键决策、任务状态、待处理事项。"
            f"\n\n{history_text}"
        )
        resp = await self.adapter.invoke(prompt, remember=False)
        self.adapter.compress_history(resp.content, keep_last=5)
        logger.info("[L1-A] 上下文压缩完成")
