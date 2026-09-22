"""统一错误结构、expected_revision 与 Idempotency-Key 的契约形状。"""

import pytest
from pydantic import ValidationError

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    ErrorResponse,
    RevisionedRequest,
    RevisionedResource,
    normalize_idempotency_key,
)


def test_error_response_has_exactly_the_documented_fields():
    body = ErrorResponse(
        code=ErrorCode.REVISION_CONFLICT,
        message="计划版本已过期，请刷新后重试",
        request_id="req_0000000000",
        retryable=False,
    ).model_dump(mode="json")

    assert set(body) == {"code", "message", "request_id", "retryable", "details"}
    assert body["code"] == "REVISION_CONFLICT"
    assert body["details"] == {}


def test_revisioned_request_requires_expected_revision():
    with pytest.raises(ValidationError):
        RevisionedRequest()


def test_revisioned_request_rejects_negative_revision():
    with pytest.raises(ValidationError):
        RevisionedRequest(expected_revision=-1)


def test_revisioned_resource_accepts_zero():
    # 刚创建的资源从 0 起，不能被当成非法值。
    assert RevisionedResource(revision=0).revision == 0


def test_idempotency_key_is_trimmed():
    assert normalize_idempotency_key("  abc-123  ") == "abc-123"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "has space",
        "has/slash",
        "中文",
    ],
)
def test_invalid_idempotency_key_is_rejected(raw):
    with pytest.raises(GoalflowError) as caught:
        normalize_idempotency_key(raw)

    assert caught.value.code is ErrorCode.VALIDATION_FAILED


def test_idempotency_key_length_limit():
    assert normalize_idempotency_key("a" * IDEMPOTENCY_KEY_MAX_LENGTH)

    with pytest.raises(GoalflowError):
        normalize_idempotency_key("a" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1))
