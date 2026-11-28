"""knowledge 库：初始化、连接与可用性检查。

knowledge 为独立 SQLite 库（默认 ``data/knowledge.db``），与业务库、
``sources.db``、``staging.db`` 物理隔离；全部 DDL 使用 ``IF NOT EXISTS``，
重复初始化幂等。只读连接额外安装表白名单 authorizer，防止知识查询越权访问
业务表。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import settings

logger = logging.getLogger("finance_ontology.knowledge.store")

KNOWLEDGE_SCHEMA_VERSION = 1

KNOWLEDGE_TABLES: frozenset[str] = frozenset(
    {
        "knowledge_source",
        "knowledge_pack",
        "knowledge_entry",
        "knowledge_link",
        "knowledge_meta",
    }
)

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS knowledge_source (
    source_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('standard','opensource','practice')),
    license    TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    notice     TEXT NOT NULL DEFAULT '',
    version    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_pack (
    pack_id     TEXT PRIMARY KEY,
    domain      TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    file_name   TEXT NOT NULL UNIQUE,
    entry_count INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL CHECK (status IN ('loaded','rejected')),
    loaded_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_entry (
    entry_id       TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    domain         TEXT NOT NULL,
    title          TEXT NOT NULL,
    statement      TEXT NOT NULL,
    basis          TEXT NOT NULL,
    source_id      TEXT NOT NULL REFERENCES knowledge_source(source_id),
    version        TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    confidence     TEXT NOT NULL CHECK (confidence IN ('standard','opensource','practice')),
    payload_json   TEXT NOT NULL DEFAULT '{}',
    pack_version   TEXT NOT NULL,
    loaded_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_entry_kind ON knowledge_entry(kind, domain);
CREATE INDEX IF NOT EXISTS idx_knowledge_entry_source ON knowledge_entry(source_id);

CREATE TABLE IF NOT EXISTS knowledge_link (
    entry_id    TEXT NOT NULL REFERENCES knowledge_entry(entry_id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL,
    target_code TEXT NOT NULL,
    PRIMARY KEY (entry_id, target_kind, target_code)
);

CREATE TABLE IF NOT EXISTS knowledge_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

_initialized: set[str] = set()


def init_knowledge() -> None:
    """首次调用执行 DDL；重复调用为幂等。"""
    path = knowledge_db_path()
    key = str(path)
    if key in _initialized and path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {KNOWLEDGE_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _initialized.add(key)
    logger.info("knowledge initialized path=%s", path)


@contextmanager
def knowledge_connection() -> Iterator[sqlite3.Connection]:
    """knowledge 写连接：autocommit（isolation_level=None），busy_timeout=10000。

    多语句原子写入须显式 ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``；
    autocommit 下 ``conn.commit()`` / ``conn.rollback()`` 为 no-op。
    """
    init_knowledge()
    conn = sqlite3.connect(str(knowledge_db_path()), timeout=10.0)
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


def _readonly_authorizer(allowed_tables: frozenset[str]):
    """knowledge 只读守卫：仅放行 SELECT/READ/FUNCTION/RECURSIVE 与白名单表。"""
    allowed = frozenset(name.lower() for name in allowed_tables)
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", 33)

    def _authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,
        trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_READ:
            if arg1 is None or arg1.lower() in allowed:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, recursive):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return _authorizer


def knowledge_read_connection() -> sqlite3.Connection:
    """knowledge 只读连接：``mode=ro`` + ``query_only`` + 表白名单 authorizer。

    调用方负责 close；只允许读取 ``KNOWLEDGE_TABLES`` 中的表。
    PRAGMA 必须在安装 authorizer 之前执行。
    """
    conn = sqlite3.connect(
        knowledge_db_path().resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.set_authorizer(_readonly_authorizer(KNOWLEDGE_TABLES))
    return conn


def knowledge_available() -> bool:
    """库文件存在且 ``knowledge_entry`` 表可读时为 True。"""
    path = knowledge_db_path()
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=5.0,
            check_same_thread=False,
        )
        try:
            conn.execute("SELECT 1 FROM knowledge_entry LIMIT 1").fetchone()
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def knowledge_db_path() -> Path:
    """当前 knowledge 库路径（配置派生，不接受 API 输入）。"""
    return settings.knowledge_db_path


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """回滚且吞掉回滚失败，避免掩盖原始异常。"""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass
