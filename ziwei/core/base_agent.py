"""
ziwei/core/base_agent.py
所有模型的统一适配器基类 —— 屏蔽各模型 API 差异
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .enums import AgentRole


@dataclass
class AgentMessage:
    role:    str    # "system" | "user" | "assistant"
    content: str


@dataclass
class AgentResponse:
    content:      str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms:   int = 0
    raw:          Any = None


class BaseAgentAdapter(ABC):
    """
    统一 Agent 调用接口。
    每种模型实现一个子类，L0/L1/L2 通过此接口调用，不感知底层差异。
    """

    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str = "",
        role: AgentRole = AgentRole.L2_WORKER,
        max_retries: int = 3,
        timeout_s: int = 120,
    ):
        self.model_name  = model_name
        self.api_key     = api_key
        self.base_url    = base_url
        self.role        = role
        self.max_retries = max_retries
        self.timeout_s   = timeout_s

        self._history: List[AgentMessage] = []
        self._tokens_used = 0

    # ── 子类必须实现 ─────────────────────────────────

    @abstractmethod
    async def _call(
        self,
        messages: List[AgentMessage],
        system:   Optional[str] = None,
        tools:    Optional[List[Dict]] = None,
    ) -> AgentResponse:
        """向模型发送请求，返回标准化响应"""

    # ── 公共接口 ─────────────────────────────────────

    async def invoke(
        self,
        user_message: str,
        system:       Optional[str] = None,
        tools:        Optional[List[Dict]] = None,
        remember:     bool = True,          # 是否写入历史
    ) -> AgentResponse:
        """带重试、超时、历史管理的标准调用"""
        msg = AgentMessage(role="user", content=user_message)
        messages = self._history + [msg]

        for attempt in range(self.max_retries):
            try:
                t0 = time.monotonic()
                resp = await asyncio.wait_for(
                    self._call(messages, system=system, tools=tools),
                    timeout=self.timeout_s,
                )
                resp.latency_ms = int((time.monotonic() - t0) * 1000)
                self._tokens_used += resp.input_tokens + resp.output_tokens

                if remember:
                    self._history.append(msg)
                    self._history.append(
                        AgentMessage(role="assistant", content=resp.content)
                    )
                return resp

            except asyncio.TimeoutError:
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError(f"[{self.model_name}] 重试 {self.max_retries} 次均失败")

    def clear_history(self) -> None:
        """清空会话历史（Worker 任务完成后调用）"""
        self._history.clear()

    def compress_history(self, summary: str, keep_last: int = 5) -> None:
        """
        滚动压缩：用摘要替换历史，保留最近 N 轮。
        Manager 上下文达 70% 阈值时调用。
        """
        recent = self._history[-keep_last * 2:]  # 每轮 user+assistant 两条
        self._history = [
            AgentMessage(role="system", content=f"[历史摘要]\n{summary}"),
            *recent,
        ]

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def history_len(self) -> int:
        return len(self._history)
