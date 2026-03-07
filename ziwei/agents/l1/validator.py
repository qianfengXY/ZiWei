"""
ziwei/agents/l1/validator.py
L1-B Validator - 简化版验证器
独立验证执行结果，输出验证报告和置信分
"""

import logging
from typing import Any, Dict, Optional

from ...core.base_agent import BaseAgentAdapter
from ...core.enums import AgentRole
from ...core.models import AgentResult, Task


logger = logging.getLogger("ziwei.l1.validator")


class L1Validator:
    """
    L1-B Validator
    独立验证，不重新执行，只做核查
    MVP 阶段：简单文本比对 + 模型辅助验证
    """

    def __init__(
        self,
        instance_id: str,
        adapter: BaseAgentAdapter,
    ):
        self.instance_id = instance_id
        self.adapter = adapter

    async def validate(
        self,
        task: Task,
        exec_result: AgentResult,
        model: str = "",
    ) -> AgentResult:
        """
        验证任务执行结果

        Args:
            task: 原始任务
            exec_result: 执行者的结果
            model: 使用的模型（可选）

        Returns:
            AgentResult: 验证报告
        """
        logger.info(f"[L1-B:{self.instance_id}] 开始验证任务 {task.id}")

        prompt = self._build_validation_prompt(task, exec_result)

        resp = await self.adapter.invoke(prompt, remember=False)

        validation_result = self._parse_validation_response(resp.content)

        result = AgentResult(
            agent_id=self.instance_id,
            agent_role=AgentRole.L1_VALIDATOR,
            task_id=task.id,
            content=resp.content,
            summary=validation_result.get("summary", ""),
            confidence=validation_result.get("confidence", 0.5),
            score=validation_result.get("score", 0.5),
            passed=validation_result.get("passed", False),
            metadata={
                "validation_type": "simple_text_comparison",
                "model_used": model or self.adapter.model_name,
                "issues": validation_result.get("issues", []),
                "suggestions": validation_result.get("suggestions", []),
            },
        )

        logger.info(
            f"[L1-B:{self.instance_id}] 验证完成: "
            f"passed={result.passed}, confidence={result.confidence:.2f}, score={result.score:.2f}"
        )

        return result

    def _build_validation_prompt(self, task: Task, exec_result: AgentResult) -> str:
        """构建验证提示"""
        return f"""
你是任务验证专家。请对以下执行结果进行独立验证。

## 原始任务
{task.instruction}

## 执行结果
{exec_result.content[:3000]}

请验证以下方面：
1. 执行结果是否完成了任务要求
2. 结果是否准确、完整
3. 格式是否符合预期
4. 是否有明显的错误或遗漏

请以 JSON 格式输出验证结果：
{{
    "passed": true/false,
    "confidence": 0.0-1.0,
    "score": 0.0-1.0,
    "summary": "验证摘要",
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"]
}}

只输出 JSON，不要其他内容。
"""

    def _parse_validation_response(self, content: str) -> Dict[str, Any]:
        """解析验证响应"""
        import json

        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                return json.loads(json_str)
        except Exception as e:
            logger.warning(f"解析验证响应失败: {e}")

        return {
            "passed": False,
            "confidence": 0.5,
            "score": 0.5,
            "summary": "验证解析失败",
            "issues": ["无法解析验证结果"],
            "suggestions": ["请重新验证"],
        }


class SimpleValidator:
    """
    简化验证器 - MVP 用
    不调用模型，直接做简单比对
    """

    def __init__(self, instance_id: str = "simple_validator"):
        self.instance_id = instance_id

    async def validate(
        self,
        task: Task,
        exec_result: AgentResult,
        model: str = "",
    ) -> AgentResult:
        """简单验证：检查是否有内容"""
        has_content = bool(exec_result.content and len(exec_result.content.strip()) > 0)

        return AgentResult(
            agent_id=self.instance_id,
            agent_role=AgentRole.L1_VALIDATOR,
            task_id=task.id,
            content=f"简单验证通过: {'有内容输出' if has_content else '无内容输出'}",
            summary="简单文本验证",
            confidence=0.6 if has_content else 0.1,
            score=0.7 if has_content else 0.0,
            passed=has_content,
            metadata={"validation_type": "simple_check"},
        )
