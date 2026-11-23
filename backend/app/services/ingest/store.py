"""staging 数据库：初始化、连接与可用性检查。

staging 为独立 SQLite 库（默认 ``data/staging.db``），与 ``finance.db``、
``sources.db`` 物理隔离；全部 DDL 使用 ``IF NOT EXISTS``，重复初始化幂等。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.core.config import settings
from app.schemas.common import AppError

logger = logging.getLogger("finance_ontology.ingest.store")

STAGING_SCHEMA_VERSION = 2

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id           TEXT PRIMARY KEY,
    batch_id         TEXT,
    source_id        TEXT NOT NULL,
    source_name      TEXT NOT NULL DEFAULT '',
    source_kind      TEXT NOT NULL DEFAULT '',
    trigger_type     TEXT NOT NULL CHECK (trigger_type IN ('manual','cron')),
    trigger_role     TEXT,
    status           TEXT NOT NULL CHECK (status IN
                     ('queued','running','succeeded','partial','failed','dead_letter','interrupted')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    tables_total     INTEGER NOT NULL DEFAULT 0,
    tables_succeeded INTEGER NOT NULL DEFAULT 0,
    tables_failed    INTEGER NOT NULL DEFAULT 0,
    rows_read        INTEGER NOT NULL DEFAULT 0,
    rows_written     INTEGER NOT NULL DEFAULT 0,
    rows_skipped     INTEGER NOT NULL DEFAULT 0,
    bytes_written    INTEGER NOT NULL DEFAULT 0,
    truncated        INTEGER NOT NULL DEFAULT 0,
    warnings_json    TEXT NOT NULL DEFAULT '[]',
    error_code       TEXT,
    error_message    TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    heartbeat_at     TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingest_run_source ON ingest_run(source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_run_status ON ingest_run(status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingest_run_active_source
    ON ingest_run(source_id) WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS ingest_run_table (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    namespace     TEXT NOT NULL DEFAULT '',
    table_name    TEXT NOT NULL,
    staging_name  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','succeeded','failed','skipped')),
    sync_mode     TEXT NOT NULL DEFAULT 'full' CHECK (sync_mode IN ('full','incremental')),
    rows_read     INTEGER NOT NULL DEFAULT 0,
    rows_written  INTEGER NOT NULL DEFAULT 0,
    rows_skipped  INTEGER NOT NULL DEFAULT 0,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    truncated     INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    error_code    TEXT,
    error_message TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (run_id, namespace, table_name)
);
CREATE INDEX IF NOT EXISTS idx_run_table_run ON ingest_run_table(run_id, id);

CREATE TABLE IF NOT EXISTS staging_table (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT '',
    table_name      TEXT NOT NULL,
    staging_name    TEXT NOT NULL UNIQUE,
    columns_json    TEXT NOT NULL DEFAULT '[]',
    primary_key_json TEXT NOT NULL DEFAULT '[]',
    watermark_column TEXT,
    watermark_value TEXT,
    sync_mode       TEXT NOT NULL DEFAULT 'full' CHECK (sync_mode IN ('full','incremental')),
    row_count       INTEGER NOT NULL DEFAULT 0,
    last_run_id     TEXT,
    last_success_at TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','healthy','stale','missing','error')),
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (source_id, namespace, table_name)
);
CREATE INDEX IF NOT EXISTS idx_staging_table_source ON staging_table(source_id, status);

CREATE TABLE IF NOT EXISTS catalog_table (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         TEXT NOT NULL,
    namespace         TEXT NOT NULL DEFAULT '',
    table_name        TEXT NOT NULL,
    staging_name      TEXT,
    display_name      TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    owner             TEXT NOT NULL DEFAULT '待认领',
    business_domain   TEXT NOT NULL DEFAULT '',
    sensitivity_level TEXT NOT NULL DEFAULT 'internal'
                      CHECK (sensitivity_level IN ('public','internal','confidential','restricted')),
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','active','missing')),
    row_count         INTEGER NOT NULL DEFAULT 0,
    last_sync_at      TEXT,
    watermark_column  TEXT,
    sync_mode         TEXT NOT NULL DEFAULT 'full' CHECK (sync_mode IN ('full','incremental')),
    ontology_term_id  INTEGER,
    source_kind       TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    updated_by        TEXT,
    UNIQUE (source_id, namespace, table_name)
);
CREATE INDEX IF NOT EXISTS idx_catalog_table_source ON catalog_table(source_id, status);
CREATE INDEX IF NOT EXISTS idx_catalog_table_owner ON catalog_table(owner);
CREATE INDEX IF NOT EXISTS idx_catalog_table_domain ON catalog_table(business_domain);
CREATE INDEX IF NOT EXISTS idx_catalog_table_name ON catalog_table(table_name);

CREATE TABLE IF NOT EXISTS catalog_column (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id          INTEGER NOT NULL REFERENCES catalog_table(id) ON DELETE CASCADE,
    column_name       TEXT NOT NULL,
    ordinal           INTEGER NOT NULL DEFAULT 0,
    display_name      TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    data_type         TEXT NOT NULL DEFAULT '',
    semantic_type     TEXT NOT NULL DEFAULT '',
    is_nullable       INTEGER NOT NULL DEFAULT 1,
    is_primary_key    INTEGER NOT NULL DEFAULT 0,
    is_foreign_key    INTEGER NOT NULL DEFAULT 0,
    sensitivity_level TEXT NOT NULL DEFAULT 'internal'
                      CHECK (sensitivity_level IN ('public','internal','confidential','restricted')),
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','active','missing')),
    ontology_column_id INTEGER,
    updated_by        TEXT,
    updated_at        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (table_id, column_name)
);
CREATE INDEX IF NOT EXISTS idx_catalog_column_table ON catalog_column(table_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_catalog_column_status ON catalog_column(status);

CREATE TABLE IF NOT EXISTS catalog_change_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type      TEXT NOT NULL CHECK (entity_type IN ('table','column')),
    entity_id        INTEGER NOT NULL,
    source_id        TEXT NOT NULL DEFAULT '',
    table_id         INTEGER,
    column_id        INTEGER,
    field_name       TEXT NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    changed_by_role  TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalog_change_entity
    ON catalog_change_log(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_change_table
    ON catalog_change_log(table_id, created_at DESC);

CREATE TABLE IF NOT EXISTS catalog_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- ===== 指标注册表（schema v2） =====
CREATE TABLE IF NOT EXISTS metric_definition (
    code              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    definition        TEXT NOT NULL DEFAULT '',
    formula_text      TEXT NOT NULL DEFAULT '',
    formula_sql       TEXT NOT NULL,
    boundary_json     TEXT NOT NULL DEFAULT '{}',
    owner             TEXT NOT NULL DEFAULT '',
    business_domain   TEXT NOT NULL DEFAULT '',
    unit              TEXT NOT NULL DEFAULT 'CNY',
    sensitivity_level TEXT NOT NULL DEFAULT 'internal'
                      CHECK (sensitivity_level IN ('public','internal','confidential','restricted')),
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','pending','certified','deprecated')),
    current_version   TEXT,
    term_code         TEXT,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    updated_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_metric_definition_status ON metric_definition(status, sort_order);
CREATE INDEX IF NOT EXISTS idx_metric_definition_domain ON metric_definition(business_domain);

CREATE TABLE IF NOT EXISTS metric_version (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_code     TEXT NOT NULL REFERENCES metric_definition(code) ON DELETE CASCADE,
    version         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('draft','pending','certified','deprecated')),
    snapshot_json   TEXT NOT NULL DEFAULT '{}',
    change_note     TEXT NOT NULL DEFAULT '',
    effective_period TEXT,
    certified_by    TEXT,
    certified_at    TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (metric_code, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_version_certified
    ON metric_version(metric_code) WHERE status = 'certified';
CREATE INDEX IF NOT EXISTS idx_metric_version_code ON metric_version(metric_code, id DESC);

CREATE TABLE IF NOT EXISTS metric_test_case (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_code     TEXT NOT NULL REFERENCES metric_definition(code) ON DELETE CASCADE,
    version_id      INTEGER REFERENCES metric_version(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    period_code     TEXT NOT NULL,
    expected_value  REAL NOT NULL,
    tolerance_abs   REAL NOT NULL DEFAULT 0.01,
    tolerance_rel   REAL NOT NULL DEFAULT 0.0001,
    last_status     TEXT NOT NULL DEFAULT 'not_run'
                    CHECK (last_status IN ('passed','failed','error','not_run')),
    last_actual     REAL,
    last_diff       REAL,
    last_run_at     TEXT,
    last_message    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (metric_code, name)
);
CREATE INDEX IF NOT EXISTS idx_metric_test_case_code ON metric_test_case(metric_code, id);

CREATE TABLE IF NOT EXISTS metric_alias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_code     TEXT NOT NULL REFERENCES metric_definition(code) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type      TEXT NOT NULL CHECK (alias_type IN ('synonym','business','exclusion')),
    priority        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (metric_code, normalized_alias)
);
CREATE INDEX IF NOT EXISTS idx_metric_alias_norm ON metric_alias(normalized_alias, status);

-- ===== 质量规则与运行（schema v2） =====
CREATE TABLE IF NOT EXISTS quality_rule (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    rule_type       TEXT NOT NULL
                    CHECK (rule_type IN ('reconciliation','completeness','uniqueness','range','custom_sql')),
    target_db       TEXT NOT NULL CHECK (target_db IN ('business','staging')),
    target_ref      TEXT NOT NULL DEFAULT '',
    expression_json TEXT NOT NULL DEFAULT '{}',
    tolerance       REAL NOT NULL DEFAULT 0.01,
    severity        TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('info','warning','blocker')),
    enabled         INTEGER NOT NULL DEFAULT 1,
    schedule        TEXT NOT NULL DEFAULT 'manual'
                    CHECK (schedule IN ('manual','sync','report','sync_report')),
    owner           TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_quality_rule_enabled ON quality_rule(enabled, schedule);

CREATE TABLE IF NOT EXISTS quality_run (
    run_id          TEXT PRIMARY KEY,
    trigger_type    TEXT NOT NULL CHECK (trigger_type IN ('manual','sync','report')),
    trigger_role    TEXT,
    period_code     TEXT,
    source_id       TEXT,
    ingest_run_id   TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
    rules_total     INTEGER NOT NULL DEFAULT 0,
    rules_passed    INTEGER NOT NULL DEFAULT 0,
    rules_failed    INTEGER NOT NULL DEFAULT 0,
    rules_skipped   INTEGER NOT NULL DEFAULT 0,
    blocker_failed  INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_run_created ON quality_run(created_at DESC, run_id);
CREATE INDEX IF NOT EXISTS idx_quality_run_trigger ON quality_run(trigger_type, created_at DESC);

CREATE TABLE IF NOT EXISTS quality_result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES quality_run(run_id) ON DELETE CASCADE,
    rule_code       TEXT NOT NULL,
    rule_name       TEXT NOT NULL,
    rule_type       TEXT NOT NULL,
    target_db       TEXT NOT NULL,
    severity        TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('passed','failed','skipped','error')),
    left_value      REAL,
    right_value     REAL,
    diff            REAL,
    tolerance       REAL,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    error_message   TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_result_run ON quality_result(run_id, id);
CREATE INDEX IF NOT EXISTS idx_quality_result_failed
    ON quality_result(status, created_at DESC);
"""

_initialized: set[str] = set()


def staging_now() -> str:
    """staging 统一时间戳：带时区、秒级 ISO 8601。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def init_staging() -> None:
    """首次调用执行 DDL；重复调用为幂等。"""
    path = settings.staging_db_path
    key = str(path)
    if key in _initialized and path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA user_version = {STAGING_SCHEMA_VERSION}")
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _initialized.add(key)
    logger.info("staging initialized path=%s", path)


@contextmanager
def staging_connection() -> Iterator[sqlite3.Connection]:
    """staging 连接：autocommit（isolation_level=None），busy_timeout=10000。

    多语句原子写入须显式 ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``；
    autocommit 下 ``conn.commit()`` / ``conn.rollback()`` 为 no-op。
    """
    init_staging()
    conn = sqlite3.connect(str(settings.staging_db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_staging_available() -> None:
    """确认 staging 可用；初始化失败统一转换为 503。"""
    try:
        init_staging()
    except Exception as exc:  # noqa: BLE001 - 初始化失败细节不暴露给调用方
        logger.exception("staging unavailable: %s", exc)
        raise AppError(503, "STAGING_UNAVAILABLE", "staging 库不可用") from exc


def _readonly_authorizer(allowed_tables: frozenset[str]):
    """staging 只读守卫：仅放行 SELECT/READ/FUNCTION/RECURSIVE 与白名单表。"""
    allowed = frozenset(name.lower() for name in allowed_tables)
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", 33)

    def _authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, trigger: str | None) -> int:
        if action == sqlite3.SQLITE_READ:
            if arg1 is None or arg1.lower() in allowed:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, recursive):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return _authorizer


def staging_readonly_connection(allowed_tables: frozenset[str]) -> sqlite3.Connection:
    """staging 只读连接：``mode=ro`` + ``query_only`` + 表白名单 authorizer。

    调用方负责 close；只允许读取 ``allowed_tables`` 中的表。
    """
    ensure_staging_available()
    path = settings.staging_db_path
    conn = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.set_authorizer(_readonly_authorizer(allowed_tables))
    return conn


def staging_db_path() -> Path:
    """当前 staging 库路径（配置派生，不接受 API 输入）。"""
    return settings.staging_db_path
