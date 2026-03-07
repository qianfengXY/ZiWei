"""
ziwei/core/enums.py
全局枚举定义
"""
from enum import Enum, auto


class AgentRole(str, Enum):
    L0_BRAIN      = "l0_brain"
    L1_EXECUTOR   = "l1_executor"
    L1_VALIDATOR  = "l1_validator"
    L1_TIEBREAKER = "l1_tiebreaker"
    L2_WORKER     = "l2_worker"


class TaskStatus(str, Enum):
    PENDING        = "pending"
    RUNNING        = "running"
    WAITING_GRANT  = "waiting_grant"    # 等待 L1 授权
    WAITING_L0     = "waiting_l0"       # 等待 L0 决策
    WAITING_HUMAN  = "waiting_human"    # 等待人工响应
    COMPLETED      = "completed"
    FAILED         = "failed"
    STALE          = "stale"            # 呆死
    ROLLED_BACK    = "rolled_back"


class TaskComplexity(str, Enum):
    SIMPLE  = "simple"   # → L2 直达
    MEDIUM  = "medium"   # → L1 调度
    COMPLEX = "complex"  # → L0 规划


class RiskLevel(str, Enum):
    ALLOWED   = "allowed"    # Manifest 范围内，直接执行
    NEED_L1   = "need_l1"    # 能力不足，找 L1 授权
    HIGH_RISK = "high_risk"  # 高危操作，找 L0 决策
    FATAL     = "fatal"      # 致命卡点，推送人工


class VerifyVerdict(str, Enum):
    AGREE    = "agree"    # 双 L1 一致
    CONFLICT = "conflict" # 不一致，召唤 L1-C


class AuditVerdict(str, Enum):
    PASS   = "pass"
    FAIL   = "fail"
    RETRY  = "retry"


class StaleVerdict(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"  # 预警，不中断
    STALE   = "stale"    # 确认呆死
    EXEMPT  = "exempt"   # 豁免（wait_human 等）


class SpecialMode(str, Enum):
    DOWNLOAD   = "download"    # 下载任务：按字节速率判断
    LONG_JOB   = "long_job"    # 长计算：按里程碑判断
    WAIT_HUMAN = "wait_human"  # 等待人工：完全豁免


class HumanDecisionMode(str, Enum):
    BLOCK     = "block"      # 强阻塞，必须人工响应
    NOTIFY    = "notify"     # 自动授权 + 仅通知


class ArtifactType(str, Enum):
    CODE = "code"
    DOC  = "doc"
    DB   = "db"
    DATA = "data"


class ProgressMetric(str, Enum):
    STEPS      = "steps"
    BYTES      = "bytes"
    PERCENTAGE = "percentage"
