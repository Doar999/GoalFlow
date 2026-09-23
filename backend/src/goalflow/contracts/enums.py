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
