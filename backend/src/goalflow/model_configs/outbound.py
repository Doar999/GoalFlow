"""自定义模型服务地址的出站校验（07 号"自定义地址"节；T14 决策 A6/A13）。

`base_url` 在交给 LangChain **之前**经过这里；框架不提供这层保护，不得省略。
规则：

- 只接受 http / https，URL 内不得携带账号口令；
- 端口若显式指定，公共地址仅允许 80/443/8080/8443（防止借 HTTP 客户端探测内网
  非 HTTP 服务，如 Redis 6379）；
- 解析出（或字面量给出）的任一地址落在本地/内网段（含 127.0.0.0/8、10/8、
  172.16/12、192.168/16、链路本地 169.254.0.0/16——云元数据 169.254.169.254 即在此段）、
  或主机名为 localhost 系，就必须命中 `GOALFLOW_MODEL_ENDPOINT_ALLOWLIST`：
  允许项可以是主机名、IP 字面量或 CIDR 段（逗号分隔）；
- 允许列表由部署环境提供，普通用户不能修改该策略（T14 决策 A13）。

重定向的说明：openai / anthropic 官方客户端基于 httpx，默认不跟随重定向，
"凭证被转发到未批准的重定向目标"的首要防线在客户端默认行为里；本模块在每次
发起调用前重新执行（不止保存时校验一次），配置保存后解析结果变化同样会被拦下。
"""

import ipaddress
import socket
from collections.abc import Callable
from typing import Final
from urllib.parse import urlsplit

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.core.config import get_settings

_ALLOWED_PUBLIC_PORTS: Final[frozenset[int]] = frozenset({80, 443, 8080, 8443})
_MAX_RESOLVED_ADDRESSES: Final = 16
_LOOPBACK_NAMES: Final[frozenset[str]] = frozenset({"localhost", "host.docker.internal"})

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], list[str]]


def _is_local_address(ip: IpAddress) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _resolve_host(hostname: str) -> list[str]:
    """解析主机名为 IP 列表。解析失败按"地址不可验证"处理，直接拒绝而不是放行。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise GoalflowError(
            ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED,
            "模型服务地址无法解析，已拒绝出站",
        ) from exc
    seen: list[str] = []
    for info in infos:
        ip_text = str(info[4][0])
        if ip_text not in seen:
            seen.append(ip_text)
        if len(seen) >= _MAX_RESOLVED_ADDRESSES:
            break
    return seen


def _parse_allowlist(allowlist: str) -> tuple[frozenset[str], list[Network]]:
    """允许项三分：CIDR 网段、裸 IP（ip_network 直接接受，视为单机网段）、其余按主机名。"""
    hostnames: set[str] = set()
    networks: list[Network] = []
    for entry in allowlist.split(","):
        text = entry.strip().lower()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            hostnames.add(text)
    return frozenset(hostnames), networks


def _all_public(resolved: list[str]) -> bool:
    if not resolved:
        return False
    for ip_text in resolved:
        try:
            ip: IpAddress = ipaddress.ip_address(ip_text)
        except ValueError:
            return False
        if _is_local_address(ip):
            return False
    return True


class OutboundPolicy:
    """按实例允许列表校验出站地址；API 进程内单例，测试可注入自定义解析器与列表。"""

    def __init__(self, allowlist: str, *, resolver: Resolver | None = None) -> None:
        self._resolver: Resolver = resolver or _resolve_host
        self._hostnames, self._networks = _parse_allowlist(allowlist)

    def validate(self, base_url: str) -> None:
        """校验一个自定义地址；不合规抛 MODEL_ENDPOINT_NOT_ALLOWED（403），格式错误抛 VALIDATION_FAILED。"""
        parsed = urlsplit(base_url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise GoalflowError(ErrorCode.VALIDATION_FAILED, "自定义地址必须是合法的 http(s) URL")
        if parsed.username or parsed.password:
            raise GoalflowError(ErrorCode.VALIDATION_FAILED, "自定义地址不支持在 URL 中携带账号口令")
        hostname = parsed.hostname.lower()
        port = parsed.port

        if hostname in _LOOPBACK_NAMES or hostname.endswith(".localhost"):
            # 本机名直接进入"必须命中允许列表"分支，不做 DNS 解析（解析结果必然是环回）。
            if self._host_matches_allowlist(hostname, []):
                return
            raise GoalflowError(
                ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED,
                "模型服务地址指向本地/内网，且未在实例允许列表中开放",
            )

        try:
            resolved = self._resolver(hostname)
        except GoalflowError:
            raise
        except OSError as exc:
            # 注入的解析器或底层 DNS 抛 OSError：按"地址不可验证"拒绝，不放行。
            raise GoalflowError(
                ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED,
                "模型服务地址无法解析，已拒绝出站",
            ) from exc

        if port is not None and port not in _ALLOWED_PUBLIC_PORTS:
            # 非标端口只对已在允许列表内的主机开放（本地模型常用自定义端口）；
            # 公共地址的非标端口一律拒绝，防止借 HTTP 探测内网服务。
            if self._host_matches_allowlist(hostname, resolved):
                return
            raise GoalflowError(
                ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED,
                f"端口 {port} 不在公共地址允许范围（80/443/8080/8443）内",
            )

        if _all_public(resolved) or self._host_matches_allowlist(hostname, resolved):
            return

        raise GoalflowError(
            ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED,
            "模型服务地址指向本地/内网，且未在实例允许列表中开放",
        )

    def _host_matches_allowlist(self, hostname: str, resolved: list[str]) -> bool:
        if hostname in self._hostnames:
            return True
        for ip_text in resolved:
            try:
                ip: IpAddress = ipaddress.ip_address(ip_text)
            except ValueError:
                continue
            if any(ip in network for network in self._networks):
                return True
        return False


def validate_outbound_url(base_url: str) -> None:
    """便捷入口：按部署环境（GOALFLOW_MODEL_ENDPOINT_ALLOWLIST）校验一个自定义地址。"""
    OutboundPolicy(get_settings().model_endpoint_allowlist).validate(base_url)
