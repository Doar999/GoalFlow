"""出站校验测试：协议/端口/本地内网段/允许列表（T14 决策 A6/A13）。

DNS 解析用注入的桩替代——规则测试不依赖真实网络。
"""

from collections.abc import Callable

import pytest

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.model_configs.outbound import OutboundPolicy

Resolver = Callable[[str], list[str]]

_PUBLIC = ["93.184.216.34"]
_PRIVATE = ["10.1.2.3"]
_LOOPBACK = ["127.0.0.1"]
_METADATA = ["169.254.169.254"]


def _resolver(ips: list[str]) -> Resolver:
    return lambda hostname: ips


def _policy(allowlist: str = "", resolver: Resolver | None = None) -> OutboundPolicy:
    return OutboundPolicy(allowlist, resolver=resolver)


class TestSchemeAndFormat:
    def test_non_http_scheme_rejected(self) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _policy().validate("ftp://api.openai.com")
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_not_a_url_rejected(self) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _policy().validate("not a url")
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_url_with_credentials_rejected(self) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _policy().validate("https://user:pass@api.openai.com")
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED


class TestPublicAddresses:
    def test_public_host_allowed_without_allowlist(self) -> None:
        _policy(resolver=_resolver(_PUBLIC)).validate("https://api.openai.com/v1")

    def test_public_port_restriction(self) -> None:
        # 公共地址的非标端口拒绝：防止借 HTTP 探测内网非 HTTP 服务。
        with pytest.raises(GoalflowError) as excinfo:
            _policy(resolver=_resolver(_PUBLIC)).validate("http://api.openai.com:6379")
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED

    def test_public_standard_alt_port_allowed(self) -> None:
        _policy(resolver=_resolver(_PUBLIC)).validate("https://api.openai.com:8443/v1")

    def test_unresolvable_host_rejected(self) -> None:
        # 解析失败按"地址不可验证"处理：拒绝而不是放行。
        policy = OutboundPolicy("", resolver=lambda hostname: (_ for _ in ()).throw(OSError("no dns")))
        with pytest.raises(GoalflowError) as excinfo:
            policy.validate("https://does-not-exist.example")
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED


class TestLocalAndIntranetBlockedByDefault:
    def test_private_ip_rejected_without_allowlist(self) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _policy(resolver=_resolver(_PRIVATE)).validate("http://models.internal:8000")
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED

    def test_loopback_hostname_rejected_without_allowlist(self) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _policy(resolver=_resolver(_LOOPBACK)).validate("http://localhost:11434")
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED

    def test_cloud_metadata_address_rejected(self) -> None:
        # 链路本地段含云元数据 169.254.169.254；10/8 的允许列表不放行它。
        with pytest.raises(GoalflowError) as excinfo:
            _policy("10.0.0.0/8", resolver=_resolver(_METADATA)).validate("http://169.254.169.254/latest")
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED

    def test_private_ip_rejected_even_with_unrelated_allowlist(self) -> None:
        with pytest.raises(GoalflowError):
            _policy("localhost", resolver=_resolver(_PRIVATE)).validate("http://models.internal:8000")


class TestAllowlistOpensLocalEndpoints:
    def test_allowlisted_hostname_allowed(self) -> None:
        _policy("models.internal", resolver=_resolver(_PRIVATE)).validate("http://models.internal:8000")

    def test_allowlisted_bare_ip_allowed(self) -> None:
        _policy("10.1.2.3", resolver=_resolver(_PRIVATE)).validate("http://10.1.2.3:11434")

    def test_allowlisted_cidr_allowed(self) -> None:
        _policy("10.0.0.0/8", resolver=_resolver(_PRIVATE)).validate("http://10.1.2.3:11434")

    def test_localhost_allowed_when_listed(self) -> None:
        _policy("localhost", resolver=_resolver(_LOOPBACK)).validate("http://localhost:11434")

    def test_unrelated_allowlist_does_not_open_other_private_host(self) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _policy("10.1.2.3", resolver=_resolver(["10.9.9.9"])).validate("http://10.9.9.9:11434")
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED

    def test_allowlist_is_case_insensitive_on_hostnames(self) -> None:
        _policy("MODELS.INTERNAL", resolver=_resolver(_PRIVATE)).validate("http://models.internal:8000")
