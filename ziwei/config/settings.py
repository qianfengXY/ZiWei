"""
ziwei/config/settings.py
全局配置（从 .env 文件加载）
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional

from dotenv import load_dotenv

# 加载 .env 文件
_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(_env_path)


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
    # L0 模型配置 (Opus)
    l0_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="anthropic",
        model=os.getenv("L0_MODEL", "claude-opus-4-6"),
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        base_url=os.getenv("ANTHROPIC_BASE_URL", ""),
    ))

    # L1 模型配置 (MiniMax M2.5)
    l1_executor_model: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="anthropic",
        model=os.getenv("L1_MODEL", "MiniMax-M2.5"),
        api_key=os.getenv("MINIMAX_API_KEY", ""),
        base_url=os.getenv("MINIMAX_BASE_URL", ""),
    ))

    l1_validator_models: Dict[str, ModelConfig] = field(default_factory=lambda: {
        "code":      ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "doc":       ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "data":      ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "reasoning": ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
    })

    l1_tiebreaker_models: Dict[str, ModelConfig] = field(default_factory=lambda: {
        "code":      ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "doc":       ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "data":      ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "reasoning": ModelConfig("openai_compat", "glm-4-7", os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
    })

    # L2 模型配置 (GLM)
    l2_worker_models: Dict[str, ModelConfig] = field(default_factory=lambda: {
        "doc":    ModelConfig("openai_compat", os.getenv("GLM_MODEL", "glm-4-7"), os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "code":   ModelConfig("openai_compat", os.getenv("GLM_MODEL", "glm-4-7"), os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "search": ModelConfig("openai_compat", os.getenv("GLM_MODEL", "glm-4-7"), os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
        "data":   ModelConfig("openai_compat", os.getenv("GLM_MODEL", "glm-4-7"), os.getenv("GLM_API_KEY",""), os.getenv("GLM_BASE_URL","")),
    })

    # 基础设施
    redis:    RedisConfig    = field(default_factory=RedisConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)

    # 运行参数
    manifest_secret:     str   = os.getenv("MANIFEST_SECRET", "change-me-in-production")
    context_limit:       int   = 80_000
    compress_at:         float = 0.70
    audit_threshold:     float = 0.80
    confidence_threshold: float = 0.80
    max_audit_retries:   int   = 2
    human_timeout_s:     int   = 1800


settings = ZiWeiSettings()
