"""外部 auto-ontology 绑定服务层单元测试：URL/SSRF、令牌、探测与注册库。

所有探测通过 ``httpx.MockTransport`` 注入，不访问真实网络；数据库由
``conftest.isolated_settings`` 隔离到每个用例的 ``tmp_path``。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable

import httpx
import pytest

from app.core.config import settings
from app.db import registry
from app.schemas.auto_ontology import AutoOntologyConfigRequest
from app.schemas.common import AppError
from app.services import auto_ontology

ProbeHandler = Callable[[httpx.Request], httpx.Response]


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "base_url": "http://upstream.test:8080",
        "auth_header": "Authorization",
        "token": None,
        "probe_path": "/api/terms?limit=1",
        "verify_tls": True,
        "timeout_seconds": 2.0,
    }
    config.update(overrides)
    return config


def _probe(handler: ProbeHandler, **overrides: Any) -> dict[str, Any]:
    return auto_ontology.probe_binding(
        _config(**overrides), transport=httpx.MockTransport(handler)
    )


def _assert_app_error(exc: pytest.ExceptionInfo[AppError], status: int, code: str) -> None:
    assert exc.value.status_code == status, exc.value.details
    assert exc.value.code == code, exc.value.message


# --- URL / SSRF 校验 ---

ACCEPTED_URLS: list[tuple[str, str]] = [
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://localhost:3000", "http://localhost:3000"),
    ("http://auto-ontology-frontend:3000", "http://auto-ontology-frontend:3000"),
    ("http://10.1.2.3", "http://10.1.2.3"),
    ("http://192.168.1.10", "http://192.168.1.10"),
    ("http://172.16.0.1", "http://172.16.0.1"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("http://[fd00::1]", "http://[fd00::1]"),
    ("http://[::ffff:127.0.0.1]", "http://[::ffff:127.0.0.1]"),
    ("http://example.com.", "http://example.com"),
    ("http://Example.COM/", "http://example.com"),
    ("http://example.com/base/", "http://example.com/base"),
    ("https://internal.example.com:8443/api", "https://internal.example.com:8443/api"),
]


@pytest.mark.parametrize(("raw", "normalized"), ACCEPTED_URLS)
def test_validate_base_url_accepts_local_and_private_hosts(raw: str, normalized: str) -> None:
    assert auto_ontology.validate_base_url(raw) == normalized


UNSAFE_URLS: list[str] = [
    # Unicode 点号 / 全角数字 / 尾随点
    "http://169。254。169。254",
    "http://169．254．169．254",
    "http://169｡254｡169｡254",
    "http://169․254․169․254",
    "http://169.254.169.254。",
    "http://169.254.169.254.",
    "http://１６９.２５４.１６９.２５４",
    "http://metadata。google。internal",
    # 元数据主机 / IP 与字面 IP 变体
    "http://metadata.google.internal",
    "http://metadata",
    "http://instance-data",
    "http://169.254.169.254",
    "http://169.254.169.254:80/latest/meta-data",
    "http://100.100.100.200",
    "http://192.0.0.192",
    "http://2852039166",
    "http://0xA9FEA9FE",
    "http://0251.0376.0376.0376",
    # IPv6 封装 / NAT64 / 6to4 / Teredo / ISATAP
    "http://[::169.254.169.254]",
    "http://[::ffff:169.254.169.254]",
    "http://[::ffff:0:169.254.169.254]",
    "http://[0:0:0:0:0:ffff:a9fe:a9fe]",
    "http://[64:ff9b::169.254.169.254]",
    "http://[2002:a9fe:a9fe::]",
    "http://[2001:0:a9fe:a9fe::]",
    "http://[2001:db8::5efe:169.254.169.254]",
    # 链路本地 / 未指定 / 组播
    "http://[fe80::1]",
    "http://0.0.0.0",
    "http://[::]",
    "http://224.0.0.1",
    # 协议 / 凭据 / 片段 / 查询串 / 非法字符
    "ftp://example.com",
    "file:///etc/passwd",
    "gopher://example.com",
    "http://user:hunter2@127.0.0.1:8000",
    "http://example.com/#fragment",
    "http://example.com/?q=1",
    "http://exa%20mple.com",
    "http://exa\\mple.com",
    "http://example.com:99999",
    "http://[::1",
    "",
    "https://",
    "http://" + "a" * 600,
]


@pytest.mark.parametrize("payload", UNSAFE_URLS)
def test_validate_base_url_rejects_unsafe_payloads(payload: str) -> None:
    with pytest.raises(AppError) as excinfo:
        auto_ontology.validate_base_url(payload)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_URL_INVALID")


@pytest.mark.xfail(
    strict=True,
    reason="CR-004：NAT64 64:ff9b:1::/48 的 IPv4 提取位置与 RFC 6052 不一致，当前仍被接受",
)
@pytest.mark.parametrize(
    "payload",
    ["http://[64:ff9b:1:6464:64:c800::]", "http://[64:ff9b:1:c000:0:c000::]"],
)
def test_validate_base_url_rejects_local_use_nat64_metadata_ip(payload: str) -> None:
    with pytest.raises(AppError) as excinfo:
        auto_ontology.validate_base_url(payload)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_URL_INVALID")


def test_validate_base_url_enforces_host_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auto_ontology_allowed_hosts", "example.com, API.internal")

    assert auto_ontology.validate_base_url("http://example.com") == "http://example.com"
    assert auto_ontology.validate_base_url("http://api.internal:9000") == (
        "http://api.internal:9000"
    )
    with pytest.raises(AppError) as excinfo:
        auto_ontology.validate_base_url("http://127.0.0.1:8000")
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_URL_INVALID")


# --- Header / 路径 / 令牌 / 超时校验 ---


@pytest.mark.parametrize("name", ["Authorization", "X-Api-Key", "x-custom-1"])
def test_validate_auth_header_accepts_custom_headers(name: str) -> None:
    assert auto_ontology.validate_auth_header(name) == name


@pytest.mark.parametrize(
    "name",
    ["Host", "content-length", "Transfer-Encoding", "connection", "upgrade",
     "Proxy-Authorization", "proxy-connection", "cookie", "te", "trailer"],
)
def test_validate_auth_header_rejects_hop_by_hop_headers(name: str) -> None:
    with pytest.raises(AppError) as excinfo:
        auto_ontology.validate_auth_header(name)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_CONFIG_INVALID")


@pytest.mark.parametrize("name", ["", "-bad", "x api", "a" * 65, "头"])
def test_validate_auth_header_rejects_invalid_names(name: str) -> None:
    with pytest.raises(AppError) as excinfo:
        auto_ontology.validate_auth_header(name)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_CONFIG_INVALID")


@pytest.mark.parametrize("path", ["/api/terms?limit=1", "/health", "/a/b?x=1&y=2"])
def test_normalize_probe_path_accepts_relative_paths(path: str) -> None:
    assert auto_ontology.normalize_probe_path(path) == path


@pytest.mark.parametrize("path", ["", "//evil", "api/terms", "/a\\b", "/a#b", "/a\nb", "x" * 300])
def test_normalize_probe_path_rejects_invalid_paths(path: str) -> None:
    with pytest.raises(AppError) as excinfo:
        auto_ontology.normalize_probe_path(path)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_CONFIG_INVALID")


@pytest.mark.parametrize("token", ["abc", "abc.DEF-_123", "a" * 1024])
def test_normalize_token_accepts_visible_ascii(token: str) -> None:
    assert auto_ontology._normalize_token(token) == token


def test_normalize_token_treats_blank_as_absent() -> None:
    assert auto_ontology._normalize_token(None) is None
    assert auto_ontology._normalize_token("") is None
    assert auto_ontology._normalize_token("   ") is None


@pytest.mark.parametrize("token", ["令牌-中文", "abc def", "abc\tdef", "abc\x01def", "abc\x7fdef", "a" * 1025])
def test_normalize_token_rejects_unsafe_values(token: str) -> None:
    with pytest.raises(AppError) as excinfo:
        auto_ontology._normalize_token(token)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_CONFIG_INVALID")


def test_resolve_request_timeout_defaults_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    assert auto_ontology._resolve_request_timeout(None) == 5.0
    assert auto_ontology._resolve_request_timeout(2.5) == 2.5

    monkeypatch.setattr(settings, "auto_ontology_max_timeout_seconds", 2.0)
    # P2-2 回归：未传超时应取 min(默认 5s, 配置上限)，而不是被 5s 默认值拒绝。
    assert auto_ontology._resolve_request_timeout(None) == 2.0
    assert auto_ontology._resolve_request_timeout(2.0) == 2.0
    with pytest.raises(AppError) as excinfo:
        auto_ontology._resolve_request_timeout(3.0)
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_CONFIG_INVALID")


# --- 探测（httpx.MockTransport，不访问外网） ---


@pytest.mark.parametrize(
    ("body", "json_type", "item_count"),
    [
        (b'[{"id":1},{"id":2}]', "array", 2),
        (b'{"items":[]}', "object", None),
        (b"not-json", "other", None),
        (b"", "other", None),
    ],
)
def test_probe_binding_reports_json_summary(
    body: bytes, json_type: str, item_count: int | None
) -> None:
    result = _probe(
        lambda _request: httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )
    )
    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["latency_ms"] >= 0
    assert result["summary"]["bytes_read"] == len(body)
    assert result["summary"]["truncated"] is False
    assert result["summary"]["json_type"] == json_type
    assert result["summary"]["item_count"] == item_count


def test_probe_binding_sends_bearer_token_with_configured_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    _probe(handler, token="abc123", auth_header="X-Api-Key")
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["X-Api-Key"] == "Bearer abc123"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "finance-ontology-mvp/0.1"


def test_probe_binding_without_token_skips_auth_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    _probe(handler)
    assert "Authorization" not in requests[0].headers


def test_probe_binding_does_not_follow_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    with pytest.raises(AppError) as excinfo:
        _probe(handler)
    _assert_app_error(excinfo, 502, "AUTO_ONTOLOGY_UPSTREAM_ERROR")
    assert excinfo.value.details["status_code"] == 302
    assert "重定向" in excinfo.value.message
    assert len(requests) == 1


@pytest.mark.parametrize("status_code", [401, 403])
def test_probe_binding_maps_auth_failures(status_code: int) -> None:
    with pytest.raises(AppError) as excinfo:
        _probe(lambda _request: httpx.Response(status_code))
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_AUTH_FAILED")
    assert excinfo.value.details["status_code"] == status_code


@pytest.mark.parametrize("status_code", [304, 400, 404, 500, 503])
def test_probe_binding_maps_non_success_status(status_code: int) -> None:
    with pytest.raises(AppError) as excinfo:
        _probe(lambda _request: httpx.Response(status_code))
    _assert_app_error(excinfo, 502, "AUTO_ONTOLOGY_UPSTREAM_ERROR")
    assert excinfo.value.details["status_code"] == status_code


def test_probe_binding_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream", request=request)

    with pytest.raises(AppError) as excinfo:
        _probe(handler, timeout_seconds=1.0)
    _assert_app_error(excinfo, 504, "AUTO_ONTOLOGY_TIMEOUT")
    assert excinfo.value.details["status_code"] is None


def test_probe_binding_maps_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AppError) as excinfo:
        _probe(handler)
    _assert_app_error(excinfo, 502, "AUTO_ONTOLOGY_UPSTREAM_ERROR")
    assert "ConnectError" in excinfo.value.details["error"]


def test_probe_binding_truncates_large_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auto_ontology_max_response_bytes", 10)
    result = _probe(lambda _request: httpx.Response(200, content=b"0123456789abcdef"))
    assert result["summary"]["bytes_read"] == 10
    assert result["summary"]["truncated"] is True


def test_probe_binding_uses_hardened_client_options(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.Client
    captured: dict[str, Any] = {}

    def recording_client(*args: Any, **kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(auto_ontology.httpx, "Client", recording_client)
    _probe(
        lambda _request: httpx.Response(200, json={}),
        verify_tls=False,
        timeout_seconds=3.0,
    )

    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["verify"] is False
    assert isinstance(captured["timeout"], httpx.Timeout)
    assert captured["timeout"].read == 3.0


def test_probe_binding_rejects_non_ascii_token_without_500() -> None:
    with pytest.raises(AppError) as excinfo:
        _probe(lambda _request: httpx.Response(200), token="令牌-中文")
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_CONFIG_INVALID")


def test_probe_binding_maps_invalid_url_without_500() -> None:
    with pytest.raises(AppError) as excinfo:
        _probe(lambda _request: httpx.Response(200), base_url="http://[::1")
    _assert_app_error(excinfo, 422, "AUTO_ONTOLOGY_URL_INVALID")


def test_probe_logs_redact_token_and_query_string(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="finance_ontology.auto_ontology")
    token = "log-probe-token-abc"

    _probe(
        lambda request: httpx.Response(
            200, json={"ok": True}, headers={"content-type": "application/json"}
        ),
        token=token,
    )

    assert token not in caplog.text
    assert "limit=1" not in caplog.text


# --- 注册库（单行表、CRUD、状态投影） ---


def test_registry_table_has_single_row_constraints_and_no_extra_index() -> None:
    registry.init_registry()
    with sqlite3.connect(settings.sources_db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'auto_ontology_binding'"
        ).fetchone()
        indexes = conn.execute("PRAGMA index_list('auto_ontology_binding')").fetchall()

    assert row is not None
    ddl = row[0]
    assert "CHECK (id = 1)" in ddl
    assert "status IN ('unconfigured','disabled','healthy','error')" in ddl
    assert "timeout_seconds > 0 AND timeout_seconds <= 10" in ddl
    assert indexes == []


def test_registry_upsert_preserves_created_at_and_updates_configuration() -> None:
    registry.upsert_auto_ontology_binding(
        {
            "base_url": "http://a.test",
            "enabled": 0,
            "status": "disabled",
            "created_at": "2020-01-01T00:00:00+08:00",
            "updated_at": "2020-01-01T00:00:00+08:00",
        }
    )
    first = registry.get_auto_ontology_binding()
    assert first is not None
    registry.upsert_auto_ontology_binding(
        {"base_url": "http://b.test", "auth_header": "X-Api-Key", "enabled": 0}
    )
    second = registry.get_auto_ontology_binding()

    assert second is not None
    assert second["created_at"] == "2020-01-01T00:00:00+08:00"
    assert second["updated_at"] >= first["updated_at"]
    assert second["base_url"] == "http://b.test"
    assert second["auth_header"] == "X-Api-Key"


def test_registry_update_test_result_only_touches_result_columns() -> None:
    registry.upsert_auto_ontology_binding(
        {
            "base_url": "http://a.test",
            "token_ciphertext": "cipher",
            "enabled": 1,
            "probe_path": "/health",
            "timeout_seconds": 2.0,
            "status": "unconfigured",
        }
    )
    registry.update_auto_ontology_binding_test_result(
        status="error",
        last_error="boom",
        last_tested_at="2026-01-01T00:00:00+08:00",
        last_latency_ms=12,
        last_status_code=401,
    )
    row = registry.get_auto_ontology_binding()

    assert row is not None
    assert (row["status"], row["last_error"], row["last_status_code"]) == ("error", "boom", 401)
    assert (row["last_tested_at"], row["last_latency_ms"]) == ("2026-01-01T00:00:00+08:00", 12)
    assert (row["base_url"], row["token_ciphertext"], row["probe_path"]) == (
        "http://a.test",
        "cipher",
        "/health",
    )


def test_registry_delete_is_idempotent() -> None:
    registry.upsert_auto_ontology_binding({"base_url": "http://a.test"})
    assert registry.delete_auto_ontology_binding() is True
    assert registry.get_auto_ontology_binding() is None
    assert registry.delete_auto_ontology_binding() is False


def test_get_binding_state_defaults_when_unconfigured() -> None:
    state = auto_ontology.get_binding_state()
    assert state["configured"] is False
    assert state["enabled"] is False
    assert state["status"] == "unconfigured"
    assert state["base_url"] is None
    assert state["has_token"] is False
    assert state["timeout_seconds"] == 5.0
    assert state["last_tested_at"] is None


def test_state_projection_keeps_status_and_switch_consistent() -> None:
    registry.upsert_auto_ontology_binding({"base_url": "", "enabled": 1, "status": "healthy"})
    assert auto_ontology.get_binding_state()["status"] == "unconfigured"

    registry.upsert_auto_ontology_binding(
        {"base_url": "http://a.test", "enabled": 0, "status": "healthy"}
    )
    assert auto_ontology.get_binding_state()["status"] == "disabled"

    registry.upsert_auto_ontology_binding(
        {"base_url": "http://a.test", "enabled": 1, "status": "error"}
    )
    assert auto_ontology.get_binding_state()["status"] == "error"

    registry.upsert_auto_ontology_binding(
        {"base_url": "http://a.test", "enabled": 1, "status": "healthy"}
    )
    assert auto_ontology.get_binding_state()["status"] == "healthy"


def test_save_binding_disabled_skips_probe_and_stores_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("enabled=false 时不得探测")

    monkeypatch.setattr(auto_ontology, "probe_binding", fail_probe)
    state = auto_ontology.save_binding(
        AutoOntologyConfigRequest(
            base_url="http://127.0.0.1:8000/",
            token="plain-token",
            enabled=False,
            verify_tls=False,
            probe_path="/health",
            timeout_seconds=2.0,
        )
    )
    row = registry.get_auto_ontology_binding()

    assert state["status"] == "disabled"
    assert state["base_url"] == "http://127.0.0.1:8000"
    assert state["timeout_seconds"] == 2.0
    assert state["has_token"] is True
    assert row is not None
    assert row["token_ciphertext"] and row["token_ciphertext"] != "plain-token"
    assert row["verify_tls"] == 0
