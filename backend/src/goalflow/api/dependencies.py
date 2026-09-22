"""路由共用的 FastAPI 依赖。"""

from typing import Annotated

from fastapi import Header

from goalflow.contracts.http import IDEMPOTENCY_KEY_HEADER, normalize_idempotency_key


def require_idempotency_key(
    raw_key: Annotated[str | None, Header(alias=IDEMPOTENCY_KEY_HEADER)] = None,
) -> str:
    """写操作的幂等键。缺失或格式非法时返回 VALIDATION_FAILED。

    见 docs/engineering/01-contracts-and-ownership.md 第 5 节。
    本依赖只负责取值与校验；"相同 key 不同内容返回冲突"需要比对已记录的请求，
    属于存储层，见 T02 交接卡决策 A4。
    """
    return normalize_idempotency_key(raw_key)
