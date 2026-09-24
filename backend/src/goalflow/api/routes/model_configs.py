"""个人模型配置接口（T14 契约 PR）：创建/列举/修改/测试/默认/删除六端点。

端点清单的出处：07-model-provider-design.md"接口建议"节（原文标为建议，
经 T14 交接卡决策 A9 确认为契约）。语义要点：

- 响应一律不携带凭证材料，读接口只给 has_credential（决策 A5）；
- PATCH 走 expected_revision 乐观锁，provider 不可改（决策 A3）；
- DELETE 是归档：enabled=0、凭证删除、行保留，归档配置对所有端点按 404 处理（决策 A4）；
- /test 结果在 200 响应体返回，不抛 MODEL_UNAVAILABLE（决策 A7）。

端点本期交付契约形状（桩），业务实现在 T14 PR-2 接入。
"""

from datetime import datetime
from typing import Annotated, Any, Final, NoReturn

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field, model_validator

from goalflow.api.dependencies import CurrentUserDep, require_idempotency_key
from goalflow.contracts.enums import (
    CapabilityState,
    ModelApiMode,
    ModelProvider,
    ModelTestErrorKind,
    ModelTestOutcome,
)
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import ErrorResponse

router = APIRouter(tags=["model-configs"])

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录或会话失效",
    403: ("请求来源不受信任，或自定义地址被实例出站策略拒绝（MODEL_ENDPOINT_NOT_ALLOWED，校验先于模型调用执行）"),
    404: "配置不存在、不属于当前用户或已删除（归档配置一律按不存在处理，T14 决策 A4）",
    409: (
        "Idempotency-Key 已用于另一项请求（IDEMPOTENCY_KEY_CONFLICT）、配置版本已变（REVISION_CONFLICT）"
        "或配置已禁用/已删除，不能设为默认（仅 PUT default）"
    ),
    422: "请求参数校验未通过",
    429: "测试调用频率超限（RATE_LIMITED）",
    500: "服务内部错误",
}


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in status_codes}


ConfigIdPath = Annotated[str, Path(max_length=36, description="模型配置 ID")]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def _implementation_stub() -> NoReturn:
    """契约桩：业务实现随 T14 PR-2 接入（交接卡第 6 节）。"""
    raise GoalflowError(
        ErrorCode.INTERNAL_ERROR,
        "该端点随 T14 业务实现（PR-2）接入交付",
    )


def _validate_provider_api_mode(provider: ModelProvider, api_mode: ModelApiMode | None) -> None:
    """provider 与 api_mode 的合法组合（07 号第 2 节；T14 决策 A3）。

    openai 必须二选一；anthropic 必须为空。DB CHECK 兜底同一规则。
    """
    if provider is ModelProvider.OPENAI and api_mode is None:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED, "openai 配置必须指定 api_mode（responses 或 chat_completions）"
        )
    if provider is ModelProvider.ANTHROPIC and api_mode is not None:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, "anthropic 配置不使用 api_mode，请勿提交")


# —— 请求体 ——


class CreateModelConfigRequest(BaseModel):
    """创建个人配置。凭证加密入库，任何响应不回传（T14 决策 A5/A10）。

    本地无鉴权模型可不填 Key（产品 06 号第 2 节，T14 决策 A11）；是否必填由
    provider 与服务形态决定，业务实现按出站策略与 provider 要求校验。
    """

    name: str = Field(min_length=1, max_length=100, description="用户可见的配置名称")
    model_provider: ModelProvider = Field(description="供应商（D08 已确认双 provider）；创建后不可修改")
    api_mode: ModelApiMode | None = Field(
        default=None,
        description="仅 openai 有效：responses / chat_completions；anthropic 必须为空（DB CHECK 兜底）",
    )
    base_url: str | None = Field(
        default=None,
        max_length=500,
        description="自定义服务地址；缺省或 null 表示 provider 官方默认端点。非官方地址受实例出站策略约束",
    )
    model_id: str = Field(min_length=1, max_length=128, description="供应商模型名，如 claude-sonnet-4")
    api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description="供应商 API Key；信封加密后入库。本地无鉴权模型可不填（决策 A11）；显式空串被拒",
    )

    @model_validator(mode="after")
    def _check_provider_api_mode(self) -> "CreateModelConfigRequest":
        _validate_provider_api_mode(self.model_provider, self.api_mode)
        return self


class UpdateModelConfigRequest(BaseModel):
    """修改配置。provider 不可修改（换 provider 新建配置，T14 决策 A3）。

    字段缺省表示不变；base_url 显式 null 表示清除自定义地址、回到官方默认端点。
    api_key 缺省 = 保持原值，显式空串被拒——不存在掩码回显覆盖路径（决策 A5）。
    禁用（enabled=false）保留凭证、可重新启用；删除走 DELETE（决策 A12）。
    """

    expected_revision: int = Field(ge=0, description="乐观锁：当前配置版本；不匹配返回 REVISION_CONFLICT")
    name: str | None = Field(default=None, min_length=1, max_length=100, description="缺省表示不变")
    api_mode: ModelApiMode | None = Field(
        default=None,
        description="仅 openai 配置可修改；修改视为配置版本变化（决策 A3）。anthropic 配置提交非空值被拒",
    )
    base_url: str | None = Field(
        default=None,
        max_length=500,
        description="显式 null 清除自定义地址；字段缺省表示不变（业务实现以字段提交状态区分）",
    )
    model_id: str | None = Field(default=None, min_length=1, max_length=128, description="缺省表示不变")
    api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description="替换凭证；缺省或 null 表示保持原值，显式空串被拒（422）",
    )
    enabled: bool | None = Field(
        default=None,
        description="缺省表示不变；false 禁用（凭证保留、可重新启用，06 号：支持替换、禁用和删除）。"
        "已删除配置按 404 处理，不能经此端点恢复",
    )


class SetDefaultModelConfigRequest(BaseModel):
    """指定当前用户的默认配置。旧默认在同一事务内让位（每用户至多一个默认）。"""

    config_id: str = Field(max_length=36, description="要设为默认的配置；必须属于当前用户且未删除")


class TestModelConfigRequest(BaseModel):
    """触发连通性测试。固定无私人内容短提示，频控与超时由服务端约束（决策 A7）。"""

    pass


class DeleteModelConfigRequest(BaseModel):
    """删除（归档）配置。凭证删除后不可恢复；默认配置失效时前端提示重新选择。"""

    pass


# —— 响应 ——


class ModelCapabilities(BaseModel):
    """能力三态记录（07 号第 1 节；T14 决策 A8）。

    LangChain 用提示工程模拟结构化输出的组合记 unsupported，不伪报原生能力。
    """

    basic_generation: CapabilityState = Field(description="基本生成；能力门槛的最低项")
    structured_output: CapabilityState = Field(description="原生 schema 结构化输出")
    streaming: CapabilityState = Field(description="流式输出；不可用时前端显示等待后完整返回")


class ModelConfigResponse(BaseModel):
    """个人配置的脱敏元数据。响应不包含任何凭证材料（T14 决策 A5）。"""

    id: str
    name: str
    model_provider: ModelProvider
    api_mode: ModelApiMode | None = Field(description="仅 openai 配置非空")
    base_url: str | None = Field(description="自定义服务地址；null 表示官方默认端点")
    model_id: str = Field(description="供应商模型名")
    revision: int = Field(description="配置版本；api_mode 等修改会递增")
    enabled: bool = Field(description="false 表示已删除（归档行保留以支撑作业历史）")
    is_default: bool = Field(description="是否为当前用户默认配置；每用户至多一个")
    has_credential: bool = Field(description="是否持有凭证；凭证材料本身绝不出现在响应中")
    capabilities: ModelCapabilities | None = Field(description="最近一次测试的能力记录；从未测试为 null")
    last_test_at: datetime | None = Field(description="最近一次测试时间；从未测试为 null")
    created_at: datetime
    updated_at: datetime


class ModelTestError(BaseModel):
    """测试失败信息。供应商原始错误脱敏后只保留分类与说明（07 号第 1 节）。"""

    kind: ModelTestErrorKind = Field(description="错误分类：鉴权/路径/限流/网络/协议")
    message: str = Field(description="脱敏后的错误说明；不含供应商原始细节与凭证片段")


class ModelTestResponse(BaseModel):
    """连通性测试结果。失败也走 200 响应体，不抛 MODEL_UNAVAILABLE（T14 决策 A7）。"""

    config_id: str
    config_revision: int = Field(description="测试所依据的配置版本；与当前 revision 不一致说明测试期间配置被修改")
    outcome: ModelTestOutcome
    capabilities: ModelCapabilities | None = Field(description="测试得出的能力记录；失败时可能为 null")
    error: ModelTestError | None = Field(description="失败时的分类与脱敏说明；成功为 null")
    tested_at: datetime


# —— 端点 ——


@router.post(
    "/api/model-configs",
    summary="创建个人模型配置",
    description=(
        "创建当前用户的模型配置，返回脱敏元数据。凭证信封加密入库（主密钥由部署环境提供，T14 决策 A10）；"
        "openai 必须指定 api_mode，anthropic 必须为空（决策 A3）。"
    ),
    status_code=201,
    responses=_errors(401, 403, 409, 422, 500),
)
def create_model_config(
    user: CurrentUserDep,
    request: CreateModelConfigRequest,
    idempotency_key: IdempotencyKeyDep,
) -> ModelConfigResponse:
    _implementation_stub()


@router.get(
    "/api/model-configs",
    summary="列举当前用户的模型配置",
    description=(
        "返回当前用户未删除的全部配置，含已禁用（enabled=false，供重新启用）；"
        "已删除（归档）配置不出现在列表中（决策 A4/A12）。"
    ),
    responses=_errors(401, 500),
)
def list_model_configs(user: CurrentUserDep) -> list[ModelConfigResponse]:
    _implementation_stub()


@router.patch(
    "/api/model-configs/{config_id}",
    summary="修改个人模型配置",
    description=(
        "带 expected_revision 乐观锁修改；版本不匹配返回 REVISION_CONFLICT。provider 不可修改；"
        "api_mode 修改视为配置版本变化（决策 A3）。api_key 缺省 = 保持原值（决策 A5）；"
        "enabled=false 禁用但保留凭证、可重新启用（决策 A12）。"
    ),
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def update_model_config(
    user: CurrentUserDep,
    config_id: ConfigIdPath,
    request: UpdateModelConfigRequest,
) -> ModelConfigResponse:
    _implementation_stub()


@router.post(
    "/api/model-configs/{config_id}/test",
    summary="测试个人模型配置",
    description=(
        "显式用户触发，用固定无私人内容短提示、限制输出/重试/超时发起最小真实调用"
        "（直接构造 chat model，不经过 LangGraph 图）。结果在 200 响应体返回：能力三态与"
        "脱敏错误分类（决策 A7/A8）；频控仅作滥用兜底，阈值放宽至每用户 1000 次/15 分钟，"
        "正常使用不可触达（决策 A14）。"
    ),
    responses=_errors(401, 404, 422, 429, 500),
)
def test_model_config(
    user: CurrentUserDep,
    config_id: ConfigIdPath,
    request: TestModelConfigRequest,
) -> ModelTestResponse:
    _implementation_stub()


@router.put(
    "/api/model-configs/default",
    summary="设置默认模型配置",
    description=(
        "指定当前用户有效配置为默认；旧默认在同一写事务内让位，每用户至多一个默认（DB 部分唯一索引兜底，决策 A2）。"
        "已禁用或已删除的配置不能设为默认（409）。"
    ),
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def set_default_model_config(
    user: CurrentUserDep,
    request: SetDefaultModelConfigRequest,
    idempotency_key: IdempotencyKeyDep,
) -> ModelConfigResponse:
    _implementation_stub()


@router.delete(
    "/api/model-configs/{config_id}",
    summary="删除个人模型配置",
    description=(
        "归档式删除（与 PATCH 的禁用不同，删除不可恢复）：deleted_at 置时间戳、enabled=0、"
        "凭证与密钥版本清空、默认标记清除，行保留以支撑作业所需的非敏感历史信息（07 号接口建议；决策 A4）。"
        "归档配置后续一律按 404 处理，已发出的模型请求无法通过删除收回，但后续重试会检查删除状态。"
        "删除默认配置时前端提示重新选择。"
    ),
    responses=_errors(401, 403, 404, 409, 500),
)
def delete_model_config(
    user: CurrentUserDep,
    config_id: ConfigIdPath,
    request: DeleteModelConfigRequest,
    idempotency_key: IdempotencyKeyDep,
) -> ModelConfigResponse:
    _implementation_stub()
