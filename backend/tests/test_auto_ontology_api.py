"""外部 auto-ontology 绑定 API 测试：权限、状态机、令牌安全与既有链路回归。

探测统一通过 ``httpx.MockTransport`` 注入，不访问真实外网；注册库由
``conftest.isolated_settings`` 隔离到 ``tmp_path``。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import registry
from app.services import auto_ontology
from tests.support import ask, error_data, headers, ok_data

SERVICE_URL = "/api/ontology/service"
TEST_URL = "/api/ontology/service/test"
BASE_URL = "http://127.0.0.1:8000"
SAVED_TOKEN = "saved-token-for-tests"
FORM_TOKEN = "form-token-for-tests"
FORM_OVERRIDE_TOKEN = "form-override-token"
FORM_LOG_TOKEN = "form-log-token-for-tests"
ProbeHandler = Callable[[httpx.Request], httpx.Response]


def _put(client: TestClient, **overrides: Any) -> httpx.Response:
    body: dict[str, Any] = {"base_url": BASE_URL, "enabled": False}
    body.update(overrides)
    return client.put(SERVICE_URL, json=body, headers=headers("admin"))


def _install_probe(monkeypatch: pytest.MonkeyPatch, handler: ProbeHandler) -> list[httpx.Request]:
    """把服务层 probe_binding 替换为注入 MockTransport 的版本，返回请求记录。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    original = auto_ontology.probe_binding

    def fake_probe(config: dict[str, Any]) -> dict[str, Any]:
        return original(config, transport=transport)

    monkeypatch.setattr(auto_ontology, "probe_binding", fake_probe)
    return calls


def _json_handler(body: Any = None, status: int = 200) -> ProbeHandler:
    payload = [{"term_code": "revenue"}] if body is None else body
    return lambda _request: httpx.Response(
        status, json=payload, headers={"content-type": "application/json"}
    )


def _status_handler(status: int) -> ProbeHandler:
    return lambda _request: httpx.Response(status)


def _redirect_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})


def _timeout_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow upstream", request=request)


def _connect_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _assert_no_sensitive_error_keys(payload: Any) -> None:
    """递归确认 422 响应不再回显 input/ctx/url（CR-002）。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in {"input", "ctx", "url"}, payload
            _assert_no_sensitive_error_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_sensitive_error_keys(item)


def _saved_row() -> dict[str, Any] | None:
    return registry.get_auto_ontology_binding()


# --- 读取与权限 ---


@pytest.mark.parametrize("role", ["admin", "analyst", "viewer"])
def test_get_unconfigured_state_is_readable_for_every_role(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    def fail_probe(config: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("GET 不应触发上游探测")

    monkeypatch.setattr(auto_ontology, "probe_binding", fail_probe)
    data = ok_data(client.get(SERVICE_URL, headers=headers(role)))

    assert data["configured"] is False
    assert data["enabled"] is False
    assert data["status"] == "unconfigured"
    assert data["base_url"] is None
    assert data["auth_type"] == "bearer"
    assert data["has_token"] is False
    assert data["last_tested_at"] is None
    assert data["timeout_seconds"] == 5.0


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_non_admin_cannot_write_test_or_clear(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    def fail_probe(config: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("非 admin 请求不得触发探测")

    monkeypatch.setattr(auto_ontology, "probe_binding", fail_probe)
    user_headers = headers(role)
    responses = [
        client.put(SERVICE_URL, json={"base_url": BASE_URL, "enabled": False}, headers=user_headers),
        client.delete(SERVICE_URL, headers=user_headers),
        client.post(TEST_URL, json={"base_url": BASE_URL}, headers=user_headers),
    ]
    for response in responses:
        assert response.status_code == 403, response.text
        assert error_data(response)["code"] == "AUTO_ONTOLOGY_FORBIDDEN"

    assert ok_data(client.get(SERVICE_URL, headers=headers("admin")))["configured"] is False
    assert _saved_row() is None


# --- 保存、令牌与 GET 安全 ---


def test_put_disabled_saves_configuration_without_probe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_probe(config: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("enabled=false 时不得探测")

    monkeypatch.setattr(auto_ontology, "probe_binding", fail_probe)
    data = ok_data(_put(client, probe_path="/health", verify_tls=False, timeout_seconds=2.0))

    assert data["configured"] is True
    assert data["enabled"] is False
    assert data["status"] == "disabled"
    assert data["base_url"] == BASE_URL
    assert data["probe_path"] == "/health"
    assert data["verify_tls"] is False
    assert data["timeout_seconds"] == 2.0
    assert data["has_token"] is False
    row = _saved_row()
    assert row is not None
    assert (row["enabled"], row["verify_tls"], row["status"]) == (0, 0, "disabled")


def test_put_stores_token_ciphertext_and_get_only_returns_has_token(client: TestClient) -> None:
    plaintext = "s3cr3t-token-123"
    data = ok_data(_put(client, token=plaintext))

    row = _saved_row()
    assert row is not None
    ciphertext = row["token_ciphertext"]
    assert ciphertext and ciphertext != plaintext
    assert ciphertext.startswith("gAAAA")
    assert data["has_token"] is True
    assert "token" not in data and "token_ciphertext" not in data

    response = client.get(SERVICE_URL, headers=headers("admin"))
    body = ok_data(response)
    assert body["has_token"] is True
    assert "token" not in body and "token_ciphertext" not in body
    assert plaintext not in response.text
    assert ciphertext not in response.text

    openapi = client.get("/openapi.json")
    assert "token_ciphertext" not in openapi.text


def test_put_blank_token_preserves_saved_ciphertext(client: TestClient) -> None:
    ok_data(_put(client, token="first-token"))
    first = _saved_row()
    assert first is not None

    data = ok_data(_put(client, token=""))
    second = _saved_row()

    assert second is not None
    assert second["token_ciphertext"] == first["token_ciphertext"]
    assert data["has_token"] is True


def test_put_clear_token_removes_ciphertext(client: TestClient) -> None:
    ok_data(_put(client, token="first-token"))
    data = ok_data(_put(client, clear_token=True))
    row = _saved_row()

    assert data["has_token"] is False
    assert row is not None and row["token_ciphertext"] is None


def test_put_token_and_clear_token_conflict_is_rejected(client: TestClient) -> None:
    response = _put(client, token="first-token", clear_token=True)
    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_CONFIG_INVALID"
    assert _saved_row() is None


def test_put_address_change_requires_new_token(client: TestClient) -> None:
    ok_data(_put(client, token="first-token"))
    first = _saved_row()
    assert first is not None

    response = _put(client, base_url="http://127.0.0.2:8000")
    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_CONFIG_INVALID"
    assert _saved_row()["base_url"] == BASE_URL

    data = ok_data(_put(client, base_url="http://127.0.0.2:8000", token="second-token"))
    assert data["base_url"] == "http://127.0.0.2:8000"
    assert _saved_row()["token_ciphertext"] != first["token_ciphertext"]


# --- 启用保存的探测与状态机 ---


def test_put_enabled_true_probes_before_persisting_and_marks_healthy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_rows: list[dict[str, Any] | None] = []
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_rows.append(_saved_row())
        requests.append(request)
        return httpx.Response(200, json=[{"term_code": "revenue"}], headers={"content-type": "application/json"})

    _install_probe(monkeypatch, handler)
    data = ok_data(_put(client, token="probe-token", enabled=True, timeout_seconds=2.0))

    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer probe-token"
    assert seen_rows == [None]
    assert data["status"] == "healthy"
    assert data["enabled"] is True
    assert data["last_status_code"] == 200
    assert data["last_tested_at"]
    assert data["last_latency_ms"] is not None
    assert data["last_error"] is None

    row = _saved_row()
    assert row is not None
    assert row["status"] == "healthy"
    assert row["token_ciphertext"]


@pytest.mark.parametrize(
    ("handler", "expected_status_code", "expected_error"),
    [
        pytest.param(_status_handler(401), 401, "鉴权失败", id="401"),
        pytest.param(_status_handler(403), 403, "鉴权失败", id="403"),
        pytest.param(_status_handler(500), 500, "HTTP 500", id="500"),
        pytest.param(_redirect_handler, 302, "重定向", id="302"),
        pytest.param(_timeout_handler, None, "连接超时", id="timeout"),
        pytest.param(_connect_handler, None, "上游服务不可达", id="connect-error"),
    ],
)
def test_put_enabled_true_probe_failure_still_saves_error_state(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    handler: ProbeHandler,
    expected_status_code: int | None,
    expected_error: str,
) -> None:
    calls = _install_probe(monkeypatch, handler)
    response = _put(client, token="probe-token", enabled=True)
    data = ok_data(response)

    assert len(calls) == 1
    assert data["configured"] is True
    assert data["enabled"] is True
    assert data["status"] == "error"
    assert data["last_status_code"] == expected_status_code
    assert expected_error in (data["last_error"] or "")
    assert data["last_tested_at"]
    assert "probe-token" not in response.text

    row = _saved_row()
    assert row is not None
    assert row["status"] == "error"
    assert row["base_url"] == BASE_URL
    assert row["token_ciphertext"]


def test_put_disabled_keeps_previous_test_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_probe(monkeypatch, _json_handler())
    healthy = ok_data(_put(client, token="probe-token", enabled=True))

    data = ok_data(_put(client, enabled=False))

    assert data["status"] == "disabled"
    assert data["last_tested_at"] == healthy["last_tested_at"]
    assert data["last_status_code"] == 200
    assert data["last_latency_ms"] == healthy["last_latency_ms"]


# --- 校验错误与 URL / 令牌安全 ---


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"base_url": BASE_URL, "token": ["top-secret-token"], "enabled": False},
            id="token-array",
        ),
        pytest.param(
            {"base_url": BASE_URL, "timeout_seconds": "abc", "enabled": False},
            id="timeout-not-number",
        ),
        pytest.param(
            {"base_url": BASE_URL, "timeout_seconds": 11, "enabled": False},
            id="timeout-too-large",
        ),
        pytest.param(
            {
                "base_url": BASE_URL,
                "token": "top-secret-token",
                "unknown_field": True,
                "enabled": False,
            },
            id="extra-field",
        ),
        pytest.param({"enabled": False}, id="missing-base-url"),
    ],
)
def test_put_validation_errors_never_echo_sensitive_input(
    client: TestClient, body: dict[str, Any]
) -> None:
    response = client.put(SERVICE_URL, json=body, headers=headers("admin"))

    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "VALIDATION_FAILED"
    _assert_no_sensitive_error_keys(response.json())
    assert "top-secret-token" not in response.text
    assert _saved_row() is None


UNSAFE_URLS = [
    "http://169。254。169。254",
    "http://169．254．169．254",
    "http://169｡254｡169｡254",
    "http://169.254.169.254。",
    "http://metadata。google。internal",
    "http://metadata.google.internal",
    "http://169.254.169.254",
    "http://2852039166",
    "http://[::169.254.169.254]",
    "http://[::ffff:0:169.254.169.254]",
    "http://[64:ff9b::169.254.169.254]",
    "http://[2002:a9fe:a9fe::]",
    "http://[2001:0:a9fe:a9fe::]",
    "http://[2001:db8::5efe:169.254.169.254]",
    "http://user:hunter2@example.com",
    "file:///etc/passwd",
    "ftp://example.com",
    "http://example.com/#fragment",
    "http://example.com/?q=1",
]


@pytest.mark.parametrize("base_url", UNSAFE_URLS)
def test_put_rejects_unsafe_urls_without_persisting(client: TestClient, base_url: str) -> None:
    response = _put(client, base_url=base_url)

    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_URL_INVALID"
    assert _saved_row() is None
    assert ok_data(client.get(SERVICE_URL))["configured"] is False


@pytest.mark.parametrize(
    "token",
    [
        "令牌-中文",
        "abc def",
        "abc\tdef",
        "abc\x01def",
        "a" * 1025,
    ],
)
def test_put_rejects_unsafe_tokens_without_500(client: TestClient, token: str) -> None:
    response = _put(client, token=token)

    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_CONFIG_INVALID"
    assert token not in response.text
    assert _saved_row() is None


def test_put_rejects_fullwidth_host_without_500(client: TestClient) -> None:
    response = _put(client, base_url="http://１６９.２５４.１６９.２５４")
    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_URL_INVALID"
    assert _saved_row() is None


def test_put_timeout_uses_capped_default_and_rejects_explicit_overage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auto_ontology_max_timeout_seconds", 2.0)

    data = ok_data(_put(client))
    assert data["timeout_seconds"] == 2.0

    response = _put(client, timeout_seconds=3.0)
    assert response.status_code == 422, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_CONFIG_INVALID"


# --- 表单测试与已保存测试 ---


def test_form_test_without_saved_config_does_not_persist(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_probe(monkeypatch, _json_handler())
    response = client.post(
        TEST_URL,
        json={"base_url": BASE_URL, "token": FORM_TOKEN},
        headers=headers("admin"),
    )
    data = ok_data(response)

    assert len(calls) == 1
    assert data["success"] is True
    assert data["persisted"] is False
    assert data["status_code"] == 200
    assert data["summary"]["json_type"] == "array"
    assert FORM_TOKEN not in response.text
    assert _saved_row() is None


def test_form_test_does_not_modify_saved_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(client, token=SAVED_TOKEN)
    snapshot = _saved_row()
    assert snapshot is not None

    _install_probe(monkeypatch, _json_handler())
    data = ok_data(
        client.post(
            TEST_URL,
            json={"base_url": BASE_URL, "token": FORM_OVERRIDE_TOKEN, "timeout_seconds": 2},
            headers=headers("admin"),
        )
    )

    assert data["persisted"] is False
    assert _saved_row() == snapshot


def test_form_test_failure_reports_not_persisted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_probe(monkeypatch, _status_handler(403))
    response = client.post(
        TEST_URL,
        json={"base_url": BASE_URL, "token": FORM_TOKEN},
        headers=headers("admin"),
    )

    assert response.status_code == 422, response.text
    payload = error_data(response)
    assert payload["code"] == "AUTO_ONTOLOGY_AUTH_FAILED"
    assert payload["details"]["persisted"] is False
    assert FORM_TOKEN not in response.text
    assert _saved_row() is None


def test_form_test_timeout_maps_to_504(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_probe(monkeypatch, _timeout_handler)
    response = client.post(
        TEST_URL, json={"base_url": BASE_URL}, headers=headers("admin")
    )

    assert response.status_code == 504, response.text
    payload = error_data(response)
    assert payload["code"] == "AUTO_ONTOLOGY_TIMEOUT"
    assert payload["details"]["persisted"] is False
    assert _saved_row() is None


@pytest.mark.parametrize("omit_body", [True, False])
def test_saved_test_without_config_returns_409(client: TestClient, omit_body: bool) -> None:
    if omit_body:
        response = client.post(TEST_URL, headers=headers("admin"))
    else:
        response = client.post(TEST_URL, json={}, headers=headers("admin"))

    assert response.status_code == 409, response.text
    assert error_data(response)["code"] == "AUTO_ONTOLOGY_NOT_CONFIGURED"


def test_saved_test_success_updates_status_and_keeps_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(client, token=SAVED_TOKEN)
    before = _saved_row()
    assert before is not None

    calls = _install_probe(monkeypatch, _json_handler())
    data = ok_data(client.post(TEST_URL, headers=headers("admin")))

    assert len(calls) == 1
    assert calls[0].headers["Authorization"] == f"Bearer {SAVED_TOKEN}"
    assert data["success"] is True
    assert data["persisted"] is True
    assert data["status_code"] == 200

    after = _saved_row()
    assert after is not None
    assert after["status"] == "healthy"
    assert after["last_status_code"] == 200
    assert after["last_error"] is None
    assert after["last_tested_at"]
    assert (after["base_url"], after["enabled"], after["probe_path"]) == (
        before["base_url"],
        before["enabled"],
        before["probe_path"],
    )
    assert after["token_ciphertext"] == before["token_ciphertext"]

    state = ok_data(client.get(SERVICE_URL))
    assert state["configured"] is True
    assert state["status"] == "disabled"
    assert state["last_status_code"] == 200
    assert state["has_token"] is True


def test_saved_test_failure_marks_error_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(client, token=SAVED_TOKEN)
    before = _saved_row()
    assert before is not None

    _install_probe(monkeypatch, _status_handler(401))
    response = client.post(TEST_URL, headers=headers("admin"))

    assert response.status_code == 422, response.text
    payload = error_data(response)
    assert payload["code"] == "AUTO_ONTOLOGY_AUTH_FAILED"
    assert payload["details"]["persisted"] is True
    assert SAVED_TOKEN not in response.text

    after = _saved_row()
    assert after is not None
    assert after["status"] == "error"
    assert after["last_status_code"] == 401
    assert "鉴权失败" in (after["last_error"] or "")
    assert after["token_ciphertext"] == before["token_ciphertext"]


def test_saved_test_decrypt_failure_marks_error_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(client, token=SAVED_TOKEN)
    row = _saved_row()
    assert row is not None
    registry.upsert_auto_ontology_binding({**row, "token_ciphertext": "not-a-fernet-token"})

    response = client.post(TEST_URL, headers=headers("admin"))

    assert response.status_code == 422, response.text
    payload = error_data(response)
    assert payload["code"] == "AUTO_ONTOLOGY_SECRET_INVALID"
    assert payload["details"]["persisted"] is True
    assert "not-a-fernet-token" not in response.text
    assert _saved_row()["status"] == "error"


# --- 清除与 OpenAPI ---


def test_delete_clears_binding_and_is_idempotent(client: TestClient) -> None:
    ok_data(_put(client, token=SAVED_TOKEN))

    first = ok_data(client.delete(SERVICE_URL, headers=headers("admin")))
    assert first["deleted"] is True
    state = ok_data(client.get(SERVICE_URL))
    assert state["configured"] is False
    assert state["status"] == "unconfigured"
    assert state["has_token"] is False

    second = ok_data(client.delete(SERVICE_URL, headers=headers("admin")))
    assert second["deleted"] is False


def test_openapi_exposes_four_routes_without_ciphertext(client: TestClient) -> None:
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert set(paths[SERVICE_URL]) >= {"get", "put", "delete"}
    assert "post" in paths[TEST_URL]
    assert "token_ciphertext" not in openapi.text


# --- 日志脱敏 ---


def test_token_ciphertext_and_query_string_never_reach_logs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="finance_ontology.auto_ontology")
    plaintext = "log-token-abc-123"
    _install_probe(monkeypatch, _json_handler())

    ok_data(_put(client, token=plaintext, enabled=True))
    ok_data(
        client.post(
            TEST_URL,
            json={"base_url": BASE_URL, "token": FORM_LOG_TOKEN},
            headers=headers("admin"),
        )
    )

    row = _saved_row()
    assert row is not None and row["token_ciphertext"]
    assert plaintext not in caplog.text
    assert FORM_LOG_TOKEN not in caplog.text
    assert row["token_ciphertext"] not in caplog.text
    assert "limit=1" not in caplog.text


# --- 既有本体/问数链路回归 ---


def _assert_local_ontology_works(client: TestClient) -> None:
    terms = ok_data(client.get("/api/ontology/terms", params={"limit": 200}))
    assert len(terms["items"]) == 27
    relations = ok_data(client.get("/api/ontology/relations"))
    assert len(relations["items"]) == 16
    generated = ok_data(
        client.post("/api/ontology/generate", json={"force": False}, headers=headers("admin"))
    )
    assert generated["status"] == "skipped"
    answer = ask(client, "客户明细", "viewer", limit=3)
    assert answer["answerable"] is True
    assert answer["rows"]


def test_local_ontology_works_with_binding_unconfigured(ontology_client: TestClient) -> None:
    _assert_local_ontology_works(ontology_client)


def test_local_ontology_works_with_binding_disabled(ontology_client: TestClient) -> None:
    ok_data(_put(ontology_client, token=SAVED_TOKEN, enabled=False))
    _assert_local_ontology_works(ontology_client)


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(_status_handler(500), id="upstream-error"),
        pytest.param(_connect_handler, id="unreachable"),
    ],
)
def test_local_ontology_works_with_binding_error(
    ontology_client: TestClient, monkeypatch: pytest.MonkeyPatch, handler: ProbeHandler
) -> None:
    _install_probe(monkeypatch, handler)
    state = ok_data(_put(ontology_client, token=SAVED_TOKEN, enabled=True))
    assert state["status"] == "error"

    _assert_local_ontology_works(ontology_client)
