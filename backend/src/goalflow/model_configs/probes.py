"""连通性测试的最小真实调用（T14 决策 A7/A8；交付计划"不经过 LangGraph 图"）。

直接构造 chat model 发起，不依赖领域策略。单次 /test 依次执行三个探测，各自由
服务端显式约束：短提示、max_tokens 很小、timeout 与 max_retries 显式设置
（07 号：max_retries 默认 6 与重试叠加会乘法放大，这里必须显式给 1）。

- basic_generation：`model.invoke` 拿文本——能力门槛的最低项；
- structured_output：`with_structured_output` + 极小 schema——按 A8 如实记录，
  失败（含提示工程模拟路径抛错）就是 unsupported，不伪报原生能力；
- streaming：`model.stream` 取首个片段。

错误分类（ModelTestErrorKind）按异常类名判别，供应商原始错误信息脱敏后才
进入响应：只保留异常类名与一段截断且掩掉密钥样式的文本。
"""

import re
from typing import Any, Final

from pydantic import BaseModel

from goalflow.contracts.enums import CapabilityState, ModelProvider, ModelTestErrorKind

_PROBE_TIMEOUT_SECONDS: Final = 15
_PROBE_MAX_RETRIES: Final = 1
_PROBE_MAX_TOKENS: Final = 16
_BASIC_PROMPT: Final = "Reply with the single word: ok"
_STREAM_PROMPT: Final = "Reply with the single word: ok"
_ERROR_MESSAGE_MAX_LENGTH: Final = 160

# 疑似凭证样式（与 check.sh 的粗筛同源；sk- 后允许连字符，覆盖 sk-ant-…）：命中即整体掩掉。
_SECRET_PATTERN: Final = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{40,})")

_AUTH_CLASS_NAMES: Final[frozenset[str]] = frozenset(
    {"AuthenticationError", "PermissionDeniedError", "PermissionError"}
)
_MODEL_CLASS_NAMES: Final[frozenset[str]] = frozenset({"NotFoundError", "APIStatusError"})
_RATE_CLASS_NAMES: Final[frozenset[str]] = frozenset({"RateLimitError"})
_NETWORK_CLASS_NAMES: Final[frozenset[str]] = frozenset(
    {
        "APIConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "WriteError",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
        "OSError",
        "TimeoutError",
    }
)


class _TinyCheck(BaseModel):
    """结构化输出探测的极小 schema。"""

    ok: bool


def _sanitize_error_text(text: str) -> str:
    masked = _SECRET_PATTERN.sub("[已掩码]", text)
    return masked.replace("\n", " ")[:_ERROR_MESSAGE_MAX_LENGTH]


def classify_error(exc: Exception) -> ModelTestErrorKind:
    """按异常类名归到五类之一；类名不在已知集合里一律按协议错误处理（宁可保守）。"""
    seen: set[type[BaseException]] = set()
    current: BaseException | None = exc
    while current is not None and type(current) not in seen:
        seen.add(type(current))
        name = type(current).__name__
        if name in _AUTH_CLASS_NAMES:
            return ModelTestErrorKind.AUTH
        if name in _RATE_CLASS_NAMES:
            return ModelTestErrorKind.RATE_LIMITED
        if name in _NETWORK_CLASS_NAMES:
            return ModelTestErrorKind.NETWORK
        # NotFoundError/APIStatusError 需要看状态码：404 才是模型/路径错，其余按协议。
        if name in _MODEL_CLASS_NAMES:
            status = getattr(current, "status_code", None)
            if status == 404:
                return ModelTestErrorKind.MODEL_NOT_FOUND
        current = current.__cause__
    return ModelTestErrorKind.PROTOCOL


def build_chat_model(
    *,
    model_provider: ModelProvider,
    model_id: str,
    api_key: str | None,
    base_url: str | None,
) -> Any:
    """按配置构造 chat model 实例（07 号第 1 节的 init_chat_model 参数面）。"""
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {
        "model": model_id,
        "model_provider": model_provider.value,
        "timeout": _PROBE_TIMEOUT_SECONDS,
        "max_retries": _PROBE_MAX_RETRIES,
        "max_tokens": _PROBE_MAX_TOKENS,
    }
    if api_key is not None:
        kwargs["api_key"] = api_key
    if base_url is not None:
        kwargs["base_url"] = base_url
    return init_chat_model(**kwargs)


class ProbeResult:
    """单个探测的结果：能力状态与（失败时的）错误分类。"""

    __slots__ = ("capability", "error_kind", "error_message")

    def __init__(self, capability: CapabilityState, error_kind: ModelTestErrorKind | None = None) -> None:
        self.capability = capability
        self.error_kind = error_kind
        self.error_message: str | None = None

    @classmethod
    def ok(cls) -> "ProbeResult":
        return cls(CapabilityState.SUPPORTED)

    @classmethod
    def unsupported(cls) -> "ProbeResult":
        return cls(CapabilityState.UNSUPPORTED)

    @classmethod
    def failed(cls, exc: Exception) -> "ProbeResult":
        result = cls(CapabilityState.UNSUPPORTED, classify_error(exc))
        result.error_message = _sanitize_error_text(f"{type(exc).__name__}: {exc}")
        return result


def _invoke_basic(model: Any) -> None:
    model.invoke(_BASIC_PROMPT)


def _invoke_structured(model: Any) -> None:
    structured = model.with_structured_output(_TinyCheck)
    structured.invoke(_BASIC_PROMPT)


def _invoke_streaming(model: Any) -> None:
    for _ in model.stream(_STREAM_PROMPT):
        break


_PROBES: Final[tuple[tuple[str, Any], ...]] = (
    ("basic_generation", _invoke_basic),
    ("structured_output", _invoke_structured),
    ("streaming", _invoke_streaming),
)


def run_capability_probes(model: Any) -> dict[str, ProbeResult]:
    """依次执行三个探测，返回能力名 → 结果。异常按五类脱敏分类（决策 A7）。"""
    results: dict[str, ProbeResult] = {}
    for capability_name, probe in _PROBES:
        try:
            probe(model)
            results[capability_name] = ProbeResult.ok()
        except Exception as exc:  # 供应商错误形态不可枚举，统一分类脱敏后记录
            results[capability_name] = ProbeResult.failed(exc)
    return results
