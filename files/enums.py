"""
ziwei/core/enums.py
全局枚举定义
"""

from enum import Enum, auto


class AgentRole(str, Enum):
    L0_BRAIN = "l0_brain"
    L1_EXECUTOR = "l1_executor"
    L1_VALIDATOR = "l1_validator"
    L1_TIEBREAKER = "l1_tiebreaker"
    L2_WORKER = "l2_worker"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_GRANT = "waiting_grant"  # 等待 L1 授权
    WAITING_L0 = "waiting_l0"  # 等待 L0 决策
    WAITING_HUMAN = "waiting_human"  # 等待人工响应
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"  # 呆死
    ROLLED_BACK = "rolled_back"


class TaskComplexity(str, Enum):
    SIMPLE = "simple"  # → L2 直达
    MEDIUM = "medium"  # → L1 调度
    COMPLEX = "complex"  # → L0 规划


class RiskLevel(str, Enum):
    ALLOWED = "allowed"  # Manifest 范围内，直接执行
    NEED_L1 = "need_l1"  # 能力不足，找 L1 授权
    HIGH_RISK = "high_risk"  # 高危操作，找 L0 决策
    FATAL = "fatal"  # 致命卡点，推送人工


class VerifyVerdict(str, Enum):
    AGREE = "agree"  # 双 L1 一致
    CONFLICT = "conflict"  # 不一致，召唤 L1-C


class AuditVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    RETRY = "retry"


class StaleVerdict(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"  # 预警，不中断
    STALE = "stale"  # 确认呆死
    EXEMPT = "exempt"  # 豁免（wait_human 等）


class SpecialMode(str, Enum):
    DOWNLOAD = "download"  # 下载任务：按字节速率判断
    LONG_JOB = "long_job"  # 长计算：按里程碑判断
    WAIT_HUMAN = "wait_human"  # 等待人工：完全豁免


class HumanDecisionMode(str, Enum):
    BLOCK = "block"  # 强阻塞，必须人工响应
    NOTIFY = "notify"  # 自动授权 + 仅通知


class ArtifactType(str, Enum):
    CODE = "code"
    DOC = "doc"
    DB = "db"
    DATA = "data"


class ProgressMetric(str, Enum):
    STEPS = "steps"
    BYTES = "bytes"
    PERCENTAGE = "percentage"


class MessageType(str, Enum):
    TASK_DISPATCH = "task.dispatch"  # L0→L1: 下发任务
    TASK_SUBMIT = "task.submit"  # L1→L0: 提交任务结果
    TASK_RESULT = "task.result"  # L1↔L2: 任务执行结果
    TASK_ESCALATE = "task.escalate"  # L1→L0: 升级任务
    TASK_CANCEL = "task.cancel"  # L0→L1: 取消任务

    HEARTBEAT = "heartbeat"  # L2→L1 / L1→L0: 心跳
    HEALTH_CHECK = "health.check"  # 系统健康检查
    HEALTH_REPORT = "health.report"  # 健康报告

    AUTH_REQUEST = "auth.request"  # L2→L1: 请求授权
    AUTH_GRANT = "auth.grant"  # L1→L2: 授予权限
    AUTH_DENY = "auth.deny"  # L1→L2: 拒绝授权
    AUTH_L0_DECISION = "auth.l0_decision"  # L0→L2: L0 决策结果

    CONTROL_START = "control.start"  # 启动组件
    CONTROL_STOP = "control.stop"  # 停止组件
    CONTROL_RESTART = "control.restart"  # 重启组件

    AUDIT_REQUEST = "audit.request"  # L0→L1: 请求审核
    AUDIT_RESULT = "audit.result"  # L0→L1: 审核结果

    NOTIFY_WARNING = "notify.warning"  # 警告通知
    NOTIFY_CRITICAL = "notify.critical"  # 严重告警
    NOTIFY_HUMAN = "notify.human"  # 人工介入请求


class MessagePriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"
