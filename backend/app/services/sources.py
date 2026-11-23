"""数据源服务层：注册源 CRUD、文件上传、连接测试与连接器工厂。"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from app.connectors import (
    Connector,
    ConnectorError,
    CsvConnector,
    ExcelConnector,
    MetadataSnapshot,
    MySQLConnector,
    PostgresConnector,
    SQLiteFileConnector,
    TestResult,
    truncate_metadata,
)
from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, sanitize_error
from app.db.connection import database_info
from app.db.registry import (
    delete_source_row,
    get_source_row,
    insert_source_row,
    list_source_rows,
    update_source_row,
)
from app.schemas.common import AppError
from app.schemas.source import SourceCreateRequest

logger = logging.getLogger("finance_ontology.sources")

BUILTIN_SOURCE_ID = "sqlite-local"
FILE_KINDS = frozenset({"csv", "excel", "sqlite"})
DATABASE_KINDS = frozenset({"postgresql", "mysql"})
_CHUNK_SIZE = 1024 * 1024
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._\-一-鿿]")
_EXTENSIONS: dict[str, frozenset[str]] = {
    "csv": frozenset({".csv"}),
    "excel": frozenset({".xlsx", ".xlsm"}),
    "sqlite": frozenset({".db", ".sqlite", ".sqlite3"}),
}
_BLOCKED_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal", "metadata"})


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_source_id() -> str:
    return "src_" + uuid4().hex[:12]


def _load_config(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("config_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _builtin_source_item() -> dict[str, Any]:
    info = database_info()
    path = settings.db_path
    size_bytes = path.stat().st_size if path.exists() else 0
    last_seeded_at = None
    if path.exists():
        last_seeded_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
            timespec="seconds"
        )
    return {
        "id": BUILTIN_SOURCE_ID,
        "name": "本地 SQLite 财务库",
        "kind": "sqlite",
        "path": str(path),
        "status": "ready" if info["db_exists"] and info["table_count"] > 0 else "missing",
        "table_count": info["table_count"],
        "size_bytes": size_bytes,
        "last_seeded_at": last_seeded_at,
        "description": "内置演示库，包含业务与本体表",
        "managed": False,
        "has_secret": False,
        "config": {},
        "original_filename": None,
        "last_tested_at": None,
        "last_error": None,
        "metadata_updated_at": last_seeded_at,
    }


def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "path": "",
        "status": row["status"],
        "table_count": int(row.get("table_count") or 0),
        "size_bytes": int(row.get("size_bytes") or 0),
        "last_seeded_at": None,
        "description": "",
        "managed": True,
        "has_secret": bool(row.get("secret_ciphertext")),
        "config": _load_config(row),
        "original_filename": row.get("original_filename"),
        "last_tested_at": row.get("last_tested_at"),
        "last_error": row.get("last_error"),
        "metadata_updated_at": row.get("metadata_updated_at"),
    }


def list_sources() -> dict[str, Any]:
    items = [_builtin_source_item()]
    items.extend(_row_to_item(row) for row in list_source_rows())
    return {"items": items}


def get_source(source_id: str) -> dict[str, Any]:
    if source_id == BUILTIN_SOURCE_ID:
        return _builtin_source_item()
    row = get_source_row(source_id)
    if row is None:
        raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")
    return _row_to_item(row)


def _require_row(source_id: str) -> dict[str, Any]:
    row = get_source_row(source_id)
    if row is None:
        raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")
    return row


def _ensure_database_kind(kind: str) -> None:
    if kind not in DATABASE_KINDS:
        raise AppError(
            422,
            "SOURCE_KIND_UNSUPPORTED",
            "JSON 创建接口仅支持 postgresql/mysql，文件类型请使用上传接口",
        )


def _ensure_host_allowed(host: str) -> None:
    normalized = host.strip().lower()
    if normalized in _BLOCKED_HOSTS:
        raise AppError(422, "VALIDATION_FAILED", "禁止连接云元数据地址")
    allowed = settings.allowed_source_hosts
    if allowed and normalized not in allowed:
        raise AppError(422, "VALIDATION_FAILED", "目标主机不在允许列表中")


def create_database_source(payload: SourceCreateRequest) -> dict[str, Any]:
    """新增 PostgreSQL/MySQL 源；先登记 status=new，测试连接由独立端点触发。"""
    kind = (payload.kind or "").strip().lower()
    _ensure_database_kind(kind)
    name = (payload.name or "").strip()
    if not name or len(name) > 80:
        raise AppError(422, "VALIDATION_FAILED", "数据源名称长度需为 1-80 字符")
    config = payload.config
    if config is None:
        raise AppError(422, "VALIDATION_FAILED", "数据库连接配置不能为空")
    _ensure_host_allowed(config.host)
    secret = payload.secret or ""
    if not secret or len(secret) > 512:
        raise AppError(422, "VALIDATION_FAILED", "密码长度需为 1-512 字符")

    schema_value = config.schema_name or (
        "public" if kind == "postgresql" else config.database
    )
    source_id = _new_source_id()
    timestamp = _now()
    insert_source_row(
        {
            "id": source_id,
            "name": name,
            "kind": kind,
            "config_json": json.dumps(
                {
                    "host": config.host,
                    "port": int(config.port),
                    "database": config.database,
                    "username": config.username,
                    "schema": schema_value,
                },
                ensure_ascii=False,
            ),
            "secret_ciphertext": encrypt_secret(secret),
            "status": "new",
            "last_error": None,
            "last_tested_at": None,
            "table_count": 0,
            "metadata_json": None,
            "metadata_updated_at": None,
            "storage_path": None,
            "original_filename": None,
            "size_bytes": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    logger.info("source created id=%s kind=%s", source_id, kind)
    return get_source(source_id)


def _upload_limit(kind: str) -> int:
    if kind == "csv":
        return settings.source_max_csv_bytes
    if kind == "excel":
        return settings.source_max_excel_bytes
    return settings.source_max_sqlite_bytes


def _safe_upload_filename(filename: str | None) -> str:
    name = Path(filename or "file").name
    name = _SAFE_FILENAME_PATTERN.sub("_", name)
    return name[:120] or "file"


def _validate_extension(filename: str, kind: str) -> None:
    suffix = Path(filename).suffix.lower()
    if kind == "excel" and suffix == ".xls":
        raise AppError(422, "UPLOAD_INVALID_TYPE", "不支持 .xls，请另存为 .xlsx")
    allowed = _EXTENSIONS.get(kind, frozenset())
    if suffix not in allowed:
        raise AppError(
            422,
            "UPLOAD_INVALID_TYPE",
            f"文件扩展名不符，仅支持 {', '.join(sorted(allowed))}",
        )


def _stream_upload(
    upload: UploadFile,
    target_dir: Path,
    kind: str,
) -> tuple[Path, str, int]:
    """分块写盘并累计大小；超限抛 413，由调用方清理目录。"""
    original = _safe_upload_filename(upload.filename)
    _validate_extension(original, kind)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = target_dir / f"{uuid4().hex[:8]}_{original}"
    limit = _upload_limit(kind)
    total = 0
    with stored.open("wb") as handle:
        while True:
            chunk = upload.file.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise AppError(413, "UPLOAD_TOO_LARGE", f"文件超过 {limit} 字节上限")
            handle.write(chunk)
    if total == 0:
        raise AppError(422, "UPLOAD_PARSE_FAILED", "上传文件为空")
    return stored, original, total


def _validate_magic(path: Path, kind: str) -> None:
    with path.open("rb") as handle:
        head = handle.read(8192)
    if kind == "csv":
        if b"\x00" in head:
            raise AppError(422, "UPLOAD_INVALID_TYPE", "CSV 文件包含二进制内容")
        try:
            head.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                head.decode("gb18030")
            except UnicodeDecodeError as exc:
                raise AppError(422, "UPLOAD_INVALID_TYPE", "CSV 文件不是可识别文本") from exc
    elif kind == "excel":
        if not head.startswith(b"PK\x03\x04"):
            raise AppError(422, "UPLOAD_INVALID_TYPE", "Excel 文件魔数不符")
    elif kind == "sqlite":
        if not head.startswith(b"SQLite format 3\x00"):
            raise AppError(422, "UPLOAD_INVALID_TYPE", "SQLite 文件魔数不符")


def _inspector_for_file(kind: str, path: Path, config: dict[str, Any] | None = None) -> Connector:
    if kind == "sqlite":
        return SQLiteFileConnector(path)
    if kind == "csv":
        return CsvConnector(path, config)
    if kind == "excel":
        return ExcelConnector(path, config)
    raise ConnectorError("不支持的数据源类型")


def _parse_error(exc: ConnectorError) -> AppError:
    message = sanitize_error(str(exc))
    if message.startswith("UPLOAD_SUSPICIOUS_ARCHIVE"):
        return AppError(422, "UPLOAD_SUSPICIOUS_ARCHIVE", message, {"error": message})
    if message.startswith("EXCEL_LEGACY_UNSUPPORTED"):
        return AppError(422, "UPLOAD_INVALID_TYPE", message, {"error": message})
    return AppError(422, "UPLOAD_PARSE_FAILED", "文件解析失败", {"error": message})


def _inspect_file(connector: Connector) -> MetadataSnapshot:
    try:
        snapshot = connector.inspect()
    except ConnectorError as exc:
        raise _parse_error(exc) from exc
    except Exception as exc:
        message = sanitize_error(str(exc))
        raise AppError(422, "UPLOAD_PARSE_FAILED", "文件解析失败", {"error": message}) from exc
    finally:
        connector.close()
    return truncate_metadata(
        snapshot,
        settings.source_max_tables,
        settings.source_max_columns,
        settings.source_max_metadata_bytes,
    )


def _file_config(kind: str, connector: Connector, snapshot: MetadataSnapshot, original: str) -> dict[str, Any]:
    base: dict[str, Any] = {"file_name": original}
    if kind == "csv":
        base.update(
            {
                "encoding": getattr(connector, "encoding", ""),
                "delimiter": getattr(connector, "delimiter", ","),
                "header": bool(getattr(connector, "has_header", False)),
                "sheet_count": 0,
            }
        )
    elif kind == "excel":
        base.update({"encoding": "", "delimiter": "", "header": True, "sheet_count": len(snapshot.tables)})
    else:
        base.update({"sheet_count": 0})
    return base


def _snapshot_json(snapshot: MetadataSnapshot) -> str:
    return json.dumps(asdict(snapshot), ensure_ascii=False, default=str)


def create_file_source(name: str, kind: str, upload: UploadFile) -> dict[str, Any]:
    """上传 CSV/Excel/SQLite 并创建文件源；解析成功后才写注册行。"""
    normalized_kind = (kind or "").strip().lower()
    if normalized_kind not in FILE_KINDS:
        raise AppError(
            422,
            "SOURCE_KIND_UNSUPPORTED",
            "上传接口仅支持 csv/excel/sqlite",
        )
    source_id = _new_source_id()
    target_dir = settings.uploads_dir / source_id
    try:
        stored, original, size = _stream_upload(upload, target_dir, normalized_kind)
        _validate_magic(stored, normalized_kind)
        connector = _inspector_for_file(normalized_kind, stored, {"file_name": original})
        snapshot = _inspect_file(connector)
        config = _file_config(normalized_kind, connector, snapshot, original)
        provided_name = (name or "").strip()
        if provided_name and len(provided_name) > 80:
            raise AppError(422, "VALIDATION_FAILED", "数据源名称长度需为 1-80 字符")
        display_name = provided_name or Path(original).stem[:80] or "未命名文件源"
        timestamp = _now()
        insert_source_row(
            {
                "id": source_id,
                "name": display_name,
                "kind": normalized_kind,
                "config_json": json.dumps(config, ensure_ascii=False),
                "secret_ciphertext": None,
                "status": "ready",
                "last_error": None,
                "last_tested_at": None,
                "table_count": len(snapshot.tables),
                "metadata_json": _snapshot_json(snapshot),
                "metadata_updated_at": timestamp,
                "storage_path": str(stored),
                "original_filename": original,
                "size_bytes": size,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    logger.info("file source created id=%s kind=%s size=%s", source_id, normalized_kind, size)
    return get_source(source_id)


def _safe_remove_dir(directory: Path | None) -> None:
    if directory is None:
        return
    root = settings.uploads_dir.resolve()
    try:
        resolved = directory.resolve()
    except OSError:
        return
    if resolved == root or not resolved.is_relative_to(root):
        return
    shutil.rmtree(resolved, ignore_errors=True)


def _storage_dir(row: dict[str, Any]) -> Path | None:
    raw = row.get("storage_path")
    return Path(raw).parent if raw else None


def replace_file_source(source_id: str, upload: UploadFile) -> dict[str, Any]:
    """替换文件源存储文件；解析或登记失败时恢复旧文件与旧元数据。"""
    if source_id == BUILTIN_SOURCE_ID:
        raise AppError(409, "BUILTIN_SOURCE", "内置数据源不支持替换文件")
    row = _require_row(source_id)
    kind = row["kind"]
    if kind not in FILE_KINDS:
        raise AppError(409, "SOURCE_NOT_FILE", "仅文件源支持替换文件")

    final_dir = settings.uploads_dir / source_id
    temp_dir = settings.uploads_dir / f".tmp-{source_id}-{uuid4().hex[:8]}"
    backup_dir = settings.uploads_dir / f".bak-{source_id}-{uuid4().hex[:8]}"
    old_dir = _storage_dir(row)
    backup_created = False
    new_moved = False
    try:
        stored_temp, original, size = _stream_upload(upload, temp_dir, kind)
        _validate_magic(stored_temp, kind)
        connector = _inspector_for_file(kind, stored_temp, {"file_name": original})
        snapshot = _inspect_file(connector)
        config = _file_config(kind, connector, snapshot, original)
        if final_dir.exists():
            final_dir.rename(backup_dir)
            backup_created = True
        temp_dir.rename(final_dir)
        new_moved = True
        stored = final_dir / stored_temp.name
        timestamp = _now()
        update_source_row(
            source_id,
            config_json=json.dumps(config, ensure_ascii=False),
            status="ready",
            last_error=None,
            table_count=len(snapshot.tables),
            metadata_json=_snapshot_json(snapshot),
            metadata_updated_at=timestamp,
            storage_path=str(stored),
            original_filename=original,
            size_bytes=size,
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if new_moved and final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        if backup_created and backup_dir.exists() and not final_dir.exists():
            try:
                backup_dir.rename(final_dir)
            except OSError:
                logger.exception("failed to restore source backup id=%s", source_id)
        raise
    # update_source_row 成功即提交点；后续清理失败不得回滚或破坏新源。
    if backup_created:
        _safe_remove_dir(backup_dir)
    if old_dir is not None and old_dir.resolve() != final_dir.resolve():
        _safe_remove_dir(old_dir)
    logger.info("file source replaced id=%s kind=%s size=%s", source_id, kind, size)
    return get_source(source_id)


def delete_source(source_id: str) -> dict[str, Any]:
    if source_id == BUILTIN_SOURCE_ID:
        raise AppError(409, "BUILTIN_SOURCE", "内置数据源不可删除")
    row = _require_row(source_id)
    if not delete_source_row(source_id):
        raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")
    _safe_remove_dir(_storage_dir(row))
    logger.info("source deleted id=%s kind=%s", source_id, row["kind"])
    return {"deleted": True, "id": source_id}


def _row_secrets(row: dict[str, Any]) -> list[str]:
    token = row.get("secret_ciphertext")
    if not token:
        return []
    try:
        return [decrypt_secret(token)]
    except AppError:
        return []


def _sanitize_test_error(exc: Exception, row: dict[str, Any] | None) -> str:
    secrets = _row_secrets(row) if row else []
    return sanitize_error(str(exc), secrets)


def _test_payload(result: TestResult, kind: str) -> dict[str, Any]:
    return {
        "success": bool(result.success),
        "latency_ms": int(result.latency_ms),
        "sqlite_version": result.server_version if kind == "sqlite" else None,
        "server_version": result.server_version,
        "table_count": int(result.table_count),
        "latest_period": result.latest_period,
        "message": result.message,
    }


def test_source(source_id: str) -> dict[str, Any]:
    """测试连接；成功写 ready，失败写 failed 并返回 422 SOURCE_TEST_FAILED。"""
    row: dict[str, Any] | None
    if source_id == BUILTIN_SOURCE_ID:
        row = None
        kind = "sqlite"
        connector: Connector = SQLiteFileConnector(settings.db_path)
    else:
        row = _require_row(source_id)
        kind = row["kind"]
        try:
            connector = _connector_for_row(row)
        except AppError as exc:
            try:
                update_source_row(
                    source_id,
                    status="failed",
                    last_error=sanitize_error(exc.message),
                    last_tested_at=_now(),
                )
            except Exception:
                logger.exception("failed to persist source test status id=%s", source_id)
            raise
        except ConnectorError as exc:
            message = _sanitize_test_error(exc, row)
            update_source_row(source_id, status="failed", last_error=message, last_tested_at=_now())
            raise AppError(422, "SOURCE_TEST_FAILED", "连接测试失败", {"error": message}) from exc
        except Exception as exc:
            message = _sanitize_test_error(exc, row)
            update_source_row(source_id, status="failed", last_error=message, last_tested_at=_now())
            raise AppError(422, "SOURCE_TEST_FAILED", "连接测试失败", {"error": message}) from exc

    started = time.perf_counter()
    try:
        result = connector.test()
    except ConnectorError as exc:
        message = _sanitize_test_error(exc, row)
        if row is not None:
            update_source_row(
                source_id,
                status="failed",
                last_error=message,
                last_tested_at=_now(),
            )
        raise AppError(422, "SOURCE_TEST_FAILED", "连接测试失败", {"error": message}) from exc
    except Exception as exc:
        message = _sanitize_test_error(exc, row)
        if row is not None:
            update_source_row(
                source_id,
                status="failed",
                last_error=message,
                last_tested_at=_now(),
            )
        raise AppError(422, "SOURCE_TEST_FAILED", "连接测试失败", {"error": message}) from exc
    finally:
        connector.close()

    if not result.success:
        raise AppError(
            422,
            "SOURCE_TEST_FAILED",
            "连接测试失败",
            {"error": sanitize_error(result.message or "连接不可用")},
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    result.latency_ms = elapsed
    if row is not None:
        update_source_row(
            source_id,
            status="ready",
            last_error=None,
            last_tested_at=_now(),
            table_count=int(result.table_count),
        )
    logger.info("source tested id=%s kind=%s success=%s latency_ms=%s", source_id, kind, True, elapsed)
    return _test_payload(result, kind)


def test_legacy_sqlite(path: str | None) -> dict[str, Any]:
    """兼容旧 POST /api/sources/test：仅允许内置库或上传目录下的 SQLite。"""
    target = Path(path).expanduser().resolve() if path else settings.db_path
    if not target.exists():
        raise AppError(422, "TEST_FAILED", f"数据库文件不存在: {target}")
    if target.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
        raise AppError(422, "TEST_FAILED", "仅支持 .db/.sqlite/.sqlite3 文件")
    builtin = settings.db_path.resolve()
    uploads = settings.uploads_dir.resolve()
    allowed = target == builtin or target.is_relative_to(uploads)
    if not allowed:
        raise AppError(422, "PATH_NOT_ALLOWED", "路径不在允许范围内")

    connector = SQLiteFileConnector(target)
    try:
        result = connector.test()
    except ConnectorError as exc:
        raise AppError(422, "TEST_FAILED", "SQLite 连接测试失败", {"error": sanitize_error(str(exc))}) from exc
    finally:
        connector.close()
    return {
        "success": True,
        "latency_ms": result.latency_ms,
        "sqlite_version": result.server_version,
        "table_count": result.table_count,
        "latest_period": result.latest_period,
    }


def _connector_for_row(row: dict[str, Any]) -> Connector:
    """按注册行构造连接器；数据库凭据仅在内存解密。"""
    kind = row["kind"]
    config = _load_config(row)
    storage_path = row.get("storage_path")
    if kind in FILE_KINDS:
        if not storage_path:
            raise ConnectorError("数据源缺少存储文件")
        return _inspector_for_file(kind, Path(storage_path), config)

    if kind not in DATABASE_KINDS:
        raise ConnectorError("不支持的数据源类型")
    token = row.get("secret_ciphertext")
    if not token:
        raise ConnectorError("数据源缺少凭据")
    secret = decrypt_secret(token)
    host = str(config.get("host") or "")
    database = str(config.get("database") or "")
    username = str(config.get("username") or "")
    schema = str(config.get("schema") or "")
    if not host or not database or not username:
        raise ConnectorError("数据源连接配置不完整")
    if kind == "postgresql":
        return PostgresConnector(
            host=host,
            port=int(config.get("port") or 5432),
            database=database,
            username=username,
            password=secret,
            schema=schema or "public",
        )
    return MySQLConnector(
        host=host,
        port=int(config.get("port") or 3306),
        database=database,
        username=username,
        password=secret,
        schema=schema or database,
    )
