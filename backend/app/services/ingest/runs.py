"""同步运行记录：原子 claim、状态迁移、表级结果、列表与恢复/清理。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.schemas.common import AppError
from app.services.ingest import store, writers

logger = logging.getLogger("finance_ontology.ingest.runs")

TERMINAL_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "dead_letter", "interrupted"}
)
RUN_STATUSES = ("queued", "running", "succeeded", "partial", "failed", "dead_letter", "interrupted")

_RUN_COUNTERS = frozenset(
    {
        "rows_read",
        "rows_written",
        "rows_skipped",
        "bytes_written",
        "truncated",
        "tables_total",
        "tables_succeeded",
        "tables_failed",
    }
)
_TABLE_RESULT_FIELDS = (
    "source_id",
    "namespace",
    "table_name",
    "staging_name",
    "status",
    "sync_mode",
    "rows_read",
    "rows_written",
    "rows_skipped",
    "bytes_written",
    "truncated",
    "duration_ms",
    "error_code",
    "error_message",
)

_RUN_COLUMNS = (
    "run_id, batch_id, source_id, source_name, source_kind, trigger_type, trigger_role, "
    "status, attempts, retry_count, tables_total, tables_succeeded, tables_failed, "
    "rows_read, rows_written, rows_skipped, bytes_written, truncated, warnings_json, "
    "error_code, error_message, started_at, finished_at, heartbeat_at, created_at, updated_at"
)


def _run_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        warnings = json.loads(item.pop("warnings_json", "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        warnings = []
    item["warnings"] = warnings if isinstance(warnings, list) else []
    item["truncated"] = bool(item.get("truncated"))
    return item


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("id") or source.get("source_id") or "")


def create_run(
    source: dict[str, Any],
    *,
    trigger_type: str,
    trigger_role: str,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """原子 claim 单源运行；唯一索引冲突转换为 409。"""
    store.ensure_staging_available()
    source_id = _source_id(source)
    if not source_id:
        raise AppError(422, "VALIDATION_FAILED", "数据源标识不能为空")
    if trigger_type not in ("manual", "cron"):
        raise AppError(422, "VALIDATION_FAILED", "触发方式非法")
    run_id = "run_" + uuid4().hex[:12]
    now = store.staging_now()
    try:
        with store.staging_connection() as conn:
            conn.execute(
                """INSERT INTO ingest_run
                   (run_id, batch_id, source_id, source_name, source_kind, trigger_type,
                    trigger_role, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    run_id,
                    batch_id,
                    source_id,
                    str(source.get("name") or ""),
                    str(source.get("kind") or ""),
                    trigger_type,
                    trigger_role,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM ingest_run WHERE run_id = ?", (run_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        with store.staging_connection() as conn:
            active = conn.execute(
                """SELECT run_id FROM ingest_run
                   WHERE source_id = ? AND status IN ('queued','running')
                   ORDER BY created_at DESC LIMIT 1""",
                (source_id,),
            ).fetchone()
        raise AppError(
            409,
            "SYNC_ALREADY_RUNNING",
            "该数据源已有运行中的同步任务",
            {"run_id": active["run_id"] if active else None},
        ) from exc
    if row is None:
        raise AppError(500, "INTERNAL_ERROR", "运行记录创建失败")
    logger.info("ingest run created run_id=%s source_id=%s trigger=%s", run_id, source_id, trigger_type)
    return _run_to_dict(row)


def start_batch(
    sources: list[dict[str, Any]], *, trigger_type: str, trigger_role: str
) -> dict[str, Any]:
    """批量 claim；冲突源进入 skipped，其余进入同一 batch。"""
    store.ensure_staging_available()
    if not sources:
        raise AppError(409, "SYNC_NO_SOURCES", "没有可同步的已注册数据源")
    max_sources = max(1, int(settings.staging_max_batch_sources or 50))
    if len(sources) > max_sources:
        raise AppError(
            422,
            "SYNC_BATCH_TOO_LARGE",
            f"单次批量同步最多 {max_sources} 个数据源",
        )
    batch_id = "batch_" + uuid4().hex[:12]
    runs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for source in sources:
        source_id = _source_id(source)
        try:
            run = create_run(
                source,
                trigger_type=trigger_type,
                trigger_role=trigger_role,
                batch_id=batch_id,
            )
        except AppError as exc:
            if exc.code == "SYNC_ALREADY_RUNNING":
                skipped.append(
                    {
                        "source_id": source_id,
                        "reason": "already_running",
                        "run_id": exc.details.get("run_id"),
                    }
                )
                continue
            raise
        runs.append(
            {"source_id": source_id, "run_id": run["run_id"], "status": run["status"]}
        )
    return {"batch_id": batch_id, "runs": runs, "skipped": skipped}


def mark_running(run_id: str) -> None:
    """进入 running；首次尝试不增加 retry_count，后续尝试递增。"""
    now = store.staging_now()
    with store.staging_connection() as conn:
        conn.execute(
            """UPDATE ingest_run
               SET status = 'running',
                   attempts = attempts + 1,
                   retry_count = CASE WHEN attempts > 0 THEN retry_count + 1 ELSE retry_count END,
                   started_at = COALESCE(started_at, ?),
                   heartbeat_at = ?,
                   updated_at = ?
               WHERE run_id = ?""",
            (now, now, now, run_id),
        )


def touch_run(run_id: str, **counters: Any) -> None:
    """累加运行计数并刷新心跳；仅接受常量白名单字段。"""
    now = store.staging_now()
    assignments = ["heartbeat_at = ?", "updated_at = ?"]
    params: list[Any] = [now, now]
    for key, value in counters.items():
        if key not in _RUN_COUNTERS or value is None:
            continue
        assignments.append(f"{key} = {key} + ?")
        params.append(int(value))
    params.append(run_id)
    with store.staging_connection() as conn:
        conn.execute(
            f"UPDATE ingest_run SET {', '.join(assignments)} WHERE run_id = ?",
            tuple(params),
        )


def record_table_result(run_id: str, **fields: Any) -> None:
    """按 (run_id, namespace, table_name) UPSERT 表级结果。"""
    values: dict[str, Any] = {
        "source_id": "",
        "namespace": "",
        "table_name": "",
        "staging_name": None,
        "status": "pending",
        "sync_mode": "full",
        "rows_read": 0,
        "rows_written": 0,
        "rows_skipped": 0,
        "bytes_written": 0,
        "truncated": 0,
        "duration_ms": 0,
        "error_code": None,
        "error_message": None,
    }
    for key, value in fields.items():
        if key in _TABLE_RESULT_FIELDS:
            values[key] = value
    if values["status"] not in ("pending", "running", "succeeded", "failed", "skipped"):
        values["status"] = "pending"
    if values["sync_mode"] not in ("full", "incremental"):
        values["sync_mode"] = "full"
    for key in ("rows_read", "rows_written", "rows_skipped", "bytes_written", "duration_ms"):
        values[key] = int(values[key] or 0)
    values["truncated"] = int(bool(values["truncated"]))
    now = store.staging_now()
    insert_columns = ("run_id", *_TABLE_RESULT_FIELDS)
    columns = ", ".join(insert_columns)
    placeholders = ", ".join("?" for _ in insert_columns)
    updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in _TABLE_RESULT_FIELDS
        if column not in ("source_id", "namespace", "table_name")
    )
    with store.staging_connection() as conn:
        conn.execute(
            f"""INSERT INTO ingest_run_table ({columns}, created_at, updated_at)
                VALUES ({placeholders}, ?, ?)
                ON CONFLICT(run_id, namespace, table_name) DO UPDATE SET
                    {updates}, updated_at = excluded.updated_at""",
            (run_id, *[values[column] for column in _TABLE_RESULT_FIELDS], now, now),
        )


def finish_run(
    run_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    """写入终态、错误与 warning；error_message 调用方须已脱敏。"""
    if status not in TERMINAL_STATUSES:
        raise AppError(422, "VALIDATION_FAILED", f"非法运行终态: {status}")
    now = store.staging_now()
    warnings_json = None if warnings is None else json.dumps(warnings, ensure_ascii=False)
    assignments = [
        "status = ?",
        "finished_at = ?",
        "updated_at = ?",
        "error_code = ?",
        "error_message = ?",
    ]
    params: list[Any] = [status, now, now, error_code, error_message[:300] if error_message else None]
    if warnings_json is not None:
        assignments.append("warnings_json = ?")
        params.append(warnings_json)
    params.append(run_id)
    with store.staging_connection() as conn:
        conn.execute(
            f"UPDATE ingest_run SET {', '.join(assignments)} WHERE run_id = ?", tuple(params)
        )
    logger.info(
        "ingest run finished run_id=%s status=%s error_code=%s", run_id, status, error_code
    )


def list_runs(
    *,
    source_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    store.ensure_staging_available()
    conditions: list[str] = []
    params: list[Any] = []
    if source_id:
        conditions.append("source_id = ?")
        params.append(str(source_id))
    if status:
        if status not in RUN_STATUSES:
            raise AppError(422, "VALIDATION_FAILED", "运行状态筛选值非法")
        conditions.append("status = ?")
        params.append(status)
    # API 层 Query(le=100) 已拒绝 >100；此处收敛仅防御直接函数调用。
    try:
        safe_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        safe_limit = 20
    try:
        safe_offset = max(0, min(int(offset), 10_000))
    except (TypeError, ValueError):
        safe_offset = 0
    where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with store.staging_connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM ingest_run{where_sql}", tuple(params)
        ).fetchone()
        rows = conn.execute(
            f"""SELECT {_RUN_COLUMNS} FROM ingest_run{where_sql}
                ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?""",
            tuple([*params, safe_limit, safe_offset]),
        ).fetchall()
    return {
        "items": [_run_to_dict(row) for row in rows],
        "total": int(total_row["n"]) if total_row else 0,
        "limit": safe_limit,
        "offset": safe_offset,
    }


def get_run(run_id: str) -> dict[str, Any]:
    store.ensure_staging_available()
    with store.staging_connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM ingest_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise AppError(404, "SYNC_RUN_NOT_FOUND", f"同步运行不存在: {run_id}")
        tables = [
            dict(item)
            for item in conn.execute(
                """SELECT id, run_id, source_id, namespace, table_name, staging_name, status,
                          sync_mode, rows_read, rows_written, rows_skipped, bytes_written,
                          truncated, duration_ms, error_code, error_message, created_at, updated_at
                   FROM ingest_run_table WHERE run_id = ? ORDER BY id""",
                (run_id,),
            ).fetchall()
        ]
    item = _run_to_dict(row)
    for table in tables:
        table["truncated"] = bool(table.get("truncated"))
    item["tables"] = tables
    return item


def recover_interrupted_runs() -> dict[str, Any]:
    """心跳过期的 queued/running 置 interrupted；无活跃运行时清理孤儿临时表。"""
    store.ensure_staging_available()
    stale_seconds = max(1, int(settings.staging_run_stale_seconds or 300))
    threshold = (
        datetime.now().astimezone() - timedelta(seconds=stale_seconds)
    ).isoformat(timespec="seconds")
    now = store.staging_now()
    with store.staging_connection() as conn:
        cursor = conn.execute(
            """UPDATE ingest_run
               SET status = 'interrupted',
                   error_code = 'RUN_INTERRUPTED',
                   error_message = '服务重启或心跳超时，运行已中断',
                   finished_at = ?,
                   updated_at = ?
               WHERE status IN ('queued','running')
                 AND (heartbeat_at IS NULL OR heartbeat_at < ?)""",
            (now, now, threshold),
        )
        interrupted = max(0, int(cursor.rowcount or 0))
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM ingest_run WHERE status IN ('queued','running')"
        ).fetchone()
        orphans = 0
        if active is not None and int(active["n"]) == 0:
            orphans = writers.drop_orphan_temp_tables(conn)
    return {"interrupted": interrupted, "orphans_dropped": orphans}


def prune_runs(retention_days: int) -> int:
    """按保留天数删除运行与表级记录（目录与变更日志不清理）。"""
    try:
        days = max(1, int(retention_days))
    except (TypeError, ValueError):
        days = 180
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    with store.staging_connection() as conn:
        rows = conn.execute(
            "SELECT run_id FROM ingest_run WHERE created_at < ?", (cutoff,)
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in rows]
        if run_ids:
            conn.executemany(
                "DELETE FROM ingest_run_table WHERE run_id = ?",
                [(run_id,) for run_id in run_ids],
            )
            conn.executemany(
                "DELETE FROM ingest_run WHERE run_id = ?",
                [(run_id,) for run_id in run_ids],
            )
    return len(run_ids)
