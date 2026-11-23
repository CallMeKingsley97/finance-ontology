"""同步编排：daemon 线程执行、源级重试、单表全量换表/增量 upsert。

约束：
- API 只负责 claim 并立即返回，全量同步在后台线程执行；
- 每个线程使用独立的 staging 连接，不跨线程共享 sqlite 连接；
- 单源互斥由 ``uq_ingest_run_active_source`` 保证；
- 表级失败不触发整源重试，run 记 partial/failed；
- 错误统一经 ``sanitize_error`` 脱敏并截断 300 字。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app.connectors.base import ConnectorError, MetadataSnapshot, TableMeta, truncate_metadata
from app.core.config import settings
from app.core.crypto import sanitize_error
from app.db.registry import get_source_row
from app.schemas.common import AppError
from app.services.ingest import readers, runs, store, writers
from app.services.sources import (
    BUILTIN_SOURCE_ID,
    FILE_KINDS,
    _connector_for_row,
    _row_secrets,
)

logger = logging.getLogger("finance_ontology.ingest.orchestrator")

NO_PRIMARY_KEY_WARNING = "NO_PRIMARY_KEY_FULL_REFRESH"
SCHEMA_CHANGED_WARNING = "SCHEMA_CHANGED_FULL_REFRESH"
ROW_LIMIT_WARNING = "ROW_LIMIT_REACHED"
BYTE_LIMIT_WARNING = "BYTE_LIMIT_REACHED"
VIEWS_SKIPPED_WARNING = "VIEWS_SKIPPED"
CATALOG_FAILED_WARNING = "CATALOG_SYNC_FAILED"


def _catalog():
    """按需导入目录模块，避免模块级循环依赖。"""
    from app.services.ingest import catalog as catalog_module

    return catalog_module


def _columns_json(columns: list[tuple[str, str]]) -> str:
    return json.dumps(
        [{"name": name, "data_type": data_type} for name, data_type in columns],
        ensure_ascii=False,
    )


def _staging_schema(conn: Any, source_id: str, namespace: str, table_name: str) -> str | None:
    row = conn.execute(
        """SELECT columns_json FROM staging_table
           WHERE source_id = ? AND namespace = ? AND table_name = ?""",
        (source_id, namespace, table_name),
    ).fetchone()
    return str(row["columns_json"]) if row else None


def _upsert_staging_table(
    conn: Any,
    *,
    source_id: str,
    namespace: str,
    table_name: str,
    staging_name: str,
    columns_json: str,
    primary_key_json: str,
    watermark_column: str | None,
    watermark_value: str | None,
    sync_mode: str,
    row_count: int,
    run_id: str,
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO staging_table
           (source_id, namespace, table_name, staging_name, columns_json, primary_key_json,
            watermark_column, watermark_value, sync_mode, row_count, last_run_id,
            last_success_at, status, last_error, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'healthy', NULL, ?, ?)
           ON CONFLICT(source_id, namespace, table_name) DO UPDATE SET
               staging_name = excluded.staging_name,
               columns_json = excluded.columns_json,
               primary_key_json = excluded.primary_key_json,
               watermark_column = excluded.watermark_column,
               watermark_value = excluded.watermark_value,
               sync_mode = excluded.sync_mode,
               row_count = excluded.row_count,
               last_run_id = excluded.last_run_id,
               last_success_at = excluded.last_success_at,
               status = 'healthy',
               last_error = NULL,
               updated_at = excluded.updated_at""",
        (
            source_id,
            namespace,
            table_name,
            staging_name,
            columns_json,
            primary_key_json,
            watermark_column,
            watermark_value,
            sync_mode,
            int(row_count),
            run_id,
            now,
            now,
            now,
        ),
    )


def _mark_staging_error(
    source_id: str, namespace: str, table_name: str, message: str
) -> None:
    try:
        with store.staging_connection() as conn:
            conn.execute(
                """UPDATE staging_table
                   SET status = 'error', last_error = ?, updated_at = ?
                   WHERE source_id = ? AND namespace = ? AND table_name = ?""",
                (message[:300], store.staging_now(), source_id, namespace, table_name),
            )
    except Exception:  # noqa: BLE001 - 错误标记失败不覆盖原始表级错误
        logger.exception("failed to mark staging error source=%s table=%s", source_id, table_name)


def _sync_table(
    connector: Any,
    table: TableMeta,
    source_row: dict[str, Any],
    run_id: str,
    secrets: list[str],
) -> dict[str, Any]:
    del secrets  # 错误脱敏在 _sync_snapshot 统一处理
    source_id = str(source_row["id"])
    namespace = table.schema or ""
    table_name = table.name
    table_columns = [(column.name, column.data_type or "TEXT") for column in table.columns]
    if not table_columns:
        raise ConnectorError(f"表 {table_name} 没有可同步的列")
    pk_columns = [column.name for column in table.columns if column.is_primary_key]
    staging_name = writers.staging_name_for(source_id, namespace, table_name)
    columns_json = _columns_json(table_columns)

    with store.staging_connection() as probe:
        previous_schema = _staging_schema(probe, source_id, namespace, table_name)
    schema_changed = previous_schema is not None and previous_schema != columns_json

    settings_info = _catalog().sync_settings(source_id, namespace, table_name)
    watermark_column = settings_info.get("watermark_column")
    watermark_value = settings_info.get("watermark_value")
    file_source = str(source_row.get("kind") or "") in FILE_KINDS

    mode = "full"
    if not file_source and pk_columns and watermark_column and watermark_value:
        mode = "incremental"
    if mode == "incremental" and schema_changed:
        mode = "full"

    table_warnings: list[str] = []
    if schema_changed:
        table_warnings.append(SCHEMA_CHANGED_WARNING)
    if not pk_columns:
        table_warnings.append(NO_PRIMARY_KEY_WARNING)

    batch_size = max(1, min(int(settings.staging_batch_size or 1000), 5000))
    max_rows = max(1, int(settings.staging_max_rows_per_table or 200_000))
    max_bytes = max(1, int(settings.staging_max_bytes_per_table or 67_108_864))
    column_names = [name for name, _ in table_columns]

    rows_read = 0
    rows_written = 0
    rows_skipped = 0
    bytes_written = 0
    truncated = False
    new_watermark: str | None = None

    with store.staging_connection() as conn:
        def commit_metadata() -> None:
            """在同一显式事务内更新 staging_table 元数据。"""
            _upsert_staging_table(
                conn,
                source_id=source_id,
                namespace=namespace,
                table_name=table_name,
                staging_name=staging_name,
                columns_json=columns_json,
                primary_key_json=json.dumps(pk_columns, ensure_ascii=False),
                watermark_column=watermark_column,
                watermark_value=new_watermark,
                sync_mode=mode,
                row_count=row_count,
                run_id=run_id,
                now=store.staging_now(),
            )

        if mode == "full":
            temp_name = staging_name + "__new"
            writers.drop_table_if_exists(conn, temp_name)
            writers.create_table(conn, temp_name, table_columns)
            for batch in readers.iter_batches(
                connector,
                table,
                watermark_column=None,
                watermark_value=None,
                batch_size=batch_size,
                max_rows=max_rows,
            ):
                written, batch_bytes = writers.insert_rows(
                    conn, temp_name, column_names, batch, pk_columns
                )
                rows_read += len(batch)
                rows_written += written
                rows_skipped += max(0, len(batch) - written)
                bytes_written += batch_bytes
                runs.touch_run(
                    run_id,
                    rows_read=len(batch),
                    rows_written=written,
                    rows_skipped=max(0, len(batch) - written),
                    bytes_written=batch_bytes,
                )
                if rows_read >= max_rows:
                    truncated = True
                    table_warnings.append(ROW_LIMIT_WARNING)
                    break
                if bytes_written >= max_bytes:
                    truncated = True
                    table_warnings.append(BYTE_LIMIT_WARNING)
                    break
            conn.execute("BEGIN IMMEDIATE")
            try:
                writers.swap_table(conn, staging_name, manage_transaction=False)
                row_count = writers.table_row_count(conn, staging_name)
                if watermark_column and pk_columns:
                    new_watermark = writers.table_max_value(conn, staging_name, watermark_column)
                commit_metadata()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        else:
            writers.create_table(conn, staging_name, table_columns)
            for batch in readers.iter_batches(
                connector,
                table,
                watermark_column=watermark_column,
                watermark_value=watermark_value,
                batch_size=batch_size,
                max_rows=max_rows,
            ):
                written, batch_bytes = writers.insert_rows(
                    conn, staging_name, column_names, batch, pk_columns
                )
                rows_read += len(batch)
                rows_written += written
                rows_skipped += max(0, len(batch) - written)
                bytes_written += batch_bytes
                runs.touch_run(
                    run_id,
                    rows_read=len(batch),
                    rows_written=written,
                    rows_skipped=max(0, len(batch) - written),
                    bytes_written=batch_bytes,
                )
                if rows_read >= max_rows:
                    truncated = True
                    table_warnings.append(ROW_LIMIT_WARNING)
                    break
                if bytes_written >= max_bytes:
                    truncated = True
                    table_warnings.append(BYTE_LIMIT_WARNING)
                    break
            conn.execute("BEGIN IMMEDIATE")
            try:
                row_count = writers.table_row_count(conn, staging_name)
                if watermark_column:
                    new_watermark = (
                        writers.table_max_value(conn, staging_name, watermark_column)
                        or watermark_value
                    )
                else:
                    new_watermark = watermark_value
                commit_metadata()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    return {
        "staging_name": staging_name,
        "mode": mode,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "rows_skipped": rows_skipped,
        "bytes_written": bytes_written,
        "truncated": truncated,
        "warnings": table_warnings,
    }


def _sync_snapshot(
    connector: Any,
    snapshot: MetadataSnapshot,
    source_row: dict[str, Any],
    run_id: str,
    secrets: list[str],
) -> dict[str, Any]:
    source_id = str(source_row["id"])
    tables = [table for table in snapshot.tables if table.table_type in ("table", "file")]
    skipped_views = len(snapshot.tables) - len(tables)
    warnings = list(snapshot.warnings)
    if skipped_views:
        warnings.append(VIEWS_SKIPPED_WARNING)
    runs.touch_run(run_id, tables_total=len(tables))

    succeeded = 0
    failed = 0
    for table in tables:
        started = time.perf_counter()
        namespace = table.schema or ""
        staging_name = writers.staging_name_for(source_id, namespace, table.name)
        try:
            result = _sync_table(connector, table, source_row, run_id, secrets)
            duration_ms = int((time.perf_counter() - started) * 1000)
            succeeded += 1
            runs.record_table_result(
                run_id,
                source_id=source_id,
                namespace=namespace,
                table_name=table.name,
                staging_name=result["staging_name"],
                status="succeeded",
                sync_mode=result["mode"],
                rows_read=result["rows_read"],
                rows_written=result["rows_written"],
                rows_skipped=result["rows_skipped"],
                bytes_written=result["bytes_written"],
                truncated=int(result["truncated"]),
                duration_ms=duration_ms,
            )
            runs.touch_run(
                run_id, tables_succeeded=1, truncated=int(result["truncated"])
            )
            warnings.extend(result["warnings"])
        except Exception as exc:  # noqa: BLE001 - 表级失败不影响其他表
            failed += 1
            duration_ms = int((time.perf_counter() - started) * 1000)
            message = sanitize_error(str(exc), secrets)
            logger.warning(
                "ingest table failed run_id=%s source_id=%s table=%s error=%s",
                run_id,
                source_id,
                table.name,
                message,
            )
            runs.record_table_result(
                run_id,
                source_id=source_id,
                namespace=namespace,
                table_name=table.name,
                staging_name=staging_name,
                status="failed",
                sync_mode="full",
                duration_ms=duration_ms,
                error_code="TABLE_SYNC_FAILED",
                error_message=message,
            )
            runs.touch_run(run_id, tables_failed=1)
            _mark_staging_error(source_id, namespace, table.name, message)

    return {"succeeded": succeeded, "failed": failed, "warnings": warnings}


def _execute_run(run_id: str, source_id: str) -> None:
    max_retries = max(0, int(settings.staging_max_retries or 0))
    attempts_allowed = 1 + max_retries
    backoff = float(settings.staging_retry_backoff_seconds or 1.0)
    connector: Any = None
    secrets: list[str] = []
    logger.info("ingest run executing run_id=%s source_id=%s", run_id, source_id)

    for attempt in range(attempts_allowed):
        runs.mark_running(run_id)
        try:
            row = get_source_row(source_id)
            if row is None:
                raise AppError(404, "SOURCE_NOT_FOUND", "数据源不存在")
            secrets = _row_secrets(row)
            connector = _connector_for_row(row)
            snapshot = connector.inspect()
            snapshot = truncate_metadata(
                snapshot,
                settings.source_max_tables,
                settings.source_max_columns,
                settings.source_max_metadata_bytes,
            )
            result = _sync_snapshot(connector, snapshot, row, run_id, secrets)
            warnings = list(result["warnings"])
            try:
                _catalog().sync_source_catalog(source_id, snapshot, run_id)
            except Exception as exc:  # noqa: BLE001 - 目录失败不覆盖同步结果
                logger.exception("catalog sync failed run_id=%s source_id=%s", run_id, source_id)
                warnings.append(CATALOG_FAILED_WARNING)
                del exc
            if result["failed"] == 0:
                status = "succeeded"
            elif result["succeeded"] > 0:
                status = "partial"
            else:
                status = "failed"
            runs.finish_run(run_id, status, warnings=warnings)
            logger.info(
                "ingest run done run_id=%s status=%s tables_succeeded=%s tables_failed=%s",
                run_id,
                status,
                result["succeeded"],
                result["failed"],
            )
            try:
                from app.services import quality

                quality.run_after_sync(source_id, run_id, status)
            except Exception:  # noqa: BLE001 - 质量检查不得影响同步状态
                logger.exception("quality hook failed run_id=%s source_id=%s", run_id, source_id)
            return
        except AppError as exc:
            runs.finish_run(
                run_id,
                "failed",
                error_code=exc.code,
                error_message=sanitize_error(exc.message, secrets),
            )
            return
        except Exception as exc:  # noqa: BLE001
            message = sanitize_error(str(exc), secrets)
            if attempt < max_retries:
                logger.warning(
                    "ingest run retry run_id=%s attempt=%s/%s error=%s",
                    run_id,
                    attempt + 1,
                    attempts_allowed,
                    message,
                )
                runs.finish_run(
                    run_id,
                    "failed",
                    error_code="SYNC_RETRY",
                    error_message=message,
                )
                time.sleep(min(backoff * (2**attempt), 30.0))
                continue
            logger.error(
                "ingest run dead letter run_id=%s attempts=%s error=%s",
                run_id,
                attempt + 1,
                message,
            )
            runs.finish_run(
                run_id,
                "dead_letter",
                error_code="SYNC_DEAD_LETTER",
                error_message=message,
            )
            return
        finally:
            if connector is not None:
                try:
                    connector.close()
                except Exception:  # noqa: BLE001
                    logger.exception("connector close failed run_id=%s", run_id)
                connector = None


def _execute_run_safe(run_id: str, source_id: str) -> None:
    try:
        _execute_run(run_id, source_id)
    except Exception as exc:  # noqa: BLE001 - 守护线程兜底
        logger.exception("ingest worker crashed run_id=%s: %s", run_id, exc)
        try:
            runs.finish_run(
                run_id,
                "failed",
                error_code="WORKER_CRASHED",
                error_message="同步工作线程异常终止",
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark crashed run run_id=%s", run_id)


def start_single_sync(source_id: str, trigger_role: str) -> dict[str, Any]:
    """校验源后 claim 并启动 daemon 线程，立即返回 run。"""
    store.ensure_staging_available()
    if source_id == BUILTIN_SOURCE_ID:
        raise AppError(409, "BUILTIN_SOURCE", "内置数据源不支持同步")
    row = get_source_row(source_id)
    if row is None:
        raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")
    run = runs.create_run(row, trigger_type="manual", trigger_role=trigger_role)
    thread = threading.Thread(
        target=_execute_run_safe,
        args=(run["run_id"], source_id),
        name=f"ingest-{run['run_id']}",
        daemon=True,
    )
    thread.start()
    return run


def start_batch_worker(batch: dict[str, Any]) -> None:
    """单线程顺序执行 batch 内的 run，避免 SQLite 多写者争抢。"""
    items = [
        (str(item["run_id"]), str(item["source_id"]))
        for item in batch.get("runs") or []
    ]
    if not items:
        return

    def _run_sequential() -> None:
        for run_id, source_id in items:
            _execute_run_safe(run_id, source_id)

    thread = threading.Thread(
        target=_run_sequential,
        name=f"ingest-batch-{batch.get('batch_id') or 'unknown'}",
        daemon=True,
    )
    thread.start()
