"""通用幂等存储（T07）。其他模块只从本包入口 import，不 import 内部文件。"""

from goalflow.idempotency.service import (
    RETENTION,
    IdempotentOutcome,
    IdempotentRequest,
    ResultRef,
    compute_request_hash,
    purge_expired,
    run_idempotent,
)

__all__ = [
    "RETENTION",
    "IdempotentOutcome",
    "IdempotentRequest",
    "ResultRef",
    "compute_request_hash",
    "purge_expired",
    "run_idempotent",
]
