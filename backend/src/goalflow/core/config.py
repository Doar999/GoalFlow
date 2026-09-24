"""运行配置。环境变量是公共契约，新增变量必须同步 .env.example。

见 docs/engineering/01-contracts-and-ownership.md 第 2 节。
"""

from enum import StrEnum
from functools import lru_cache
from typing import Final, Self
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env.example 里发给使用者的占位值。带着它上生产等于会话可被伪造、
# 已存储的用户模型凭证可被解密，因此必须在启动时就拦下。
PLACEHOLDER_SECRET: Final = "change-me-generate-a-random-32-byte-value"

_REQUIRED_IN_PRODUCTION: Final = ("session_secret", "credential_encryption_key")


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """从 GOALFLOW_ 前缀的环境变量与 .env 读取。

    数据库连接串形如 sqlite+pysqlite:///<路径>（RFC 0003）。这里只读取字符串，
    格式校验与连接参数属于 `goalflow.db`——`create_database_engine()` 会在启动时
    拒绝空值、非 SQLite 连接串和内存库。默认留空是为了让"忘记配"表现为一条明确的
    启动错误，而不是悄悄连上某个默认位置的库文件。
    """

    model_config = SettingsConfigDict(
        env_prefix="GOALFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.LOCAL
    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    session_secret: SecretStr | None = None
    credential_encryption_key: SecretStr | None = None
    allow_registration: bool = True
    # 自定义模型服务地址的本地/内网允许列表（T14 决策 A13）：逗号分隔的主机名、IP 或 CIDR 段。
    # 留空 = 本地/内网端点全部拒绝（多用户托管的安全默认）；自部署跑本地模型时由部署者显式开放。
    # 普通用户不能修改该策略（07 号"自定义地址"节）。与 T15 的出站开关同属实例级环境变量先例。
    model_endpoint_allowlist: str = ""
    # 浏览器访问本应用的源，形如 https://goalflow.example.com，不带路径与末尾斜杠。
    # 写请求的 Origin 必须与它逐字相等（CSRF 防护，T03 决策 C7）。
    # 非生产环境留空表示不做 Origin 校验；生产环境必填且必须是 https。
    public_origin: str = ""
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_public_origin(self) -> Self:
        if self.public_origin:
            parsed = urlsplit(self.public_origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "GOALFLOW_PUBLIC_ORIGIN 必须形如 https://example.com 或 http://localhost:5173，不带路径与末尾斜杠"
                )
        if self.env is Environment.PRODUCTION and not self.public_origin.startswith("https://"):
            # 生产 Cookie 带 Secure 与 __Host- 前缀，只在 https 下生效；缺了这一项等于关掉 CSRF 防护。
            raise ValueError("生产环境必须配置 https 的 GOALFLOW_PUBLIC_ORIGIN")
        return self

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Self:
        if self.env is not Environment.PRODUCTION:
            return self

        missing = [name for name in _REQUIRED_IN_PRODUCTION if getattr(self, name) is None]
        if missing:
            raise ValueError("生产环境缺少必填密钥：" + "、".join(f"GOALFLOW_{n.upper()}" for n in missing))

        placeholders = [
            name
            for name in _REQUIRED_IN_PRODUCTION
            if (value := getattr(self, name)) is not None and value.get_secret_value() == PLACEHOLDER_SECRET
        ]
        if placeholders:
            raise ValueError(
                "生产环境仍在使用 .env.example 的占位密钥：" + "、".join(f"GOALFLOW_{n.upper()}" for n in placeholders)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例。测试里需要不同取值时直接构造 Settings(...)，不要改这里的缓存。"""
    return Settings()
