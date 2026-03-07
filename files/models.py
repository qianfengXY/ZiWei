"""
ziwei/core/models.py
系统核心数据结构 —— 整个平台的地基
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from .enums import (
    AgentRole, ArtifactType, HumanDecisionMode, ProgressMetric,
    RiskLevel, SpecialMode, TaskComplexity, TaskStatus,
)


# ─────────────────────────────────────────
# 权限结构
# ─────────────────────────────────────────

@dataclass
class FilePerms:
    read:    bool = True
    write:   bool = False
    append:  bool = False
    delete:  bool = False    # 高危 → 必须 L0 决策
    execute: bool = False    # 高危 → 必须 L0 决策

    def permits(self, op: str) -> bool:
        return getattr(self, op, False)


@dataclass
class NetPerms:
    intranet_get:  bool = True
    intranet_post: bool = False
    external_get:  bool = False   # 需 L1 授权
    external_post: bool = False   # 需 L1 授权
    paid_api:      bool = False   # 高危 → 必须 L0 决策


@dataclass
class DBPerms:
    select: bool = True
    insert: bool = False   # 需 L1 授权
    update: bool = False   # 需 L1 授权
    delete: bool = False   # 高危 → 必须 L0 决策
    ddl:    bool = False   # 高危 → 必须 L0 决策


@dataclass
class CodePerms:
    sandbox:    bool = True    # 沙箱隔离执行
    read_host:  bool = False   # 需 L1 授权
    modify_env: bool = False   # 高危 → 必须 L0 决策


@dataclass
class CapabilitySet:
    file:   FilePerms = field(default_factory=FilePerms)
    net:    NetPerms  = field(default_factory=NetPerms)
    db:     DBPerms   = field(default_factory=DBPerms)
    code:   CodePerms = field(default_factory=CodePerms)
    skills: List[str] = field(default_factory=list)  # 白名单 Skill ID

    def permits(self, action: "Action") -> bool:
        """检查某操作是否在当前 Capability 范围内"""
        t = action.type
        if t.startswith("file."):
            return self.file.permits(t.split(".")[1])
        if t.startswith("net."):
            return self.net.permits(t.split(".")[1])  # type: ignore
        if t.startswith("db."):
            return self.db.permits(t.split(".")[1])  # type: ignore
        if t.startswith("code."):
            return self.code.permits(t.split(".")[1])  # type: ignore
        if t.startswith("skill."):
            return action.params.get("skill_id") in self.skills
        return False


@dataclass
class WorkspaceConfig:
    root_path:     str       = "/workspace/tasks/{task_id}"
    allowed_read:  List[str] = field(default_factory=lambda: ["/workspace/shared/readonly"])
    allowed_write: List[str] = field(default_factory=list)
    forbidden:     List[str] = field(default_factory=lambda: ["/etc", "/root", "/sys", "/proc"])

    def resolve(self, task_id: str) -> "WorkspaceConfig":
        self.root_path = self.root_path.format(task_id=task_id)
        return self


@dataclass
class ResourceLimits:
    max_tokens:       int = 32_000
    max_exec_time_s:  int = 120
    max_file_size_mb: int = 50
    max_api_calls:    int = 10


@dataclass
class EscalationConfig:
    l1_endpoint:      str       = "http://l1-manager/grant"
    l0_endpoint:      str       = "http://l0-brain/highrisk"
    human_channels:   List[str] = field(default_factory=lambda: ["dingtalk", "slack"])
    block_on_human:   bool      = True
    human_timeout_s:  int       = 1800   # 30 分钟


# ─────────────────────────────────────────
# TaskManifest —— L1 下发给 L2 的权限令牌
# ─────────────────────────────────────────

@dataclass
class TaskManifest:
    """
    L1 → L2 的签名权限令牌。
    Worker 只能在此范围内行动，无法自行扩权。
    """
    task_id:      str
    parent_id:    str
    worker_model: str
    issued_by:    str           # L1 实例 ID
    issued_at:    datetime      = field(default_factory=datetime.utcnow)
    expires_at:   datetime      = field(default_factory=lambda: datetime.utcnow() + timedelta(hours=2))

    workspace:    WorkspaceConfig  = field(default_factory=WorkspaceConfig)
    capabilities: CapabilitySet    = field(default_factory=CapabilitySet)
    limits:       ResourceLimits   = field(default_factory=ResourceLimits)
    escalation:   EscalationConfig = field(default_factory=EscalationConfig)

    signature:    str = ""   # HMAC-SHA256，由 L1 签发后填入

    def sign(self, secret: str) -> "TaskManifest":
        payload = json.dumps({
            "task_id": self.task_id,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }, sort_keys=True)
        self.signature = hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return self

    def verify(self, secret: str) -> bool:
        expected = self.sign(secret).signature
        return hmac.compare_digest(self.signature, expected)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at


# ─────────────────────────────────────────
# Task —— 平台内流转的任务单元
# ─────────────────────────────────────────

@dataclass
class Task:
    id:              str             = field(default_factory=lambda: str(uuid.uuid4()))
    instruction:     str             = ""
    complexity:      TaskComplexity  = TaskComplexity.MEDIUM
    assigned_to:     AgentRole       = AgentRole.L1_EXECUTOR
    parent_task_id:  Optional[str]   = None
    context:         Dict[str, Any]  = field(default_factory=dict)
    status:          TaskStatus      = TaskStatus.PENDING
    result:          Optional[str]   = None
    error:           Optional[str]   = None
    escalation_count: int            = 0
    created_at:      datetime        = field(default_factory=datetime.utcnow)
    updated_at:      datetime        = field(default_factory=datetime.utcnow)

    # 执行链追踪
    executor_result:  Optional["AgentResult"] = None
    validator_report: Optional["AgentResult"] = None
    tiebreaker_result: Optional["AgentResult"] = None
    audit_result:     Optional["AuditResult"] = None


# ─────────────────────────────────────────
# Action —— Worker 的单步操作
# ─────────────────────────────────────────

@dataclass
class Action:
    """
    Worker 每一步对外部资源的操作请求。
    执行前必须通过 classify_risk() → do() 权限门。
    """
    type:    str              # e.g. "file.write", "db.delete", "skill.use"
    params:  Dict[str, Any]   = field(default_factory=dict)
    reason:  str              = ""  # 操作意图说明（上报时用）
    task_id: str              = ""

    def exceeds_max_permission(self, manifest: TaskManifest) -> bool:
        """检测是否超出 Manifest 声明的最大权限上界"""
        # 路径越界检测
        if "path" in self.params:
            p = self.params["path"]
            if any(p.startswith(f) for f in manifest.workspace.forbidden):
                return True
            allowed = [manifest.workspace.root_path] + manifest.workspace.allowed_read
            if self.type.startswith("file.write"):
                allowed += manifest.workspace.allowed_write
            if not any(p.startswith(a) for a in allowed):
                return True
        return False


@dataclass
class ActionResult:
    success:    bool
    output:     Any            = None
    error:      Optional[str]  = None
    risk_level: RiskLevel      = RiskLevel.ALLOWED
    token_id:   Optional[str]  = None   # 授权令牌 ID

    @classmethod
    def denied(cls, reason: str) -> "ActionResult":
        return cls(success=False, error=f"DENIED: {reason}")

    @classmethod
    def pending_human(cls) -> "ActionResult":
        return cls(success=False, error="PENDING_HUMAN: 等待人工响应")


# ─────────────────────────────────────────
# AgentResult —— Agent 的执行/验证报告
# ─────────────────────────────────────────

@dataclass
class AgentResult:
    agent_id:   str
    agent_role: AgentRole
    task_id:    str
    content:    str
    summary:    str             = ""
    confidence: float           = 0.0   # 0.0 ~ 1.0
    score:      float           = 0.0
    passed:     bool            = False
    metadata:   Dict[str, Any]  = field(default_factory=dict)
    created_at: datetime        = field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────
# AuditResult —— L0 最终审核结果
# ─────────────────────────────────────────

@dataclass
class AuditResult:
    task_id:     str
    logic_score:     float = 0.0   # 推理链完整性
    factual_score:   float = 0.0   # 事实准确性
    risk_score:      float = 0.0   # 风险标注（越低越危险）
    alignment_score: float = 0.0   # 用户意图对齐
    passed:          bool  = False
    correction:      Optional[str] = None  # 不通过时的修正指令
    version_id:      Optional[str] = None

    @property
    def overall(self) -> float:
        return (self.logic_score + self.factual_score +
                self.risk_score + self.alignment_score) / 4


# ─────────────────────────────────────────
# ArtifactVersion —— 版本控制单元
# ─────────────────────────────────────────

@dataclass
class ArtifactVersion:
    version_id:    str             = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_id:       str             = ""
    agent_id:      str             = ""
    artifact_type: ArtifactType    = ArtifactType.CODE
    content:       str             = ""
    diff_from:     Optional[str]   = None    # 父版本 version_id
    score:         float           = 0.0
    status:        Literal["committed", "rolled_back", "pending"] = "pending"
    snapshot:      bytes           = b""     # 压缩快照
    timestamp:     datetime        = field(default_factory=datetime.utcnow)
    metadata:      Dict[str, Any]  = field(default_factory=dict)


# ─────────────────────────────────────────
# TaskTimeDeclaration —— 长耗时任务声明
# ─────────────────────────────────────────

@dataclass
class Milestone:
    name:         str
    estimated_s:  int
    completed:    bool      = False
    completed_at: Optional[datetime] = None


@dataclass
class TaskTimeDeclaration:
    """
    Worker 启动时必须提交。
    声明了 special_mode 的任务不会被常规超时规则误杀。
    """
    task_id:         str
    task_type:       str
    estimated_s:     int
    special_mode:    Optional[SpecialMode]    = None
    milestones:      List[Milestone]          = field(default_factory=list)
    progress_metric: ProgressMetric           = ProgressMetric.STEPS
    declared_at:     datetime                 = field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────
# WorkerHeartbeat —— L2 向 L1 的心跳包
# ─────────────────────────────────────────

@dataclass
class WorkerHeartbeat:
    task_id:          str
    worker_id:        str
    step_name:        str
    tokens_used:      int
    tokens_budget:    int
    elapsed_s:        int
    estimated_total_s: int
    status:           Literal["running", "waiting", "blocked"] = "running"
    special_mode:     Optional[SpecialMode] = None
    bytes_delta:      int = 0       # 下载任务用
    last_milestone:   Optional[str] = None
    timestamp:        datetime = field(default_factory=datetime.utcnow)

    @property
    def token_ratio(self) -> float:
        return self.tokens_used / max(self.tokens_budget, 1)

    @property
    def progress_ratio(self) -> float:
        return self.elapsed_s / max(self.estimated_total_s, 1)


# ─────────────────────────────────────────
# FinalResult —— 最终输出给用户
# ─────────────────────────────────────────

@dataclass
class FinalResult:
    task_id:    str
    content:    str
    confidence: float
    version_id: str
    trace:      Dict[str, Any] = field(default_factory=dict)
    created_at: datetime       = field(default_factory=datetime.utcnow)

    # 验证路径摘要（用户可查）
    verified_by:  List[str] = field(default_factory=list)
    audit_scores: Dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────
# AutoGrantRule —— 人工预配置自动授权规则
# ─────────────────────────────────────────

@dataclass
class AutoGrantRule:
    rule_id:         str
    name:            str
    action_type:     str
    conditions:      Dict[str, Any]
    notify_only:     bool = True         # True=只通知 False=必须响应
    notify_channels: List[str] = field(default_factory=lambda: ["dingtalk"])
    created_by:      str = ""
    expires_at:      Optional[datetime] = None
    enabled:         bool = True

    def matches(self, action: Action) -> bool:
        if action.type != self.action_type:
            return False
        for k, v in self.conditions.items():
            val = action.params.get(k, "")
            if isinstance(v, str) and val.startswith(v):
                continue
            if action.params.get(k) != v:
                return False
        return True
