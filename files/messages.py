"""
ziwei/core/messages.py
L0 / L1 / L2 之间的消息协议定义

设计原则：
1. 每个消息有唯一 ID，便于追踪
2. 消息分为 Header + Body 结构
3. 支持请求-响应模式（带 correlation_id）
4. 所有模型都能解析和识别消息
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from .enums import (
    AgentRole,
    MessagePriority,
    MessageType,
    RiskLevel,
    SpecialMode,
    TaskComplexity,
    TaskStatus,
)
from .models import Action, AgentResult, Task, TaskManifest


# ─────────────────────────────────────────
# 通用消息头
# ─────────────────────────────────────────


@dataclass
class MessageHeader:
    """所有消息的通用头部"""

    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_type: MessageType = MessageType.TASK_DISPATCH
    priority: MessagePriority = MessagePriority.NORMAL

    # 发送者和接收者
    sender: AgentRole = AgentRole.L2_WORKER
    sender_id: str = ""
    receiver: AgentRole = AgentRole.L1_EXECUTOR
    receiver_id: str = ""

    # 消息关联（请求-响应）
    correlation_id: Optional[str] = None  # 关联到原始请求
    reply_to: Optional[str] = None  # 回复队列

    # 时间戳
    timestamp: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at


# ─────────────────────────────────────────
# 通用消息包装
# ─────────────────────────────────────────


@dataclass
class Message:
    """
    通用消息包装器
    所有 L0/L1/L2 之间的消息都通过此格式传输
    """

    header: MessageHeader
    body: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        message_type: MessageType,
        sender: AgentRole,
        receiver: AgentRole,
        body: Dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL,
        correlation_id: Optional[str] = None,
        sender_id: str = "",
        receiver_id: str = "",
    ) -> "Message":
        return cls(
            header=MessageHeader(
                message_type=message_type,
                sender=sender,
                receiver=receiver,
                sender_id=sender_id,
                receiver_id=receiver_id,
                priority=priority,
                correlation_id=correlation_id,
            ),
            body=body,
        )

    def to_json(self) -> str:
        """序列化为 JSON"""
        return json.dumps(
            {
                "header": {
                    "message_id": self.header.message_id,
                    "message_type": self.header.message_type.value,
                    "priority": self.header.priority.value,
                    "sender": self.header.sender.value,
                    "sender_id": self.header.sender_id,
                    "receiver": self.header.receiver.value,
                    "receiver_id": self.header.receiver_id,
                    "correlation_id": self.header.correlation_id,
                    "reply_to": self.header.reply_to,
                    "timestamp": self.header.timestamp.isoformat(),
                    "expires_at": self.header.expires_at.isoformat()
                    if self.header.expires_at
                    else None,
                    "metadata": self.header.metadata,
                },
                "body": self.body,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """从 JSON 反序列化"""
        data = json.loads(json_str)
        h = data["header"]
        return cls(
            header=MessageHeader(
                message_id=h["message_id"],
                message_type=MessageType(h["message_type"]),
                priority=MessagePriority(h["priority"]),
                sender=AgentRole(h["sender"]),
                sender_id=h["sender_id"],
                receiver=AgentRole(h["receiver"]),
                receiver_id=h["receiver_id"],
                correlation_id=h.get("correlation_id"),
                reply_to=h.get("reply_to"),
                timestamp=datetime.fromisoformat(h["timestamp"]),
                expires_at=datetime.fromisoformat(h["expires_at"])
                if h.get("expires_at")
                else None,
                metadata=h.get("metadata", {}),
            ),
            body=data["body"],
        )


# ─────────────────────────────────────────
# 任务消息
# ─────────────────────────────────────────


@dataclass
class TaskDispatch:
    """
    L0 → L1: 下发任务
    包含任务信息和执行计划
    """

    task: Task

    # L0 指定的模型组合
    executor_model: str = ""  # 执行者模型
    validator_model: str = ""  # 验证者模型
    tiebreaker_model: str = ""  # 仲裁者模型

    # 任务分类
    task_type: str = "general"  # code/doc/data/reasoning

    # 优先级
    priority: int = 5  # 1-10，10 最高

    # 截止时间
    deadline: Optional[datetime] = None

    # 上下文摘要（用于传递关键上下文）
    context_summary: str = ""

    # 重试策略
    max_retries: int = 2
    retry_delay_s: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": {
                "id": self.task.id,
                "instruction": self.task.instruction,
                "complexity": self.task.complexity.value,
                "parent_task_id": self.task.parent_task_id,
                "context": self.task.context,
                "status": self.task.status.value,
            },
            "executor_model": self.executor_model,
            "validator_model": self.validator_model,
            "tiebreaker_model": self.tiebreaker_model,
            "task_type": self.task_type,
            "priority": self.priority,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "context_summary": self.context_summary,
            "max_retries": self.max_retries,
            "retry_delay_s": self.retry_delay_s,
        }


@dataclass
class TaskSubmit:
    """
    L1 → L0: 提交任务结果
    包含执行结果、验证报告
    """

    task_id: str

    # 执行结果
    executor_result: AgentResult

    # 验证结果（可选）
    validator_result: Optional[AgentResult] = None

    # 仲裁结果（如果有冲突）
    tiebreaker_result: Optional[AgentResult] = None

    # 任务状态
    status: TaskStatus = TaskStatus.COMPLETED

    # 执行摘要
    summary: str = ""

    # 失败原因（如果有）
    error: Optional[str] = None

    # 版本信息
    version_ids: List[str] = field(default_factory=list)

    # 执行时间
    elapsed_s: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "executor_result": {
                "agent_id": self.executor_result.agent_id,
                "agent_role": self.executor_result.agent_role.value,
                "content": self.executor_result.content,
                "summary": self.executor_result.summary,
                "confidence": self.executor_result.confidence,
                "score": self.executor_result.score,
                "passed": self.executor_result.passed,
            },
            "validator_result": {
                "agent_id": r.agent_id,
                "agent_role": r.agent_role.value,
                "content": r.content,
                "confidence": r.confidence,
                "score": r.score,
            }
            if self.validator_result
            else None,
            "status": self.status.value,
            "summary": self.summary,
            "error": self.error,
            "version_ids": self.version_ids,
            "elapsed_s": self.elapsed_s,
        }


@dataclass
class TaskResult:
    """
    L1 ↔ L2: 任务执行结果
    Worker 返回执行结果
    """

    task_id: str
    worker_id: str

    # 执行结果
    result: AgentResult

    # Manifest 信息（用于验证权限）
    manifest_id: str = ""

    # 是否需要升级
    need_escalate: bool = False
    escalate_reason: str = ""

    # 执行步骤统计
    steps_count: int = 0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "result": {
                "agent_id": self.result.agent_id,
                "content": self.result.content,
                "summary": self.result.summary,
                "confidence": self.result.confidence,
                "score": self.result.score,
                "passed": self.result.passed,
                "metadata": self.result.metadata,
            },
            "manifest_id": self.manifest_id,
            "need_escalate": self.need_escalate,
            "escalate_reason": self.escalate_reason,
            "steps_count": self.steps_count,
            "total_tokens": self.total_tokens,
        }


@dataclass
class TaskEscalate:
    """
    L1 → L0: 升级任务
    任务无法处理，需要 L0 介入
    """

    task_id: str
    worker_id: str

    # 升级原因
    reason: str
    escalate_type: Literal["quality_low", "conflict", "high_risk", "error"] = (
        "quality_low"
    )

    # 当前执行结果
    current_result: Optional[AgentResult] = None

    # 错误信息
    error: Optional[str] = None

    # 升级次数
    escalation_count: int = 0

    # 建议
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "reason": self.reason,
            "escalate_type": self.escalate_type,
            "current_result": {
                "content": self.current_result.content,
                "score": self.current_result.score,
                "confidence": self.current_result.confidence,
            }
            if self.current_result
            else None,
            "error": self.error,
            "escalation_count": self.escalation_count,
            "suggestion": self.suggestion,
        }


@dataclass
class TaskCancel:
    """
    L0 → L1: 取消任务
    """

    task_id: str

    # 取消原因
    reason: str = ""

    # 是否强制取消（忽略执行状态）
    force: bool = False

    # 取消后是否保存现场
    save_state: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "reason": self.reason,
            "force": self.force,
            "save_state": self.save_state,
        }


# ─────────────────────────────────────────
# 心跳和健康检查消息
# ─────────────────────────────────────────


@dataclass
class Heartbeat:
    """
    心跳消息
    L2 → L1: Worker 心跳
    L1 → L0: Manager 心跳
    """

    sender_id: str  # worker_id 或 manager_id
    sender_role: AgentRole

    # 任务状态
    task_id: Optional[str] = None

    # 当前步骤
    step_name: str = "init"
    step_started_at: Optional[datetime] = None

    # 资源使用
    tokens_used: int = 0
    tokens_budget: int = 32_000

    # 时间
    elapsed_s: int = 0
    estimated_total_s: int = 0

    # 状态
    status: Literal["running", "waiting", "blocked", "idle"] = "running"

    # 特殊模式
    special_mode: Optional[SpecialMode] = None

    # 里程碑（长任务）
    last_milestone: Optional[str] = None

    # 下载进度
    bytes_delta: int = 0
    bytes_total: int = 0

    @property
    def token_ratio(self) -> float:
        return self.tokens_used / max(self.tokens_budget, 1)

    @property
    def progress_ratio(self) -> float:
        return self.elapsed_s / max(self.estimated_total_s, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "sender_role": self.sender_role.value,
            "task_id": self.task_id,
            "step_name": self.step_name,
            "step_started_at": self.step_started_at.isoformat()
            if self.step_started_at
            else None,
            "tokens_used": self.tokens_used,
            "tokens_budget": self.tokens_budget,
            "elapsed_s": self.elapsed_s,
            "estimated_total_s": self.estimated_total_s,
            "status": self.status,
            "special_mode": self.special_mode.value if self.special_mode else None,
            "last_milestone": self.last_milestone,
            "bytes_delta": self.bytes_delta,
            "bytes_total": self.bytes_total,
        }


@dataclass
class HealthCheck:
    """
    健康检查请求
    """

    check_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    check_type: Literal["l0", "l1", "l2", "worker", "system"] = "system"
    target_id: str = ""

    # 检查项
    check_items: List[str] = field(
        default_factory=lambda: ["heartbeat", "resource", "queue"]
    )

    # 超时时间
    timeout_s: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "check_type": self.check_type,
            "target_id": self.target_id,
            "check_items": self.check_items,
            "timeout_s": self.timeout_s,
        }


@dataclass
class HealthReport:
    """
    健康检查报告
    """

    check_id: str

    # 检查结果
    healthy: bool = True

    # 组件状态
    component_status: Dict[str, str] = field(default_factory=dict)

    # 资源状态
    resource_status: Dict[str, Any] = field(default_factory=dict)

    # 问题列表
    issues: List[Dict[str, Any]] = field(default_factory=list)

    # 建议
    recommendations: List[str] = field(default_factory=list)

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "healthy": self.healthy,
            "component_status": self.component_status,
            "resource_status": self.resource_status,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }


# ─────────────────────────────────────────
# 授权消息
# ─────────────────────────────────────────


@dataclass
class AuthRequest:
    """
    L2 → L1: 请求授权
    Worker 遇到权限不足的操作
    """

    worker_id: str
    task_id: str
    manifest_id: str

    # 请求的操作
    action: Action

    # 请求原因
    reason: str = ""

    # 当前风险等级
    current_risk: RiskLevel = RiskLevel.NEED_L1

    # 上下文
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "manifest_id": self.manifest_id,
            "action": {
                "type": self.action.type,
                "params": self.action.params,
                "reason": self.action.reason,
                "task_id": self.action.task_id,
            },
            "reason": self.reason,
            "current_risk": self.current_risk.value,
            "context": self.context,
        }


@dataclass
class AuthGrant:
    """
    L1 → L2: 授予权限
    """

    worker_id: str
    task_id: str

    # 授权的操作
    action_type: str

    # 授权令牌
    token_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # 权限范围
    permission_scope: Dict[str, Any] = field(default_factory=dict)

    # 过期时间
    expires_at: datetime = field(
        default_factory=lambda: datetime.utcnow() + timedelta(minutes=10)
    )

    # 条件
    conditions: List[str] = field(default_factory=list)

    # 备注
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "action_type": self.action_type,
            "token_id": self.token_id,
            "permission_scope": self.permission_scope,
            "expires_at": self.expires_at.isoformat(),
            "conditions": self.conditions,
            "note": self.note,
        }


@dataclass
class AuthDeny:
    """
    L1 → L2: 拒绝授权
    """

    worker_id: str
    task_id: str

    # 拒绝的操作
    action_type: str

    # 拒绝原因
    reason: str

    # 建议
    suggestion: str = ""

    # 是否可申诉
    appealable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "action_type": self.action_type,
            "reason": self.reason,
            "suggestion": self.suggestion,
            "appealable": self.appealable,
        }


@dataclass
class AuthL0Decision:
    """
    L0 → L2: L0 高危操作决策结果
    """

    worker_id: str
    task_id: str

    # 决策结果
    approved: bool

    # 授权令牌（如果批准）
    token_id: Optional[str] = None

    # 拒绝原因（如果拒绝）
    reason: Optional[str] = None

    # L0 的附加指令
    instruction: str = ""

    # 决策者 ID
    decision_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_id": self.task_id,
            "approved": self.approved,
            "token_id": self.token_id,
            "reason": self.reason,
            "instruction": self.instruction,
            "decision_by": self.decision_by,
        }


# ─────────────────────────────────────────
# 控制命令消息
# ─────────────────────────────────────────


@dataclass
class ControlCommand:
    """
    控制命令
    用于启动、停止、重启组件
    """

    command: Literal["start", "stop", "restart", "pause", "resume"]

    target_role: AgentRole
    target_id: str

    # 命令参数
    params: Dict[str, Any] = field(default_factory=dict)

    # 原因
    reason: str = ""

    # 是否强制执行
    force: bool = False

    # 回调
    callback_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "target_role": self.target_role.value,
            "target_id": self.target_id,
            "params": self.params,
            "reason": self.reason,
            "force": self.force,
            "callback_url": self.callback_url,
        }


@dataclass
class ControlResponse:
    """
    控制命令响应
    """

    command_id: str

    # 执行结果
    success: bool

    # 消息
    message: str = ""

    # 错误详情
    error: Optional[str] = None

    # 新的组件状态
    new_status: Optional[str] = None

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "success": self.success,
            "message": self.message,
            "error": self.error,
            "new_status": self.new_status,
            "timestamp": self.timestamp.isoformat(),
        }


# ─────────────────────────────────────────
# 审核消息
# ─────────────────────────────────────────


@dataclass
class AuditRequest:
    """
    L0 → L1: 请求审核
    L0 完成决策后，请求 L1 对执行结果进行审核
    """

    task_id: str

    # 待审核的内容
    content: str

    # 用户原始指令
    original_instruction: str

    # 修正历史
    correction_history: List[str] = field(default_factory=list)

    # 审核维度
    audit_dimensions: List[str] = field(
        default_factory=lambda: ["logic", "factual", "risk", "alignment"]
    )

    # 上下文
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "content": self.content,
            "original_instruction": self.original_instruction,
            "correction_history": self.correction_history,
            "audit_dimensions": self.audit_dimensions,
            "context": self.context,
        }


@dataclass
class AuditResponse:
    """
    L0 → L1: 审核结果
    """

    task_id: str

    # 审核结果
    passed: bool

    # 各维度分数
    scores: Dict[str, float] = field(default_factory=dict)

    # 总体分数
    overall_score: float = 0.0

    # 修正指令（如果不通过）
    correction: Optional[str] = None

    # 审核者 ID
    auditor_id: str = ""

    # 审核时间
    audit_time: datetime = field(default_factory=datetime.utcnow)

    # 备注
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "scores": self.scores,
            "overall_score": self.overall_score,
            "correction": self.correction,
            "auditor_id": self.auditor_id,
            "audit_time": self.audit_time.isoformat(),
            "notes": self.notes,
        }


# ─────────────────────────────────────────
# 通知消息
# ─────────────────────────────────────────


@dataclass
class NotifyMessage:
    """
    通知消息
    警告、告警、人工介入请求
    """

    level: Literal["info", "warning", "critical"]

    title: str
    content: str

    # 关联任务
    task_id: Optional[str] = None
    worker_id: Optional[str] = None

    # 渠道
    channels: List[str] = field(default_factory=lambda: ["dingtalk"])

    # 行动建议
    action_suggested: str = ""

    # 额外数据
    extra: Dict[str, Any] = field(default_factory=dict)

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "content": self.content,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "channels": self.channels,
            "action_suggested": self.action_suggested,
            "extra": self.extra,
            "timestamp": self.timestamp.isoformat(),
        }


# ─────────────────────────────────────────
# 消息工厂函数
# ─────────────────────────────────────────


def create_task_dispatch(
    task: Task,
    executor_model: str,
    validator_model: str,
    sender_id: str = "",
) -> Message:
    """创建任务下发消息"""
    dispatch = TaskDispatch(
        task=task,
        executor_model=executor_model,
        validator_model=validator_model,
    )
    body = dispatch.to_dict()
    return Message.create(
        message_type=MessageType.TASK_DISPATCH,
        sender=AgentRole.L0_BRAIN,
        receiver=AgentRole.L1_EXECUTOR,
        body=body,
        sender_id=sender_id,
    )


def create_task_result(
    task_id: str,
    worker_id: str,
    result: AgentResult,
    manifest_id: str = "",
    need_escalate: bool = False,
) -> Message:
    """创建任务结果消息"""
    task_result = TaskResult(
        task_id=task_id,
        worker_id=worker_id,
        result=result,
        manifest_id=manifest_id,
        need_escalate=need_escalate,
    )
    body = task_result.to_dict()
    return Message.create(
        message_type=MessageType.TASK_RESULT,
        sender=AgentRole.L2_WORKER,
        receiver=AgentRole.L1_EXECUTOR,
        body=body,
        sender_id=worker_id,
    )


def create_heartbeat(
    sender_id: str,
    sender_role: AgentRole,
    task_id: Optional[str] = None,
    **kwargs,
) -> Message:
    """创建心跳消息"""
    heartbeat = Heartbeat(
        sender_id=sender_id,
        sender_role=sender_role,
        task_id=task_id,
        **kwargs,
    )
    body = heartbeat.to_dict()
    return Message.create(
        message_type=MessageType.HEARTBEAT,
        sender=sender_role,
        receiver=AgentRole.L1_EXECUTOR
        if sender_role == AgentRole.L2_WORKER
        else AgentRole.L0_BRAIN,
        body=body,
        sender_id=sender_id,
        priority=MessagePriority.HIGH,
    )


def create_auth_request(
    worker_id: str,
    task_id: str,
    manifest_id: str,
    action: Action,
    reason: str = "",
    current_risk: RiskLevel = RiskLevel.NEED_L1,
) -> Message:
    """创建授权请求消息"""
    auth_req = AuthRequest(
        worker_id=worker_id,
        task_id=task_id,
        manifest_id=manifest_id,
        action=action,
        reason=reason,
        current_risk=current_risk,
    )
    body = auth_req.to_dict()
    return Message.create(
        message_type=MessageType.AUTH_REQUEST,
        sender=AgentRole.L2_WORKER,
        receiver=AgentRole.L1_EXECUTOR,
        body=body,
        sender_id=worker_id,
        priority=MessagePriority.URGENT,
    )


def create_control_command(
    command: Literal["start", "stop", "restart", "pause", "resume"],
    target_role: AgentRole,
    target_id: str,
    reason: str = "",
    **kwargs,
) -> Message:
    """创建控制命令消息"""
    ctrl = ControlCommand(
        command=command,
        target_role=target_role,
        target_id=target_id,
        reason=reason,
        **kwargs,
    )
    body = ctrl.to_dict()
    return Message.create(
        message_type=MessageType.CONTROL_STOP
        if command == "stop"
        else MessageType.CONTROL_START,
        sender=AgentRole.L0_BRAIN,
        receiver=target_role,
        body=body,
        priority=MessagePriority.HIGH,
    )


def parse_message(message_data: Dict[str, Any]) -> Message:
    """解析消息（从 Dict）"""
    header = message_data.get("header", {})
    body = message_data.get("body", {})

    return Message(
        header=MessageHeader(
            message_id=header.get("message_id", str(uuid.uuid4())),
            message_type=MessageType(header.get("message_type", "task.dispatch")),
            priority=MessagePriority(header.get("priority", "normal")),
            sender=AgentRole(header.get("sender", "l2_worker")),
            sender_id=header.get("sender_id", ""),
            receiver=AgentRole(header.get("receiver", "l1_executor")),
            receiver_id=header.get("receiver_id", ""),
            correlation_id=header.get("correlation_id"),
            reply_to=header.get("reply_to"),
            timestamp=datetime.fromisoformat(header["timestamp"])
            if header.get("timestamp")
            else datetime.utcnow(),
            expires_at=datetime.fromisoformat(header["expires_at"])
            if header.get("expires_at")
            else None,
            metadata=header.get("metadata", {}),
        ),
        body=body,
    )
