"""错误码契约。"""

import pytest

from goalflow.contracts.errors import (
    HTTP_STATUS_BY_ERROR_CODE,
    RETRYABLE_ERROR_CODES,
    ErrorCode,
    GoalflowError,
)

# 独立表达期望，不从实现里抄常量：这六个来自 docs/development/05-module-contracts.md
# 的"共同规则"，少任何一个都是契约破坏。
CONTRACT_ERROR_CODES = {
    "REVISION_CONFLICT",
    "BUDGET_CONFLICT",
    "DEPENDENCY_CYCLE",
    "CONFIRMATION_REQUIRED",
    "INPUT_STALE",
    "MODEL_UNAVAILABLE",
}


def test_documented_error_codes_exist():
    assert {code.value for code in ErrorCode} >= CONTRACT_ERROR_CODES


def test_error_code_value_equals_its_name():
    # 值即对外字符串。名与值不一致时，前端映射和日志会各说各话。
    for code in ErrorCode:
        assert code.value == code.name


def test_every_error_code_maps_to_an_http_status():
    assert set(HTTP_STATUS_BY_ERROR_CODE) == set(ErrorCode)


def test_retryable_codes_are_a_subset_of_error_codes():
    assert set(ErrorCode) >= RETRYABLE_ERROR_CODES


def test_version_and_budget_conflicts_are_not_retryable():
    # 原样重试只会再失败一次，必须让用户先看到新状态。
    assert ErrorCode.REVISION_CONFLICT not in RETRYABLE_ERROR_CODES
    assert ErrorCode.BUDGET_CONFLICT not in RETRYABLE_ERROR_CODES


def test_error_carries_code_status_and_retryable():
    error = GoalflowError(ErrorCode.REVISION_CONFLICT, "计划版本已过期")

    assert error.code is ErrorCode.REVISION_CONFLICT
    assert error.http_status == 409
    assert error.retryable is False
    assert error.details == {}


def test_model_unavailable_is_retryable():
    assert GoalflowError(ErrorCode.MODEL_UNAVAILABLE, "供应商暂时不可用").retryable is True


def test_error_is_raisable_and_keeps_message():
    with pytest.raises(GoalflowError) as caught:
        raise GoalflowError(ErrorCode.NOT_FOUND, "目标不存在", details={"goal_id": "g-1"})

    assert str(caught.value) == "目标不存在"
    assert caught.value.details == {"goal_id": "g-1"}
