"""
ziwei/config/settings.py
全局配置（从 YAML 或环境变量加载）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ModelConfig:
    provider: str        # "anthropic" | "openai_compat"
    model:    str
    api_key:  str
    base_url: str = ""


@dataclass
class RedisConfig:
    host:     str = "localhost"
    port:     int = 6379
    db:       int = 0
    password: Optional[str] = None


@dataclass
class PostgresConfig:
    dsn: str = "postgresql://ziwei:ziwei@localhost:5432/ziwei"


@dataclass
class NotifierConfig:
    dingtalk_webhook: str = ""
    slack_webhook:    str = ""
    wechat_key:       str = ""


@dataclass
class ZiWeiSettings:
    # ── 模型配置 ──
    l0_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="anthropic",
        model="claude-opus-4-6",
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
    ))

    l1_executor_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="openai_compat",
        model="MiniMax-Text-01",
        api_key=os.getenv("MINIMAX_API_KEY", ""),
        base_url="https://api.minimax.chat/v1",
    ))

    l1_validator_models: Dict[str, ModelConfig] = field(default_factory=lambda: {
        "code":      ModelConfig("openai_compat", "ep-xxx", os.getenv("DOUBAO_API_KEY",""), "https://ark.cn-beijing.volces.com/api/v3"),
        "doc":       ModelConfig("openai_compat", "glm-4-plus", os.getenv("GLM_API_KEY",""), "https://open.bigmodel.cn/api/paas/v4"),
        "data":      ModelConfig("openai_compat", "qwen-max", os.getenv("QWEN_API_KEY",""), "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "reasoning": ModelConfig("openai_compat", "ep-xxx", os.getenv("DOUBAO_API_KEY",""), "https://ark.cn-beijing.volces.com/api/v3"),
    })

    l1_tiebreaker_models: Dict[str, ModelConfig] = field(default_factory=lambda: {
        "code":      ModelConfig("openai_compat", "qwen-coder-plus", os.getenv("QWEN_API_KEY",""), "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "doc":       ModelConfig("openai_compat", "ep-xxx", os.getenv("DOUBAO_API_KEY",""), "https://ark.cn-beijing.volces.com/api/v3"),
        "data":      ModelConfig("openai_compat", "glm-4-plus", os.getenv("GLM_API_KEY",""), "https://open.bigmodel.cn/api/paas/v4"),
        "reasoning": ModelConfig("openai_compat", "qwen-max", os.getenv("QWEN_API_KEY",""), "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    })

    l2_worker_models: Dict[str, ModelConfig] = field(default_factory=lambda: {
        "doc":    ModelConfig("openai_compat", "glm-4v", os.getenv("GLM_API_KEY",""), "https://open.bigmodel.cn/api/paas/v4"),
        "code":   ModelConfig("openai_compat", "deepseek-coder", os.getenv("DEEPSEEK_API_KEY",""), "https://api.deepseek.com/v1"),
        "search": ModelConfig("openai_compat", "glm-4", os.getenv("GLM_API_KEY",""), "https://open.bigmodel.cn/api/paas/v4"),
        "data":   ModelConfig("openai_compat", "qwen-max", os.getenv("QWEN_API_KEY",""), "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    })

    # ── 基础设施 ──
    redis:    RedisConfig    = field(default_factory=RedisConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)

    # ── 运行参数 ──
    manifest_secret:     str   = os.getenv("MANIFEST_SECRET", "change-me-in-production")
    context_limit:       int   = 80_000
    compress_at:         float = 0.70
    audit_threshold:     float = 0.80
    confidence_threshold: float = 0.80
    max_audit_retries:   int   = 2
    human_timeout_s:     int   = 1800


# 全局单例
settings = ZiWeiSettings()
