"""
ziwei/agents/l1/tiebreaker.py
L1-C Tiebreaker - 简化版仲裁者
当 L1-A 和 L1-B 结果冲突时，第三方模型仲裁
"""

import logging
from typing import Any, Dict, Optional

from ...core.base_agent import BaseAgentAdapter
from ...core.enums import AgentRole
from ...core.models import AgentResult, Task


logger = logging.getLogger("ziwei.l1.tiebreaker")


class L1Tiebreaker:
    """
    L1-C Tiebreaker
    仅在 L1-A 和 L1-B 结果不一致时召唤
    MVP 阶段：调用第三方模型做仲裁
    """

    def __init__(
        self,
        instance_id: str,
        adapter: BaseAgentAdapter,
    ):
        self.instance_id = instance_id
        self.adapter = adapter

    async def arbitrate(
        self,
        task: Task,
        report_a: AgentResult,
        report_b: AgentResult,
        model: str = "",
    ) -> AgentResult:
        """
        仲裁 A/B 双方报告

        Args:
            task: 原始任务
            report_a: 执行者报告 (L1-A)
            report_b: 验证者报告 (L1-B)
            model: 使用的模型

        Returns:
            AgentResult: 仲裁结果
        """
        logger.info(f"[L1-C:{self.instance_id}] 开始仲裁任务 {task.id}")

        prompt = self._build_arbitration_prompt(task, report_a, report_b)

        resp = await self.adapter.invoke(prompt, remember=False)

        arbitration_result = self._parse_arbitration_response(resp.content)

        result = AgentResult(
            agent_id=self.instance_id,
            agent_role=AgentRole.L1_TIEBREAKER,
            task_id=task.id,
            content=resp.content,
            summary=arbitration_result.get("summary", ""),
            confidence=arbitration_result.get("confidence", 0.5),
            score=arbitration_result.get("score", 0.5),
            passed=arbitration_result.get("passed", False),
            metadata={
                "arbitration_type": "third_party_model",
                "model_used": model or self.adapter.model_name,
                "decision": arbitration_result.get("decision", "unknown"),
                "reasoning": arbitration_result.get("reasoning", ""),
            },
        )

        logger.info(
            f"[L1-C:{self.instance_id}] 仲裁完成: "
            f"decision={result.metadata.get('decision')}, "
            f"confidence={result.confidence:.2f}"
        )

        return result

    def _build_arbitration_prompt(
        self,
        task: Task,
        report_a: AgentResult,
        report_b: AgentResult,
    ) -> str:
        """构建仲裁提示"""
        return f"""
你是任务仲裁专家。L1-A 执行者和 L1-B 验证者的结果不一致，需要你做出最终裁决。

## 原始任务
{task.instruction}

## L1-A 执行者报告
{report_a.content[:1500]}

评分: {report_a.score:.2f}, 置信度: {report_a.confidence:.2f}

## L1-B 验证者报告
{report_b.content[:1500]}

评分: {report_b.score:.2f}, 置信度: {report_b.confidence:.2f}

请综合分析双方报告，给出最终仲裁结果。

请以 JSON 格式输出：
{{
    "decision": "accept_a" | "accept_b" | "hybrid" | "reject_both",
    "confidence": 0.0-1.0,
    "score": 0.0-1.0,
    "passed": true/false,
    "summary": "仲裁摘要",
    "reasoning": "详细推理过程"
}}

只输出 JSON，不要其他内容。
"""

    def _parse_arbitration_response(self, content: str) -> Dict[str, Any]:
        """解析仲裁响应"""
        import json

        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                return json.loads(json_str)
        except Exception as e:
            logger.warning(f"解析仲裁响应失败: {e}")

        return {
            "decision": "hybrid",
            "passed": True,
            "confidence": 0.5,
            "score": 0.5,
            "summary": "仲裁解析失败，返回默认值",
            "reasoning": "无法解析仲裁结果",
        }


class SimpleTiebreaker:
    """
    简化仲裁器 - MVP 用
    简单多数决策略
    """

    def __init__(self, instance_id: str = "simple_tiebreaker"):
        self.instance_id = instance_id

    async def arbitrate(
        self,
        task: Task,
        report_a: AgentResult,
        report_b: AgentResult,
        model: str = "",
    ) -> AgentResult:
        """简单仲裁：选择分数更高的"""
        chosen = report_a if report_a.score >= report_b.score else report_b

        return AgentResult(
            agent_id=self.instance_id,
            agent_role=AgentRole.L1_TIEBREAKER,
            task_id=task.id,
            content=f"仲裁选择: {'L1-A' if chosen == report_a else 'L1-B'} 的结果\n{chosen.content[:1000]}",
            summary=f"简单仲裁: 选择分数更高的一方 ({chosen.score:.2f})",
            confidence=max(report_a.confidence, report_b.confidence),
            score=(report_a.score + report_b.score) / 2,
            passed=chosen.passed,
            metadata={
                "arbitration_type": "simple_majority",
                "chosen_report": "a" if chosen == report_a else "b",
            },
        )
