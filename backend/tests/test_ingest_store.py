"""staging 存储层：DDL、命名、行键、建表/写入/换表与孤儿清理。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.connectors.base import ConnectorError
from app.core.config import settings
from app.services.ingest import store, writers

EXPECTED_TABLES = {
    # schema v1：同步/目录元数据
    "ingest_run",
    "ingest_run_table",
    "staging_table",
    "catalog_table",
    "catalog_column",
    "catalog_change_log",
    "catalog_meta",
    # schema v2：指标注册表与质量规则/运行
    "metric_definition",
    "metric_version",
    "metric_test_case",
    "metric_alias",
    "quality_rule",
    "quality_run",
    "quality_result",
}

EXPECTED_INDEXES = {
    "idx_ingest_run_source",
    "idx_ingest_run_status",
    "uq_ingest_run_active_source",
    "idx_run_table_run",
    "idx_staging_table_source",
    "idx_catalog_table_source",
    "idx_catalog_table_owner",
    "idx_catalog_table_domain",
    "idx_catalog_table_name",
    "idx_catalog_column_table",
    "idx_catalog_column_status",
    "idx_catalog_change_entity",
    "idx_catalog_change_table",
    "idx_metric_definition_status",
    "idx_metric_definition_domain",
    "uq_metric_version_certified",
    "idx_metric_version_code",
    "idx_metric_test_case_code",
    "idx_metric_alias_norm",
    "idx_quality_rule_enabled",
    "idx_quality_run_created",
    "idx_quality_run_trigger",
    "idx_quality_result_run",
    "idx_quality_result_failed",
}


def _schema_names(kind: str) -> set[str]:
    with store.staging_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (kind,),
        ).fetchall()
    return {str(row["name"]) for row in rows}


def test_staging_path_derives_from_database_path(tmp_path: Path, monkeypatch) -> None:
    settings.database_path = str(tmp_path / "finance.db")
    assert settings.staging_db_path == tmp_path / "staging.db"

    custom = tmp_path / "custom" / "sync.db"
    monkeypatch.setattr(settings, "staging_database_path", str(custom))
    assert settings.staging_db_path == custom


def test_init_staging_creates_tables_indexes_and_version() -> None:
    store.init_staging()
    store.init_staging()  # 幂等

    assert _schema_names("table") == EXPECTED_TABLES
    indexes = _schema_names("index")
    assert EXPECTED_INDEXES <= indexes, EXPECTED_INDEXES - indexes

    with store.staging_connection() as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"

    mode = settings.staging_db_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_staging_name_for_is_deterministic_and_sql_safe() -> None:
    first = writers.staging_name_for("src_a", "main", "fact_journal")
    second = writers.staging_name_for("src_a", "main", "fact_journal")
    other = writers.staging_name_for("src_b", "main", "fact_journal")

    assert first == second
    assert first != other
    assert first.startswith("stg_")
    assert len(first) <= 61

    injected = writers.staging_name_for(
        "src'; DROP TABLE x;--", "main", "用户'); DROP TABLE y;--"
    )
    assert injected.startswith("stg_")
    assert ";" not in injected
    assert " " not in injected
    assert "'" not in injected
    assert injected == injected.lower()

    long_name = writers.staging_name_for("src_a", "main", "超长中文表名" * 50)
    assert long_name.startswith("stg_")
    assert len(long_name) <= 61


def test_create_table_rejects_unsafe_identifiers() -> None:
    with store.staging_connection() as conn:
        for bad_name in ("bad name", "stg_ok;DROP", "../stg_ok", ""):
            with pytest.raises(ConnectorError):
                writers.create_table(conn, bad_name, [("id", "INTEGER")])

        writers.create_table(conn, "stg_abc123_def", [("id", "INTEGER")])
        with pytest.raises(ConnectorError):
            writers.create_table(conn, "stg_abc123_def", [("_row_key", "TEXT")])
        with pytest.raises(ConnectorError):
            writers.create_table(conn, "stg_abc123_def", [("id", "INTEGER"), ("ID", "TEXT")])
        with pytest.raises(ConnectorError):
            writers.create_table(conn, "stg_abc123_def", [("", "TEXT")])


def test_sqlite_affinity_mapping() -> None:
    assert writers.sqlite_affinity("INTEGER") == "INTEGER"
    assert writers.sqlite_affinity("DECIMAL(18, 2)") == "REAL"
    assert writers.sqlite_affinity("BOOLEAN") == "INTEGER"
    assert writers.sqlite_affinity("BYTEA") == "BLOB"
    assert writers.sqlite_affinity("TIMESTAMP WITH TIME ZONE") == "TEXT"
    assert writers.sqlite_affinity(None) == "TEXT"


def test_row_key_pk_mode_ignores_non_key_changes_and_order() -> None:
    columns = ["id", "name", "amount"]
    base = {"id": 1, "name": "alpha", "amount": 1.5}
    reordered = {"amount": 1.5, "name": "alpha", "id": 1}

    key = writers.row_key(columns, base, ["id"])
    assert key == writers.row_key(columns, reordered, ["id"])
    assert key == writers.row_key(columns, {**base, "name": "beta"}, ["id"])
    assert key == writers.row_key(columns, {**base, "amount": 99.0}, ["id"])
    assert key != writers.row_key(columns, {**base, "id": 2}, ["id"])

    # 主键列不在列清单时退化为整行哈希。
    fallback = writers.row_key(columns, base, ["missing_pk"])
    assert fallback != key


def test_row_key_without_pk_changes_on_any_value() -> None:
    columns = ["id", "name", "amount"]
    base = {"id": 1, "name": "alpha", "amount": 1.5}
    key = writers.row_key(columns, base, [])

    assert key != writers.row_key(columns, {**base, "name": "beta"}, [])
    assert key != writers.row_key(columns, {**base, "amount": 2.5}, [])
    assert key != writers.row_key(columns, {**base, "id": 2}, [])
    assert key == writers.row_key(columns, dict(base), [])


def test_create_insert_swap_is_idempotent_without_temp_leftover() -> None:
    staging_name = writers.staging_name_for("src_a", "main", "events")
    temp_name = staging_name + "__new"
    columns = [("id", "INTEGER"), ("name", "TEXT")]
    rows = [
        {"id": 1, "name": "a"},
        {"id": 1, "name": "a"},  # 同批重复主键
        {"id": 2, "name": "b"},
    ]

    with store.staging_connection() as conn:
        writers.create_table(conn, temp_name, columns)
        written, bytes_estimate = writers.insert_rows(
            conn, temp_name, ["id", "name"], rows, ["id"]
        )
        assert written == 2
        assert bytes_estimate > 0
        writers.swap_table(conn, staging_name)
        assert writers.table_row_count(conn, staging_name) == 2

        # 重复执行同一快照：行数不增加，且没有 __new 残留。
        writers.create_table(conn, temp_name, columns)
        writers.insert_rows(conn, temp_name, ["id", "name"], rows, ["id"])
        writers.swap_table(conn, staging_name)
        assert writers.table_row_count(conn, staging_name) == 2

        leftovers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name LIKE 'stg\\_%\\_\\_new' ESCAPE '\\'"
        ).fetchall()
        assert leftovers == []


def test_insert_rows_empty_batch_returns_zero() -> None:
    with store.staging_connection() as conn:
        writers.create_table(conn, "stg_empty_table", [("id", "INTEGER")])
        assert writers.insert_rows(conn, "stg_empty_table", ["id"], [], []) == (0, 0)


def test_drop_orphan_temp_tables_only_removes_temp_tables() -> None:
    with store.staging_connection() as conn:
        writers.create_table(conn, "stg_abcd1234_keep", [("id", "INTEGER")])
        writers.create_table(conn, "stg_abcd1234_keep__new", [("id", "INTEGER")])
        writers.create_table(conn, "stg_abcd1234_other__new", [("id", "INTEGER")])
        conn.execute("CREATE TABLE unrelated (id INTEGER)")

        removed = writers.drop_orphan_temp_tables(conn)
        assert removed == 2

    assert _schema_names("table") >= EXPECTED_TABLES
    with store.staging_connection() as conn:
        names = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "stg_abcd1234_keep" in names
    assert "unrelated" in names
    assert "stg_abcd1234_keep__new" not in names
    assert "stg_abcd1234_other__new" not in names
