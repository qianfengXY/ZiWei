"""
ziwei/core/adapters.py
各模型适配器实现
- AnthropicAdapter   → Claude (Opus)
- OpenAIAdapter     → OpenAI-compatible (MiniMax / GLM / Qwen / Deepseek)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .base_agent import AgentMessage, AgentResponse, BaseAgentAdapter
from .enums import AgentRole


# Anthropic (Claude / Opus) —— L0 专用

class AnthropicAdapter(BaseAgentAdapter):
    """
    Claude Opus 适配器，用于 L0 Brain。
    支持自定义 base_url（用于兼容 API 如 cursor.scihub.edu.kg）
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-6",
        base_url: str = "https://api.anthropic.com/v1/messages",
        **kwargs
    ):
        super().__init__(
            model_name=model,
            api_key=api_key,
            base_url=base_url or "https://api.anthropic.com/v1/messages",
            role=AgentRole.L0_BRAIN,
            **kwargs,
        )

    async def _call(
        self,
        messages: List[AgentMessage],
        system:   Optional[str] = None,
        tools:    Optional[List[Dict]] = None,
    ) -> AgentResponse:
        # 分离 system 消息
        sys_msgs = [m for m in messages if m.role == "system"]
        other    = [m for m in messages if m.role != "system"]
        system_prompt = system or (sys_msgs[-1].content if sys_msgs else None)

        body: Dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": 8192,
            "messages": [{"role": m.role, "content": m.content} for m in other],
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        text = "".join(
            b["text"] for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return AgentResponse(
            content=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            raw=data,
        )


# OpenAI-Compatible —— L1 / L2 通用

class OpenAICompatAdapter(BaseAgentAdapter):
    """
    OpenAI 兼容格式适配器。
    L1 Manager（MiniMax）和 L2 Workers（GLM / Qwen / Deepseek）共用。
    """

    def __init__(
        self,
        api_key:   str,
        model:     str,
        base_url:  str,
        role:      AgentRole = AgentRole.L2_WORKER,
        **kwargs,
    ):
        super().__init__(
            model_name=model,
            api_key=api_key,
            base_url=base_url,
            role=role,
            **kwargs,
        )

    async def _call(
        self,
        messages: List[AgentMessage],
        system:   Optional[str] = None,
        tools:    Optional[List[Dict]] = None,
    ) -> AgentResponse:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs += [{"role": m.role, "content": m.content} for m in messages]

        body: Dict[str, Any] = {
            "model": self.model_name,
            "messages": msgs,
            "max_tokens": 8192,
        }
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"] or ""
        usage   = data.get("usage", {})
        return AgentResponse(
            content=content,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )


# 工厂函数

def create_adapter(cfg: Dict[str, Any], role: AgentRole) -> BaseAgentAdapter:
    """
    根据配置字典创建对应 Adapter。
    """
    provider = cfg.get("provider", "openai_compat")
    
    if provider == "anthropic":
        return AnthropicAdapter(
            api_key=cfg["api_key"],
            model=cfg.get("model", "claude-opus-4-6"),
            base_url=cfg.get("base_url", ""),
        )
    
    if provider == "openai_compat":
        return OpenAICompatAdapter(
            api_key=cfg["api_key"],
            model=cfg["model"],
            base_url=cfg["base_url"],
            role=role,
        )
    
    raise ValueError(f"未知 provider: {provider}")
