"""
ziwei/core/exceptions.py
ZiWei 异常体系
"""


class ZiWeiError(Exception):
    """基础异常"""


class ManifestExpiredError(ZiWeiError):
    """TaskManifest 已过期"""


class ManifestInvalidError(ZiWeiError):
    """TaskManifest 签名校验失败"""


class PermissionDeniedError(ZiWeiError):
    """操作权限被拒绝"""
    def __init__(self, action_type: str, reason: str = ""):
        super().__init__(f"Permission denied for '{action_type}': {reason}")
        self.action_type = action_type


class EscalationError(ZiWeiError):
    """上报链路失败"""


class WorkerStaleError(ZiWeiError):
    """Worker 呆死"""
    def __init__(self, worker_id: str, reason: str):
        super().__init__(f"Worker '{worker_id}' stale: {reason}")
        self.worker_id = worker_id


class L1DownError(ZiWeiError):
    """L1 Manager 不可用"""


class L0DownError(ZiWeiError):
    """L0 Brain 不可用"""


class HumanTimeoutError(ZiWeiError):
    """人工决策超时"""


class SkillSecurityError(ZiWeiError):
    """Skill/MCP 安全检测失败"""


class MaxEscalationError(ZiWeiError):
    """超出最大上报次数"""


class AuditFailedError(ZiWeiError):
    """L0 审核不通过"""
    def __init__(self, score: float, correction: str):
        super().__init__(f"Audit failed (score={score:.2f}): {correction}")
        self.score = score
        self.correction = correction
