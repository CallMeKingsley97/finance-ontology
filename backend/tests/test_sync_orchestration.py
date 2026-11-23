"""同步编排：成功/幂等、截断、重试死信、表级 partial、恢复与 staging 503。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors.base import ConnectorError
from app.core.config import settings
from app.main import app
from app.services.ingest import catalog, orchestrator, runs, store
from tests.ingest_support import (
    CUSTOMER_ROWS,
    accepted_data,
    catalog_tables,
    create_database_source,
    create_raw_table,
    disable_background_workers,
    execute_run,
    get_run,
    orphan_temp_tables,
    raw_table_exists,
    register_missing_file_source,
    run_sync_now,
    set_run_heartbeat,
    staging_row,
    staging_row_count,
    staging_table_names,
    trigger_sync,
    upload_csv,
    upload_excel,
    upload_sqlite,
)
from tests.source_support import make_sqlite_db
from tests.support import error_data, headers, ok_data


def test_sqlite_sync_succeeds_and_repeat_is_idempotent(
    client, monkeypatch, tmp_path: Path
) -> None:
    disable_background_workers(monkeypatch)
    source_path = make_sqlite_db(tmp_path / "source.db")
    source = upload_sqlite(client, source_path)

    first = run_sync_now(client, source["id"])
    assert first["status"] == "succeeded"
    assert first["attempts"] == 1
    assert first["retry_count"] == 0
    assert (first["tables_total"], first["tables_succeeded"], first["tables_failed"]) == (2, 2, 0)
    assert first["rows_read"] == first["rows_written"] == 4
    assert first["truncated"] is False
    assert first["warnings"] == []

    tables = {item["table_name"]: item for item in first["tables"]}
    assert set(tables) == {"parent", "child"}
    for name, item in tables.items():
        assert item["status"] == "succeeded"
        assert item["sync_mode"] == "full"
        assert item["rows_written"] == 2
        assert item["staging_name"].startswith(f"stg_")
        assert staging_row(source["id"], name)["status"] == "healthy"
        assert staging_row_count(item["staging_name"]) == 2

    registered = {item["table_name"] for item in catalog_tables(client, source["id"])}
    assert registered == {"parent", "child"}

    second = run_sync_now(client, source["id"])
    assert second["status"] == "succeeded"
    assert second["rows_written"] == 4
    for name in ("parent", "child"):
        item = staging_row(source["id"], name)
        assert item["row_count"] == 2
        assert staging_row_count(item["staging_name"]) == 2
    assert orphan_temp_tables() == []


def test_csv_sync_truncates_at_row_limit_and_warns(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    monkeypatch.setattr(settings, "staging_max_rows_per_table", 2)
    monkeypatch.setattr(settings, "staging_batch_size", 1)
    rows = [["id", "name"]] + [[index, f"row-{index}"] for index in range(1, 6)]
    source = upload_csv(client, rows)

    run = run_sync_now(client, source["id"])
    assert run["status"] == "succeeded"
    assert run["truncated"] is True
    assert "ROW_LIMIT_REACHED" in run["warnings"]
    assert "NO_PRIMARY_KEY_FULL_REFRESH" in run["warnings"]
    assert run["rows_read"] == 2
    table = run["tables"][0]
    assert table["truncated"] is True
    assert table["rows_read"] == 2
    staging = staging_row(source["id"], "customers")
    assert staging["row_count"] == 2


def test_retry_exhaustion_marks_dead_letter(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    monkeypatch.setattr(settings, "staging_retry_backoff_seconds", 0.001)
    source_row = register_missing_file_source()

    response = trigger_sync(client, source_row["id"])
    assert response.status_code == 202, response.text
    run_id = response.json()["data"]["run_id"]
    execute_run(run_id, source_row["id"])

    run = get_run(client, run_id)
    assert run["status"] == "dead_letter"
    assert run["error_code"] == "SYNC_DEAD_LETTER"
    assert run["attempts"] == 4
    assert run["retry_count"] == 3
    assert run["error_message"]
    assert "missing.csv" not in run["error_message"]
    assert staging_table_names() == []


@pytest.mark.parametrize("kind", ["postgresql", "mysql"])
def test_database_connection_failure_is_dead_letter_without_secret(
    client, monkeypatch, kind: str
) -> None:
    """真实数据库连通性未运行；此处仅覆盖本机不可达端口的失败路径。"""
    disable_background_workers(monkeypatch)
    monkeypatch.setattr(settings, "staging_retry_backoff_seconds", 0.001)
    secret = "TASK04_SECRET_MARKER"
    source = create_database_source(client, kind=kind, secret=secret)

    response = trigger_sync(client, source["id"])
    run_id = accepted_data(response)["run_id"]
    execute_run(run_id, source["id"])

    run = get_run(client, run_id)
    assert run["status"] == "dead_letter"
    assert run["error_code"] == "SYNC_DEAD_LETTER"
    assert run["attempts"] == 4
    assert run["retry_count"] == 3
    assert secret not in (run["error_message"] or "")


def test_table_level_failure_yields_partial_and_keeps_good_table(
    client, monkeypatch
) -> None:
    disable_background_workers(monkeypatch)
    source = upload_excel(
        client,
        {
            "good": [["id", "name"], [1, "a"], [2, "b"]],
            "bad": [["id", "name"], [3, "c"]],
        },
    )

    original = orchestrator._sync_table

    def flaky(connector, table, source_row, run_id, secrets):
        if table.name == "bad":
            raise ConnectorError("模拟表级失败")
        return original(connector, table, source_row, run_id, secrets)

    monkeypatch.setattr(orchestrator, "_sync_table", flaky)
    run = run_sync_now(client, source["id"])

    assert run["status"] == "partial"
    assert (run["tables_total"], run["tables_succeeded"], run["tables_failed"]) == (2, 1, 1)
    tables = {item["table_name"]: item for item in run["tables"]}
    assert tables["good"]["status"] == "succeeded"
    assert tables["good"]["rows_written"] == 2
    assert tables["bad"]["status"] == "failed"
    assert tables["bad"]["error_code"] == "TABLE_SYNC_FAILED"
    assert tables["bad"]["error_message"] == "模拟表级失败"

    good = staging_row(source["id"], "good")
    assert good is not None and good["status"] == "healthy"
    assert staging_row(source["id"], "bad") is None


def test_catalog_sync_failure_does_not_fail_run(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    source = upload_csv(client, CUSTOMER_ROWS)

    def fail_catalog(*_args, **_kwargs):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(catalog, "sync_source_catalog", fail_catalog)
    run = run_sync_now(client, source["id"])

    assert run["status"] == "succeeded"
    assert "CATALOG_SYNC_FAILED" in run["warnings"]
    with store.staging_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_table WHERE source_id = ?",
            (source["id"],),
        ).fetchone()
    assert int(count["n"]) == 0


def test_recover_interrupted_runs_and_orphans(client) -> None:
    stale = runs.create_run(
        {"id": "src_stale01", "name": "过期源", "kind": "csv"},
        trigger_type="manual",
        trigger_role="admin",
    )
    set_run_heartbeat(stale["run_id"], "2020-01-01T00:00:00+08:00")

    fresh = runs.create_run(
        {"id": "src_fresh01", "name": "活跃源", "kind": "csv"},
        trigger_type="manual",
        trigger_role="admin",
    )
    runs.mark_running(fresh["run_id"])

    create_raw_table("stg_abcd1234__new")
    create_raw_table("stg_abcd1234_keep")

    first = runs.recover_interrupted_runs()
    assert first["interrupted"] == 1
    assert first["orphans_dropped"] == 0  # 仍有活跃运行，不清临时表
    stale_detail = runs.get_run(stale["run_id"])
    assert stale_detail["status"] == "interrupted"
    assert stale_detail["error_code"] == "RUN_INTERRUPTED"
    assert stale_detail["finished_at"]
    assert raw_table_exists("stg_abcd1234__new")

    runs.finish_run(fresh["run_id"], "succeeded")
    second = runs.recover_interrupted_runs()
    assert second["interrupted"] == 0
    assert second["orphans_dropped"] == 1
    assert not raw_table_exists("stg_abcd1234__new")
    assert raw_table_exists("stg_abcd1234_keep")


def test_staging_unavailable_does_not_block_app_startup(
    seeded_db: dict, tmp_path: Path, monkeypatch
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        settings, "staging_database_path", str(blocker / "staging.db")
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        health = ok_data(test_client.get("/api/health"))
        assert health["status"] == "ok"

        sources = ok_data(test_client.get("/api/sources"))
        assert sources["items"][0]["id"] == "sqlite-local"
        assert sources["items"][0]["table_count"] == 16

        for path in ("/api/sync/sources", "/api/sync/runs", "/api/catalog/summary"):
            response = test_client.get(path, headers=headers("admin"))
            assert response.status_code == 503, (path, response.text)
            assert error_data(response)["code"] == "STAGING_UNAVAILABLE"

        single = test_client.post(
            "/api/sources/sqlite-local/sync", headers=headers("admin")
        )
        assert single.status_code == 503
        assert error_data(single)["code"] == "STAGING_UNAVAILABLE"

        batch = test_client.post(
            "/api/sync/run", json={}, headers=headers("admin")
        )
        assert batch.status_code == 503
        assert error_data(batch)["code"] == "STAGING_UNAVAILABLE"


def test_batch_execution_is_sequential_and_idempotent(client, monkeypatch) -> None:
    disable_background_workers(monkeypatch)
    first = upload_csv(client, CUSTOMER_ROWS, filename="first.csv", name="源一")
    second = upload_csv(client, CUSTOMER_ROWS, filename="second.csv", name="源二")

    accepted = accepted_data(
        client.post("/api/sync/run", json={}, headers=headers("admin"))
    )
    for item in accepted["runs"]:
        execute_run(item["run_id"], item["source_id"])

    for item in accepted["runs"]:
        detail = get_run(client, item["run_id"])
        assert detail["status"] == "succeeded"
        assert detail["batch_id"] == accepted["batch_id"]
        assert detail["rows_written"] == 2

    table_names = {first["id"]: "first", second["id"]: "second"}
    for source in (first, second):
        staging = staging_row(source["id"], table_names[source["id"]])
        assert staging["row_count"] == 2
        assert staging_row_count(staging["staging_name"]) == 2

    repeated = accepted_data(
        client.post("/api/sync/run", json={}, headers=headers("admin"))
    )
    for item in repeated["runs"]:
        execute_run(item["run_id"], item["source_id"])
    for source in (first, second):
        staging = staging_row(source["id"], table_names[source["id"]])
        assert staging["row_count"] == 2
    assert orphan_temp_tables() == []
