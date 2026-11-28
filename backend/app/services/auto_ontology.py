"""外部 auto-ontology 绑定：URL/SSRF 防护、令牌加密、探测与状态编排。

绑定配置存放在独立的 data/sources.db 单行表中；本模块与本地规则本体生成、
术语查询/编辑链路完全隔离。所有对外错误经 sanitize_error 脱敏，日志不输出
完整 URL、查询串、请求头、令牌或密文。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, sanitize_error
from app.db import registry
from app.schemas.auto_ontology import AutoOntologyConfigRequest, AutoOntologyTestRequest
from app.schemas.common import AppError

logger = logging.getLogger("finance_ontology.auto_ontology")


class _ProbeLogFilter(logging.Filter):
    """探测期间脱敏 httpx 请求日志中的查询串。

    仅在单次探测调用期间挂载/卸载，不修改全局日志级别，避免影响其他 httpx 使用方。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            redacted: list[Any] = []
            changed = False
            for arg in record.args:
                if isinstance(arg, httpx.URL) and arg.query:
                    redacted.append(str(arg).split("?", 1)[0])
                    changed = True
                elif isinstance(arg, str) and "?" in arg:
                    redacted.append(f"{arg.split('?', 1)[0]}?<redacted>")
                    changed = True
                else:
                    redacted.append(arg)
            if changed:
                record.args = tuple(redacted)
        return True


_PROBE_LOG_FILTER = _ProbeLogFilter()

_MAX_TIMEOUT_SECONDS = 10.0
_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_PROBE_PATH = "/api/terms?limit=1"
_DEFAULT_AUTH_HEADER = "Authorization"
_DEFAULT_RESPONSE_BYTES = 65_536
_USER_AGENT = "finance-ontology-mvp/0.1"
_URL_MAX_LENGTH = 512
_PROBE_PATH_MAX_LENGTH = 256
_TOKEN_MAX_LENGTH = 1024

_AUTH_HEADER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_HOSTNAME_PATTERN = re.compile(r"^[a-z0-9._-]+$")

# IDNA 归一化前显式处理 Unicode 点号变体，避免 httpx 归一化后落到不同主机。
_UNICODE_DOTS = ("。", "．", "｡")

# 逐跳/敏感头禁止作为令牌载体，避免请求走私或凭据被转发。
_BLOCKED_AUTH_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "proxy-authorization",
        "proxy-connection",
        "cookie",
        "te",
        "trailer",
    }
)

# 常见云元数据主机/IP；回环与私网地址允许（服务常部署在本机或私有网络）。
_METADATA_HOSTS = frozenset({"metadata", "metadata.google.internal", "instance-data"})
_METADATA_IPS = frozenset({"169.254.169.254", "100.100.100.200", "192.0.0.192"})
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _invalid_url(message: str) -> AppError:
    return AppError(422, "AUTO_ONTOLOGY_URL_INVALID", message)


def _invalid_config(message: str) -> AppError:
    return AppError(422, "AUTO_ONTOLOGY_CONFIG_INVALID", message)


def _safe_host(base_url: str) -> str:
    """仅用于日志的主机名，绝不输出完整 URL。"""
    try:
        return (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return ""


def _embedded_ipv4_addresses(value: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """提取 IPv6 转换/隧道形式中嵌入的 IPv4 地址。

    覆盖 ipv4_mapped、IPv4-compatible ::/96、IPv4-translated ::ffff:0:0/96、
    6to4 2002::/16、NAT64 64:ff9b::/96 与 64:ff9b:1::/48、Teredo 2001:0000::/32
    以及 ISATAP；未知形式不猜测，由调用方按无法判定处理。
    """
    packed = value.packed
    candidates: list[ipaddress.IPv4Address] = []
    if value.ipv4_mapped is not None:
        candidates.append(value.ipv4_mapped)
    # IPv4-compatible ::/96
    if packed[:12] == b"\x00" * 12:
        candidates.append(ipaddress.IPv4Address(packed[12:]))
    # IPv4-translated ::ffff:0:0/96（ipaddress 未提供现成属性）
    if packed[:8] == b"\x00" * 8 and packed[8:10] == b"\xff\xff":
        candidates.append(ipaddress.IPv4Address(packed[12:]))
    # 6to4 2002::/16
    if packed[:2] == b"\x20\x02":
        candidates.append(ipaddress.IPv4Address(packed[2:6]))
    # NAT64 well-known 64:ff9b::/96 与 local-use 64:ff9b:1::/48
    if packed[:12] == b"\x00\x64\xff\x9b" + b"\x00" * 8:
        candidates.append(ipaddress.IPv4Address(packed[12:]))
    if packed[:6] == b"\x00\x64\xff\x9b\x00\x01":
        candidates.append(ipaddress.IPv4Address(packed[6:10]))
    # Teredo 2001:0000::/32：服务端 IPv4 + 取反编码的客户端 IPv4
    if packed[:4] == b"\x20\x01\x00\x00":
        candidates.append(ipaddress.IPv4Address(packed[4:8]))
        candidates.append(ipaddress.IPv4Address(bytes(byte ^ 0xFF for byte in packed[12:])))
    # ISATAP：接口标识符 0000:5efe 后嵌入 IPv4（标记位于字节 10-11）
    if packed[10:12] == b"\x5e\xfe":
        candidates.append(ipaddress.IPv4Address(packed[12:]))
    return candidates


def _is_blocked_ip(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [value]
    if isinstance(value, ipaddress.IPv6Address):
        candidates.extend(_embedded_ipv4_addresses(value))
    for candidate in candidates:
        if str(candidate) in _METADATA_IPS:
            return True
        if candidate.is_link_local or candidate.is_unspecified or candidate.is_multicast:
            return True
    return False


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """解析字面 IP；兼容十进制/八进制/十六进制等 inet_aton 形式，不做 DNS 解析。"""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        # inet_aton 仅解析数字形式，不触发 DNS；用于拦截 2852039166 之类的元数据地址变体。
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def _normalize_host(host: str) -> str:
    """把主机名归一化为 ASCII 并做保守校验。

    显式处理 Unicode 点号与尾随点，再执行 IDNA/ASCII 归一化；归一化失败、
    结果仍含非 ASCII 或无法安全判定的主机形式一律拒绝。
    """
    candidate = (host or "").strip().lower()
    for dot in _UNICODE_DOTS:
        candidate = candidate.replace(dot, ".")
    candidate = candidate.rstrip(".")
    if not candidate:
        raise _invalid_url("服务地址缺少主机名")
    try:
        normalized = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid_url("服务地址主机名不合法") from exc
    normalized = normalized.lower().rstrip(".")
    if not normalized:
        raise _invalid_url("服务地址缺少主机名")
    if any(ord(char) < 33 or ord(char) > 126 for char in normalized):
        raise _invalid_url("服务地址主机名必须是 ASCII")
    if "%" in normalized or "\\" in normalized or "/" in normalized:
        raise _invalid_url("服务地址主机名包含非法字符")
    if ":" in normalized:
        # 含冒号只能按 IPv6 字面量处理；无法解析的混合形式直接拒绝。
        try:
            ipaddress.IPv6Address(normalized)
        except ValueError as exc:
            raise _invalid_url("IPv6 地址格式不合法") from exc
    elif not _HOSTNAME_PATTERN.fullmatch(normalized):
        raise _invalid_url("服务地址主机名包含非法字符")
    return normalized


def validate_base_url(base_url: str) -> str:
    """校验并规范化服务基础地址；禁止凭据、片段、查询串与元数据地址。"""
    raw = (base_url or "").strip()
    if not raw or len(raw) > _URL_MAX_LENGTH:
        raise _invalid_url(f"服务地址长度需为 1-{_URL_MAX_LENGTH} 字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise _invalid_url("服务地址包含控制字符")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise _invalid_url("服务地址格式不合法") from exc

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise _invalid_url("服务地址仅支持 http/https")
    if not parts.netloc or not parts.hostname:
        raise _invalid_url("服务地址缺少主机名")
    if parts.username is not None or parts.password is not None:
        raise _invalid_url("服务地址不能包含用户名或密码")
    if parts.fragment:
        raise _invalid_url("服务地址不能包含片段")
    if parts.query:
        raise _invalid_url("服务地址不能包含查询串，请填写在探测路径中")

    host = _normalize_host(parts.hostname)
    if host in _METADATA_HOSTS:
        raise _invalid_url("禁止访问云元数据地址")

    literal_ip = _parse_ip_literal(host)
    if literal_ip is not None and _is_blocked_ip(literal_ip):
        raise _invalid_url("禁止访问链路本地、未指定或元数据地址")

    allowed_hosts = settings.allowed_auto_ontology_hosts
    if allowed_hosts and host not in allowed_hosts:
        raise _invalid_url("目标主机不在允许列表中")

    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parts.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def validate_auth_header(auth_header: str) -> str:
    """校验自定义认证 Header 名，拒绝逐跳/敏感头。"""
    name = (auth_header or "").strip()
    if not _AUTH_HEADER_PATTERN.fullmatch(name):
        raise _invalid_config("认证 Header 名不合法")
    if name.lower() in _BLOCKED_AUTH_HEADERS:
        raise _invalid_config("认证 Header 名不允许使用逐跳或敏感头")
    return name


def normalize_probe_path(probe_path: str) -> str:
    """探测路径必须是以单个 / 开头的相对路径，允许查询串。"""
    path = (probe_path or "").strip()
    if not path or len(path) > _PROBE_PATH_MAX_LENGTH:
        raise _invalid_config(f"探测路径长度需为 1-{_PROBE_PATH_MAX_LENGTH} 字符")
    if not path.startswith("/") or path.startswith("//"):
        raise _invalid_config("探测路径必须以单个 / 开头")
    if any(char in path for char in ("\r", "\n", "\\", "#")):
        raise _invalid_config("探测路径包含非法字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise _invalid_config("探测路径包含控制字符")
    return path


def build_probe_url(base_url: str, probe_path: str) -> str:
    """拼接探测地址；base_url 已在保存时规范化。"""
    return f"{base_url.rstrip('/')}{normalize_probe_path(probe_path)}"


def _normalize_token(token: str | None) -> str | None:
    text = (token or "").strip()
    if not text:
        return None
    if len(text) > _TOKEN_MAX_LENGTH:
        raise _invalid_config(f"令牌长度不能超过 {_TOKEN_MAX_LENGTH} 字符")
    # Header 值必须可安全编码为 ASCII；拒绝控制字符、空格与非 ASCII 字符。
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in text):
        raise _invalid_config("令牌只能包含可见 ASCII 字符")
    return text


def _timeout_limit() -> float:
    try:
        configured = float(settings.auto_ontology_max_timeout_seconds)
    except (TypeError, ValueError):
        configured = _DEFAULT_TIMEOUT_SECONDS
    if configured <= 0:
        configured = _DEFAULT_TIMEOUT_SECONDS
    return min(configured, _MAX_TIMEOUT_SECONDS)


def _resolve_request_timeout(value: float | None) -> float:
    """未传时取 min(默认值, 上限)；显式值超过配置上限时拒绝，不静默截断。"""
    limit = _timeout_limit()
    if value is None:
        return min(_DEFAULT_TIMEOUT_SECONDS, limit)
    requested = float(value)
    if requested <= 0:
        raise _invalid_config("超时时间必须大于 0")
    if requested > limit:
        raise _invalid_config(f"超时时间不能超过 {limit:g} 秒")
    return requested


def _effective_stored_timeout(value: Any) -> float:
    """已保存值只受当前上限约束，避免配置收紧后无法测试。"""
    try:
        stored = float(value)
    except (TypeError, ValueError):
        stored = _DEFAULT_TIMEOUT_SECONDS
    if stored <= 0:
        stored = _DEFAULT_TIMEOUT_SECONDS
    return min(stored, _timeout_limit())


def _response_byte_limit() -> int:
    try:
        limit = int(settings.auto_ontology_max_response_bytes)
    except (TypeError, ValueError):
        limit = _DEFAULT_RESPONSE_BYTES
    return max(1, limit)


def _detail_int(exc: AppError, key: str) -> int | None:
    value = (exc.details or {}).get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_capped(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    """流式读取响应体，最多 limit 字节；超出仅标记 truncated。"""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        remaining = limit - total
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            total += remaining
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def _json_summary(body: bytes) -> tuple[str, int | None]:
    """仅返回 JSON 顶层类型；数组附带元素数量，不回显正文。"""
    if not body:
        return "other", None
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return "other", None
    if isinstance(parsed, dict):
        return "object", None
    if isinstance(parsed, list):
        return "array", len(parsed)
    return "other", None


def _raise_for_upstream_status(status_code: int, latency_ms: int) -> None:
    details = {"latency_ms": latency_ms, "status_code": status_code}
    if status_code in _REDIRECT_STATUS_CODES:
        raise AppError(
            502,
            "AUTO_ONTOLOGY_UPSTREAM_ERROR",
            "上游返回重定向，已按安全策略拒绝",
            details,
        )
    if status_code in (401, 403):
        raise AppError(
            422,
            "AUTO_ONTOLOGY_AUTH_FAILED",
            "上游鉴权失败，请检查令牌与 Header",
            details,
        )
    raise AppError(
        502,
        "AUTO_ONTOLOGY_UPSTREAM_ERROR",
        f"上游服务返回 HTTP {status_code}",
        details,
    )


def probe_binding(
    config: dict[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """对上游执行单次 GET 探测；2xx 返回摘要，其余抛 AppError。

    transport 仅用于测试注入 httpx.MockTransport；生产调用传 None。
    禁止重定向、忽略代理环境变量、限制响应体大小、单次请求不重试。
    """
    base_url = str(config.get("base_url") or "")
    probe_path = str(config.get("probe_path") or _DEFAULT_PROBE_PATH)
    url = build_probe_url(base_url, probe_path)
    auth_header = str(config.get("auth_header") or _DEFAULT_AUTH_HEADER)
    token = config.get("token")
    timeout_seconds = float(config.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS)
    verify_tls = bool(config.get("verify_tls", True))

    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if token:
        headers[auth_header] = f"Bearer {token}"

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.addFilter(_PROBE_LOG_FILTER)
    started = time.perf_counter()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            verify=verify_tls,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream("GET", url, headers=headers) as response:
                if response.is_success:
                    body, truncated = _read_capped(response, _response_byte_limit())
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    json_type, item_count = _json_summary(body)
                    logger.info(
                        "auto-ontology probe ok host=%s status=%s latency_ms=%s",
                        _safe_host(base_url),
                        response.status_code,
                        latency_ms,
                    )
                    return {
                        "success": True,
                        "latency_ms": latency_ms,
                        "status_code": response.status_code,
                        "summary": {
                            "content_type": response.headers.get("content-type", ""),
                            "bytes_read": len(body),
                            "truncated": truncated,
                            "json_type": json_type,
                            "item_count": item_count,
                        },
                    }
                latency_ms = int((time.perf_counter() - started) * 1000)
                _raise_for_upstream_status(response.status_code, latency_ms)
    except httpx.TimeoutException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "auto-ontology probe timeout host=%s latency_ms=%s",
            _safe_host(base_url),
            latency_ms,
        )
        raise AppError(
            504,
            "AUTO_ONTOLOGY_TIMEOUT",
            "连接超时",
            {"latency_ms": latency_ms, "status_code": None},
        ) from exc
    except httpx.InvalidURL as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "auto-ontology probe invalid url host=%s error_type=%s",
            _safe_host(base_url),
            type(exc).__name__,
        )
        raise AppError(
            422,
            "AUTO_ONTOLOGY_URL_INVALID",
            "服务地址无法被 HTTP 客户端解析",
            {"latency_ms": latency_ms, "status_code": None},
        ) from exc
    except UnicodeError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "auto-ontology probe encoding error host=%s error_type=%s",
            _safe_host(base_url),
            type(exc).__name__,
        )
        raise AppError(
            422,
            "AUTO_ONTOLOGY_CONFIG_INVALID",
            "令牌或请求头包含非法字符",
            {"latency_ms": latency_ms, "status_code": None},
        ) from exc
    except ValueError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "auto-ontology probe config error host=%s error_type=%s",
            _safe_host(base_url),
            type(exc).__name__,
        )
        raise AppError(
            422,
            "AUTO_ONTOLOGY_CONFIG_INVALID",
            "探测参数不合法",
            {"latency_ms": latency_ms, "status_code": None},
        ) from exc
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "auto-ontology probe error host=%s latency_ms=%s error_type=%s",
            _safe_host(base_url),
            latency_ms,
            type(exc).__name__,
        )
        raise AppError(
            502,
            "AUTO_ONTOLOGY_UPSTREAM_ERROR",
            "上游服务不可达",
            {
                "latency_ms": latency_ms,
                "status_code": None,
                "error": f"上游连接失败（{type(exc).__name__}）",
            },
        ) from exc
    finally:
        httpx_logger.removeFilter(_PROBE_LOG_FILTER)


def _decrypt_saved_token(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return decrypt_secret(ciphertext)
    except AppError as exc:
        if exc.code == "SOURCE_SECRET_INVALID":
            raise AppError(
                422,
                "AUTO_ONTOLOGY_SECRET_INVALID",
                "令牌无法解密，请重新设置",
            ) from exc
        raise


def _row_to_state(row: dict[str, Any] | None) -> dict[str, Any]:
    """把单行配置投影为接口状态；状态与启用开关保持自洽。"""
    if row is None:
        return {
            "configured": False,
            "enabled": False,
            "status": "unconfigured",
            "base_url": None,
            "auth_type": "bearer",
            "auth_header": _DEFAULT_AUTH_HEADER,
            "probe_path": _DEFAULT_PROBE_PATH,
            "verify_tls": True,
            "timeout_seconds": _resolve_request_timeout(None),
            "has_token": False,
            "last_tested_at": None,
            "last_error": None,
            "last_latency_ms": None,
            "last_status_code": None,
            "updated_at": None,
        }

    base_url = str(row.get("base_url") or "").strip()
    configured = bool(base_url)
    enabled = bool(row.get("enabled")) and configured
    persisted_status = str(row.get("status") or "unconfigured")
    if not configured:
        status = "unconfigured"
    elif not enabled:
        status = "disabled"
    elif persisted_status == "healthy":
        status = "healthy"
    else:
        status = "error"
    return {
        "configured": configured,
        "enabled": enabled,
        "status": status,
        "base_url": base_url or None,
        "auth_type": "bearer",
        "auth_header": str(row.get("auth_header") or _DEFAULT_AUTH_HEADER),
        "probe_path": str(row.get("probe_path") or _DEFAULT_PROBE_PATH),
        "verify_tls": bool(row.get("verify_tls", 1)),
        "timeout_seconds": float(row.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS),
        "has_token": bool(row.get("token_ciphertext")),
        "last_tested_at": row.get("last_tested_at"),
        "last_error": row.get("last_error"),
        "last_latency_ms": row.get("last_latency_ms"),
        "last_status_code": row.get("last_status_code"),
        "updated_at": row.get("updated_at"),
    }


def get_binding_state() -> dict[str, Any]:
    """纯读接口：不解密令牌，不触发上游请求。"""
    return _row_to_state(registry.get_auto_ontology_binding())


def save_binding(payload: AutoOntologyConfigRequest) -> dict[str, Any]:
    """保存全量配置；启用时先探测再一次性落库，探测失败仍保存并标记 error。"""
    base_url = validate_base_url(payload.base_url)
    auth_header = validate_auth_header(payload.auth_header)
    probe_path = normalize_probe_path(payload.probe_path)
    timeout_seconds = _resolve_request_timeout(payload.timeout_seconds)
    token = _normalize_token(payload.token)
    if token and payload.clear_token:
        raise _invalid_config("token 与 clear_token 不能同时设置")

    existing = registry.get_auto_ontology_binding() or {}
    saved_base_url = str(existing.get("base_url") or "").strip()
    saved_ciphertext = existing.get("token_ciphertext")

    saved_token: str | None = None
    if token:
        ciphertext = encrypt_secret(token)
        saved_token = token
    elif payload.clear_token:
        ciphertext = None
    else:
        if saved_ciphertext and saved_base_url and saved_base_url != base_url:
            raise _invalid_config("地址变更时请重新填写令牌或显式清除")
        ciphertext = saved_ciphertext
        # 停用保存不需要探测，也就无需解密；避免密钥变更后无法停用绑定。
        if ciphertext and payload.enabled:
            saved_token = _decrypt_saved_token(ciphertext)

    fields: dict[str, Any] = {
        "base_url": base_url,
        "auth_header": auth_header,
        "token_ciphertext": ciphertext,
        "enabled": 1 if payload.enabled else 0,
        "verify_tls": 1 if payload.verify_tls else 0,
        "probe_path": probe_path,
        "timeout_seconds": timeout_seconds,
    }

    if not payload.enabled:
        fields["status"] = "disabled"
        for key in ("last_error", "last_tested_at", "last_latency_ms", "last_status_code"):
            fields[key] = existing.get(key)
        registry.upsert_auto_ontology_binding(fields)
        logger.info("auto-ontology binding saved disabled host=%s", _safe_host(base_url))
        return get_binding_state()

    config = {
        "base_url": base_url,
        "auth_header": auth_header,
        "token": saved_token,
        "probe_path": probe_path,
        "verify_tls": payload.verify_tls,
        "timeout_seconds": timeout_seconds,
    }
    tested_at = _now()
    try:
        result = probe_binding(config)
    except AppError as exc:
        fields["status"] = "error"
        fields["last_error"] = sanitize_error(
            exc.message, [saved_token] if saved_token else []
        )
        fields["last_tested_at"] = tested_at
        fields["last_latency_ms"] = _detail_int(exc, "latency_ms")
        fields["last_status_code"] = _detail_int(exc, "status_code")
        registry.upsert_auto_ontology_binding(fields)
        logger.info(
            "auto-ontology binding saved error host=%s code=%s latency_ms=%s",
            _safe_host(base_url),
            exc.code,
            fields["last_latency_ms"],
        )
        return get_binding_state()

    fields["status"] = "healthy"
    fields["last_error"] = None
    fields["last_tested_at"] = tested_at
    fields["last_latency_ms"] = result["latency_ms"]
    fields["last_status_code"] = result["status_code"]
    registry.upsert_auto_ontology_binding(fields)
    logger.info(
        "auto-ontology binding saved healthy host=%s latency_ms=%s",
        _safe_host(base_url),
        result["latency_ms"],
    )
    return get_binding_state()


def _probe_with_error_details(
    config: dict[str, Any],
    *,
    persisted: bool,
) -> dict[str, Any]:
    token = config.get("token")
    secrets = [str(token)] if token else []
    try:
        return probe_binding(config)
    except AppError as exc:
        details = dict(exc.details or {})
        detail_error = str(details.get("error") or exc.message)
        details["persisted"] = persisted
        details["error"] = sanitize_error(detail_error, secrets)
        raise AppError(exc.status_code, exc.code, exc.message, details) from exc


def _test_form_binding(payload: AutoOntologyTestRequest) -> dict[str, Any]:
    """携带未保存参数的表单测试：只探测，绝不写配置或状态。"""
    existing = registry.get_auto_ontology_binding() or {}
    saved_base_url = str(existing.get("base_url") or "").strip()

    raw_base_url = payload.base_url if payload.base_url is not None else saved_base_url
    if not raw_base_url:
        raise AppError(
            409,
            "AUTO_ONTOLOGY_NOT_CONFIGURED",
            "尚未配置外部本体服务，请先填写服务地址",
        )
    base_url = validate_base_url(raw_base_url)
    auth_header = validate_auth_header(
        payload.auth_header or existing.get("auth_header") or _DEFAULT_AUTH_HEADER
    )
    probe_path = normalize_probe_path(
        payload.probe_path or existing.get("probe_path") or _DEFAULT_PROBE_PATH
    )
    if payload.timeout_seconds is not None:
        timeout_seconds = _resolve_request_timeout(payload.timeout_seconds)
    elif existing:
        timeout_seconds = _effective_stored_timeout(existing.get("timeout_seconds"))
    else:
        timeout_seconds = _resolve_request_timeout(None)
    verify_tls = (
        payload.verify_tls
        if payload.verify_tls is not None
        else bool(existing.get("verify_tls", 1))
    )

    token = _normalize_token(payload.token)
    if token is None:
        saved_ciphertext = existing.get("token_ciphertext")
        if saved_ciphertext and saved_base_url and saved_base_url == base_url:
            try:
                token = _decrypt_saved_token(saved_ciphertext)
            except AppError as exc:
                details = dict(exc.details or {})
                details.update(
                    {"persisted": False, "error": sanitize_error(exc.message)}
                )
                raise AppError(exc.status_code, exc.code, exc.message, details) from exc

    config = {
        "base_url": base_url,
        "auth_header": auth_header,
        "token": token,
        "probe_path": probe_path,
        "verify_tls": verify_tls,
        "timeout_seconds": timeout_seconds,
    }
    result = _probe_with_error_details(config, persisted=False)
    return {**result, "persisted": False, "message": "连接正常"}


def _test_saved_binding() -> dict[str, Any]:
    """使用已保存配置测试，成功/失败都更新 status 与 last_*。"""
    existing = registry.get_auto_ontology_binding()
    saved_base_url = str((existing or {}).get("base_url") or "").strip()
    if existing is None or not saved_base_url:
        raise AppError(409, "AUTO_ONTOLOGY_NOT_CONFIGURED", "尚未配置外部本体服务")

    base_url = validate_base_url(saved_base_url)
    auth_header = validate_auth_header(
        str(existing.get("auth_header") or _DEFAULT_AUTH_HEADER)
    )
    probe_path = normalize_probe_path(
        str(existing.get("probe_path") or _DEFAULT_PROBE_PATH)
    )
    timeout_seconds = _effective_stored_timeout(existing.get("timeout_seconds"))
    verify_tls = bool(existing.get("verify_tls", 1))

    try:
        token = _decrypt_saved_token(existing.get("token_ciphertext"))
    except AppError as exc:
        persisted = _persist_test_failure(
            status="error",
            message=exc.message,
            tested_at=_now(),
            latency_ms=None,
            status_code=None,
        )
        details = dict(exc.details or {})
        details.update({"persisted": persisted, "error": sanitize_error(exc.message)})
        if not persisted:
            details["persist_error"] = "测试结果写入失败"
        raise AppError(exc.status_code, exc.code, exc.message, details) from exc

    config = {
        "base_url": base_url,
        "auth_header": auth_header,
        "token": token,
        "probe_path": probe_path,
        "verify_tls": verify_tls,
        "timeout_seconds": timeout_seconds,
    }
    tested_at = _now()
    try:
        result = probe_binding(config)
    except AppError as exc:
        sanitized = sanitize_error(exc.message, [token] if token else [])
        persisted = _persist_test_failure(
            status="error",
            message=sanitized,
            tested_at=tested_at,
            latency_ms=_detail_int(exc, "latency_ms"),
            status_code=_detail_int(exc, "status_code"),
        )
        details = dict(exc.details or {})
        details.update({"persisted": persisted, "error": sanitized})
        if not persisted:
            details["persist_error"] = "测试结果写入失败"
        raise AppError(exc.status_code, exc.code, exc.message, details) from exc

    registry.update_auto_ontology_binding_test_result(
        status="healthy",
        last_error=None,
        last_tested_at=tested_at,
        last_latency_ms=result["latency_ms"],
        last_status_code=result["status_code"],
    )
    logger.info(
        "auto-ontology saved test ok host=%s latency_ms=%s",
        _safe_host(base_url),
        result["latency_ms"],
    )
    return {**result, "persisted": True, "message": "连接正常"}


def _persist_test_failure(
    *,
    status: str,
    message: str,
    tested_at: str,
    latency_ms: int | None,
    status_code: int | None,
) -> bool:
    """落库测试失败结果，返回真实持久化结果；写失败不掩盖上游错误。"""
    try:
        registry.update_auto_ontology_binding_test_result(
            status=status,
            last_error=message,
            last_tested_at=tested_at,
            last_latency_ms=latency_ms,
            last_status_code=status_code,
        )
    except Exception:  # noqa: BLE001 - 落库失败不掩盖上游错误
        logger.exception("failed to persist auto-ontology test result")
        return False
    return True


def test_binding(payload: AutoOntologyTestRequest) -> dict[str, Any]:
    """有覆盖字段 → 表单测试不落库；无覆盖 → 测试已保存配置并更新状态。"""
    overrides = payload.model_dump(exclude_unset=True)
    if overrides:
        return _test_form_binding(payload)
    return _test_saved_binding()


def clear_binding() -> dict[str, Any]:
    """删除单行绑定配置（含令牌密文）；重复删除幂等。"""
    deleted = registry.delete_auto_ontology_binding()
    logger.info("auto-ontology binding cleared deleted=%s", deleted)
    return {"deleted": deleted}
