"""共享枚举。在此单点定义，新增或改名属于公共契约变更。

见 docs/engineering/01-contracts-and-ownership.md 第 2 节。
"""

from enum import StrEnum


class UserRole(StrEnum):
    """账号角色。首位注册者不自动成为管理员，只能由部署者本地命令授予。"""

    USER = "user"
    ADMIN = "admin"


class UserStatus(StrEnum):
    """账号状态。disabled 的账号不能登录，已有会话在禁用时全部撤销。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class JobStatus(StrEnum):
    """后台作业状态（T07 决策 E9）。

    queued → running → succeeded / failed / cancelled / stale；可重试错误经 retry_wait 回到 queued。
    后四个是终态，进入后不再变化。等待用户确认属于业务会话状态，不是作业状态。
    """

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE}
)


class JobEventType(StrEnum):
    """作业持久化事件（T07 决策 E19），SSE 按 sequence 补读。

    completed、failed、cancelled、stale 是终态事件，订阅方收到后即可关闭连接。
    """

    QUEUED = "queued"
    STARTED = "started"
    STAGE_COMPLETED = "stage_completed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
