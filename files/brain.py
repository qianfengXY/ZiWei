"""
ziwei/agents/l0/brain.py
L0 Brain —— 规划 + 三重验证 + 最终审核
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from ...core.base_agent import BaseAgentAdapter
from ...core.enums import AgentRole, AuditVerdict, VerifyVerdict
from ...core.exceptions import AuditFailedError, MaxEscalationError
from ...core.models import (
    AgentResult, AuditResult, FinalResult, Task, TaskComplexity,
)

logger = logging.getLogger("ziwei.l0.brain")

CONFIDENCE_THRESHOLD = 0.80   # 双 L1 一致判定阈值
SIMILARITY_THRESHOLD = 0.85   # 语义相似度阈值
AUDIT_THRESHOLD      = 0.80   # 最终审核通过阈值
MAX_AUDIT_RETRIES    = 2


class L0Brain:
    """
    L0 决策脑。

    流程：
    ① 任务规划 & 动态模型分配
    ② 下发 L1-A Executor
    ③ 接收双 L1 结果 → 仲裁门比对
    ④ 不一致 → 召唤 L1-C Tiebreaker
    ⑤ 四维最终审核
    ⑥ 通过 → 版本提交 → 输出
    ⑦ 不通过 → 修正指令重试（最多2次）
    """

    def __init__(
        self,
        adapter:       BaseAgentAdapter,   # Claude Opus 4.6
        l1_executor:   Any,                # L1Executor instance
        l1_validator:  Any,                # L1Validator instance
        l1_tiebreaker: Any,                # L1Tiebreaker instance
        version_store: Any,
        notifier:      Any,
        embedder:      Any,                # 语义相似度计算
    ):
        self.adapter        = adapter
        self.l1_executor    = l1_executor
        self.l1_validator   = l1_validator
        self.l1_tiebreaker  = l1_tiebreaker
        self.version_store  = version_store
        self.notifier       = notifier
        self.embedder       = embedder

    # ── 主入口 ────────────────────────────────────────

    async def run(self, task: Task) -> FinalResult:
        logger.info(f"[L0] 开始处理任务 {task.id}: {task.instruction[:80]}")

        for attempt in range(MAX_AUDIT_RETRIES + 1):
            try:
                return await self._pipeline(task, attempt)
            except AuditFailedError as e:
                if attempt >= MAX_AUDIT_RETRIES:
                    logger.error(f"[L0] 审核连续失败 {MAX_AUDIT_RETRIES} 次，上报人工")
                    await self._notify_human_critical(task, str(e))
                    raise
                logger.warning(f"[L0] 审核不通过，追加修正指令重试 (第{attempt+1}次)")
                task.context["correction"] = e.correction

        raise RuntimeError("不可达")

    async def _pipeline(self, task: Task, attempt: int) -> FinalResult:
        # ① 规划 & 模型分配
        plan = await self._plan(task)
        logger.info(f"[L0] 执行计划: {plan}")

        # ② 执行者 & 验证者并发启动
        #    验证者等执行者完成后才开始（用 Event 同步）
        exec_done = asyncio.Event()
        exec_result_holder: Dict[str, AgentResult] = {}

        async def run_executor():
            r = await self.l1_executor.execute(task)
            exec_result_holder["result"] = r
            exec_done.set()

        async def run_validator():
            await exec_done.wait()            # 等执行者完成
            exec_r = exec_result_holder["result"]
            return await self.l1_validator.validate(task, exec_r, model=plan["validator"])

        exec_task = asyncio.create_task(run_executor())
        val_task  = asyncio.create_task(run_validator())

        await asyncio.gather(exec_task, val_task)
        exec_result = exec_result_holder["result"]
        val_report  = val_task.result()

        # ③ 仲裁门
        verdict = await self._compare(exec_result, val_report)

        if verdict == VerifyVerdict.CONFLICT:
            # ④ 召唤 L1-C
            logger.warning(f"[L0] 双 L1 结果冲突，召唤仲裁者")
            tb_result = await self.l1_tiebreaker.arbitrate(
                task=task,
                report_a=exec_result,
                report_b=val_report,
                model=plan["tiebreaker"],
            )
            final_input = await self._merge_with_tiebreaker(exec_result, val_report, tb_result)
        else:
            final_input = exec_result

        # ⑤ 最终审核
        audit = await self._audit(task, final_input)

        if audit.passed:
            # ⑥ 版本提交 + 输出
            await self.version_store.commit_audit(audit)
            logger.info(f"[L0] ✅ 审核通过 (分={audit.overall:.2f})，提交版本 {audit.version_id}")
            return FinalResult(
                task_id=task.id,
                content=final_input.content,
                confidence=audit.overall,
                version_id=audit.version_id or "",
                trace=self._build_trace(plan, exec_result, val_report, audit),
                verified_by=[exec_result.agent_id, val_report.agent_id],
                audit_scores={
                    "logic":     audit.logic_score,
                    "factual":   audit.factual_score,
                    "risk":      audit.risk_score,
                    "alignment": audit.alignment_score,
                },
            )
        else:
            raise AuditFailedError(audit.overall, audit.correction or "需要修正")

    # ── 任务规划 & 模型分配 ────────────────────────────

    async def _plan(self, task: Task) -> Dict[str, str]:
        """
        L0 分析任务类型，动态选择执行/验证/仲裁模型组合。
        三不原则：不同源 · 不共享 · 不知情。
        """
        prompt = f"""
分析以下任务类型，输出最适合的模型组合 JSON:
{{"task_type": "code|doc|data|reasoning",
  "executor":   "minimax-2.5",
  "validator":  "doubao-pro|glm-4-plus|qwen-max",
  "tiebreaker": "qwen-max|doubao-pro|glm-4-plus"}}

规则：executor/validator/tiebreaker 必须来自不同厂商。

任务: {task.instruction[:500]}
"""
        resp = await self.adapter.invoke(prompt, remember=False)
        import json
        try:
            return json.loads(resp.content)
        except Exception:
            return {
                "task_type":  "general",
                "executor":   "minimax-2.5",
                "validator":  "doubao-pro",
                "tiebreaker": "qwen-max",
            }

    # ── 仲裁门 ────────────────────────────────────────

    async def _compare(
        self, exec_r: AgentResult, val_r: AgentResult
    ) -> VerifyVerdict:
        """语义相似度 + 置信分双重判定"""
        both_confident = (
            exec_r.confidence >= CONFIDENCE_THRESHOLD and
            val_r.confidence  >= CONFIDENCE_THRESHOLD
        )
        if not both_confident:
            return VerifyVerdict.CONFLICT

        similarity = await self.embedder.cosine_sim(exec_r.summary, val_r.summary)
        logger.info(f"[L0] 双 L1 语义相似度: {similarity:.3f}，置信: exec={exec_r.confidence:.2f} val={val_r.confidence:.2f}")

        return VerifyVerdict.AGREE if similarity >= SIMILARITY_THRESHOLD else VerifyVerdict.CONFLICT

    async def _merge_with_tiebreaker(
        self,
        exec_r: AgentResult,
        val_r:  AgentResult,
        tb_r:   AgentResult,
    ) -> AgentResult:
        """三方结果合并：以仲裁者建议为准"""
        prompt = f"""
综合以下三份报告，生成最终整合结果：

报告A: {exec_r.content[:1500]}
报告B: {val_r.content[:1500]}
仲裁意见: {tb_r.content[:1000]}

请输出最准确、最完整的最终答案。
"""
        resp = await self.adapter.invoke(prompt, remember=False)
        return AgentResult(
            agent_id="l0_merged",
            agent_role=AgentRole.L0_BRAIN,
            task_id=exec_r.task_id,
            content=resp.content,
            summary=resp.content[:300],
            confidence=max(exec_r.confidence, val_r.confidence, tb_r.confidence),
            score=max(exec_r.score, val_r.score),
            passed=True,
        )

    # ── 最终审核（四维）──────────────────────────────

    async def _audit(self, task: Task, result: AgentResult) -> AuditResult:
        prompt = f"""
对以下执行结果进行四维审核，输出 JSON:
{{
  "logic_score":     0.0-1.0,  // 推理链完整性
  "factual_score":   0.0-1.0,  // 核心事实准确性
  "risk_score":      0.0-1.0,  // 风险等级（1=无风险，0=高风险）
  "alignment_score": 0.0-1.0,  // 与用户意图对齐
  "passed":          true/false,
  "correction":      "不通过时的修正指令，通过则为null"
}}

用户任务: {task.instruction}
执行结果: {result.content[:3000]}
修正历史: {task.context.get('correction', '无')}

只输出 JSON。
"""
        resp = await self.adapter.invoke(prompt, remember=False)
        import json, uuid
        try:
            data = json.loads(resp.content)
        except Exception:
            data = {"logic_score": 0.5, "factual_score": 0.5,
                    "risk_score": 0.5, "alignment_score": 0.5,
                    "passed": False, "correction": "解析审核结果失败，需重试"}

        audit = AuditResult(
            task_id=task.id,
            logic_score=data.get("logic_score", 0.5),
            factual_score=data.get("factual_score", 0.5),
            risk_score=data.get("risk_score", 0.5),
            alignment_score=data.get("alignment_score", 0.5),
            correction=data.get("correction"),
            version_id=str(uuid.uuid4())[:8],
        )
        audit.passed = audit.overall >= AUDIT_THRESHOLD and data.get("passed", False)
        return audit

    # ── 工具方法 ─────────────────────────────────────

    def _build_trace(self, plan, exec_r, val_r, audit) -> Dict:
        return {
            "plan":          plan,
            "executor":      {"id": exec_r.agent_id, "score": exec_r.score},
            "validator":     {"id": val_r.agent_id,  "confidence": val_r.confidence},
            "audit_overall": audit.overall,
        }

    async def _notify_human_critical(self, task: Task, reason: str) -> None:
        await self.notifier.push_urgent(
            channels=["dingtalk", "slack"],
            payload={
                "level":   "CRITICAL",
                "task_id": task.id,
                "reason":  reason,
                "message": "L0 连续审核失败，需要人工介入",
            },
        )
