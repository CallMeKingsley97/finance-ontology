"""同步 API 契约：单源/批量触发、令牌、权限、运行列表与源聚合。"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.schemas.common import AppError
from app.services.ingest import runs, store
from tests.ingest_support import (
    CUSTOMER_ROWS,
    accepted_data,
    disable_background_workers,
    execute_run,
    get_run,
    new_run,
    trigger_sync,
    upload_csv,
)
from tests.support import error_data, headers, ok_data


def _finish_active_runs() -> None:
    with store.staging_connection() as conn:
        rows = conn.execute(
            "SELECT run_id FROM ingest_run WHERE status IN ('queued','running')"
        ).fetchall()
    for row in rows:
        runs.finish_run(str(row["run_id"]), "succeeded")


def _run_of(client, response: dict) -> dict:
    run_id = accepted_data(response)["runs"][0]["run_id"]
    return get_run(client, run_id)


def test_single_sync_returns_202_and_queued_run(client, monkeypatch) -> None:
    calls = disable_background_workers(monkeypatch)
    source = upload_csv(client, CUSTOMER_ROWS)

    response = trigger_sync(client, source["id"])
    assert response.status_code == 202, response.text
    data = accepted_data(response)
    assert data["source_id"] == source["id"]
    assert data["run_id"].startswith("run_")
    assert data["status"] == "queued"
    assert data["created_at"]
    assert calls["runs"] == [(data["run_id"], source["id"])]

    detail = get_run(client, data["run_id"])
    assert detail["status"] == "queued"
    assert detail["trigger_type"] == "manual"
    assert detail["trigger_role"] == "admin"
    assert detail["tables"] == []


def test_single_sync_conflict_returns_409_with_active_run(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    source = upload_csv(client, CUSTOMER_ROWS)

    first = accepted_data(trigger_sync(client, source["id"]))
    second = trigger_sync(client, source["id"])
    assert second.status_code == 409
    payload = error_data(second)
    assert payload["code"] == "SYNC_ALREADY_RUNNING"
    assert payload["details"]["run_id"] == first["run_id"]

    listed = ok_data(
        client.get(
            "/api/sync/runs",
            params={"source_id": source["id"]},
            headers=headers("admin"),
        )
    )
    assert listed["total"] == 1


def test_single_sync_permissions_and_errors(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    source = upload_csv(client, CUSTOMER_ROWS)

    for role in ("analyst", "viewer"):
        response = trigger_sync(client, source["id"], role=role)
        assert response.status_code == 403
        assert error_data(response)["code"] == "FORBIDDEN"

    missing_role = client.post(f"/api/sources/{source['id']}/sync")
    assert missing_role.status_code == 403

    unknown = trigger_sync(client, "src_does_not_exist")
    assert unknown.status_code == 404
    assert error_data(unknown)["code"] == "SOURCE_NOT_FOUND"

    builtin = trigger_sync(client, "sqlite-local")
    assert builtin.status_code == 409
    assert error_data(builtin)["code"] == "BUILTIN_SOURCE"


def test_batch_sync_token_matrix(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    source = upload_csv(client, CUSTOMER_ROWS)
    monkeypatch.setattr(settings, "sync_service_token", "token-abc")

    accepted = client.post(
        "/api/sync/run", json={}, headers={"X-Service-Token": "token-abc"}
    )
    assert accepted.status_code == 202, accepted.text
    batch = accepted_data(accepted)
    assert batch["batch_id"].startswith("batch_")
    assert batch["runs"][0]["source_id"] == source["id"]
    run = _run_of(client, accepted)
    assert run["trigger_type"] == "cron"
    assert run["trigger_role"] == "service"
    assert run["batch_id"] == batch["batch_id"]
    _finish_active_runs()

    wrong = client.post(
        "/api/sync/run", json={}, headers={"X-Service-Token": "wrong"}
    )
    assert wrong.status_code == 401
    assert error_data(wrong)["code"] == "SYNC_TOKEN_INVALID"

    wrong_with_admin = client.post(
        "/api/sync/run",
        json={},
        headers={"X-Service-Token": "wrong", "X-Demo-Role": "admin"},
    )
    assert wrong_with_admin.status_code == 401

    viewer = client.post(
        "/api/sync/run", json={}, headers=headers("viewer")
    )
    assert viewer.status_code == 403
    assert error_data(viewer)["code"] == "FORBIDDEN"

    manual = client.post("/api/sync/run", json={}, headers=headers("admin"))
    assert manual.status_code == 202, manual.text
    manual_run = _run_of(client, manual)
    assert manual_run["trigger_type"] == "manual"
    assert manual_run["trigger_role"] == "admin"
    _finish_active_runs()

    # 令牌为空：令牌通道关闭，携带任意令牌也按角色判定。
    monkeypatch.setattr(settings, "sync_service_token", "")
    closed = client.post(
        "/api/sync/run",
        json={},
        headers={"X-Service-Token": "anything", "X-Demo-Role": "admin"},
    )
    assert closed.status_code == 202, closed.text
    closed_run = _run_of(client, closed)
    assert closed_run["trigger_type"] == "manual"


def test_batch_sync_without_external_sources_returns_409(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    response = client.post("/api/sync/run", json={}, headers=headers("admin"))
    assert response.status_code == 409
    assert error_data(response)["code"] == "SYNC_NO_SOURCES"


def test_batch_sync_source_validation(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    source = upload_csv(client, CUSTOMER_ROWS)

    empty = client.post(
        "/api/sync/run", json={"source_ids": []}, headers=headers("admin")
    )
    assert empty.status_code == 409
    assert error_data(empty)["code"] == "SYNC_NO_SOURCES"

    builtin = client.post(
        "/api/sync/run",
        json={"source_ids": ["sqlite-local"]},
        headers=headers("admin"),
    )
    assert builtin.status_code == 409
    assert error_data(builtin)["code"] == "BUILTIN_SOURCE"

    unknown = client.post(
        "/api/sync/run",
        json={"source_ids": ["src_missing"]},
        headers=headers("admin"),
    )
    assert unknown.status_code == 404
    assert error_data(unknown)["code"] == "SOURCE_NOT_FOUND"

    blank = client.post(
        "/api/sync/run", json={"source_ids": [""]}, headers=headers("admin")
    )
    assert blank.status_code == 422
    assert error_data(blank)["code"] == "VALIDATION_FAILED"

    accepted = client.post(
        "/api/sync/run",
        json={"source_ids": [source["id"], source["id"]]},
        headers=headers("admin"),
    )
    assert accepted.status_code == 202, accepted.text
    assert len(accepted_data(accepted)["runs"]) == 1


def test_batch_sync_too_many_sources_returns_422(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    monkeypatch.setattr(settings, "staging_max_batch_sources", 1)
    first = upload_csv(client, CUSTOMER_ROWS, filename="first.csv", name="源一")
    second = upload_csv(client, CUSTOMER_ROWS, filename="second.csv", name="源二")

    response = client.post(
        "/api/sync/run",
        json={"source_ids": [first["id"], second["id"]]},
        headers=headers("admin"),
    )
    assert response.status_code == 422
    assert error_data(response)["code"] == "SYNC_BATCH_TOO_LARGE"


def test_batch_sync_skips_already_running_sources(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    first = upload_csv(client, CUSTOMER_ROWS, filename="first.csv", name="源一")
    second = upload_csv(client, CUSTOMER_ROWS, filename="second.csv", name="源二")

    accepted = accepted_data(
        client.post("/api/sync/run", json={}, headers=headers("admin"))
    )
    assert {item["source_id"] for item in accepted["runs"]} == {first["id"], second["id"]}

    repeated = client.post("/api/sync/run", json={}, headers=headers("admin"))
    assert repeated.status_code == 202
    data = accepted_data(repeated)
    assert data["runs"] == []
    assert {item["source_id"] for item in data["skipped"]} == {first["id"], second["id"]}
    assert all(item["reason"] == "already_running" for item in data["skipped"])
    assert all(item["run_id"] for item in data["skipped"])


def test_sync_runs_filters_pagination_and_limit_validation(client) -> None:
    first = upload_csv(client, CUSTOMER_ROWS, filename="first.csv", name="源一")
    second = upload_csv(client, CUSTOMER_ROWS, filename="second.csv", name="源二")

    new_run(first["id"], status="succeeded")
    new_run(first["id"], status="failed")
    new_run(second["id"], status="succeeded")

    succeeded = ok_data(
        client.get(
            "/api/sync/runs",
            params={"source_id": first["id"], "status": "succeeded"},
            headers=headers("viewer"),
        )
    )
    assert succeeded["total"] == 1
    assert succeeded["items"][0]["source_id"] == first["id"]
    assert succeeded["items"][0]["status"] == "succeeded"

    paged = ok_data(
        client.get(
            "/api/sync/runs",
            params={"limit": 1, "offset": 1},
            headers=headers("admin"),
        )
    )
    assert paged["total"] == 3
    assert len(paged["items"]) == 1

    for params, code in (
        ({"limit": 101}, "VALIDATION_FAILED"),
        ({"limit": 0}, "VALIDATION_FAILED"),
        ({"offset": -1}, "VALIDATION_FAILED"),
        ({"status": "bogus"}, "VALIDATION_FAILED"),
    ):
        response = client.get("/api/sync/runs", params=params, headers=headers("admin"))
        assert response.status_code == 422, (params, response.text)
        assert error_data(response)["code"] == code

    unknown = client.get("/api/sync/runs/run_does_not_exist", headers=headers("admin"))
    assert unknown.status_code == 404
    assert error_data(unknown)["code"] == "SYNC_RUN_NOT_FOUND"


def test_sync_sources_aggregate_excludes_builtin_and_counts_rows(
    client, monkeypatch
) -> None:
    disable_background_workers(monkeypatch)
    empty = ok_data(client.get("/api/sync/sources", headers=headers("viewer")))
    assert empty["items"] == []
    assert empty["last_batch_at"] is None

    source = upload_csv(client, CUSTOMER_ROWS)
    response = trigger_sync(client, source["id"])
    run_id = accepted_data(response)["run_id"]
    execute_run(run_id, source["id"])

    data = ok_data(client.get("/api/sync/sources", headers=headers("viewer")))
    assert [item["source_id"] for item in data["items"]] == [source["id"]]
    item = data["items"][0]
    assert item["name"] == source["name"]
    assert item["kind"] == "csv"
    assert item["sync_status"] == "succeeded"
    assert item["last_run_id"] == run_id
    assert item["last_run_status"] == "succeeded"
    assert item["tables_total"] == 1
    assert item["rows_total"] == 2

    batch = accepted_data(
        client.post("/api/sync/run", json={}, headers=headers("admin"))
    )
    assert batch["batch_id"]
    after_batch = ok_data(client.get("/api/sync/sources", headers=headers("viewer")))
    assert after_batch["last_batch_at"]


def test_sync_sources_staging_failure_is_503(client, monkeypatch) -> None:
    from app.api import sync as sync_api

    def fail() -> None:
        raise AppError(503, "STAGING_UNAVAILABLE", "staging 库不可用")

    monkeypatch.setattr(sync_api.store, "ensure_staging_available", fail)
    response = client.get("/api/sync/sources")
    assert response.status_code == 503
    assert error_data(response)["code"] == "STAGING_UNAVAILABLE"
