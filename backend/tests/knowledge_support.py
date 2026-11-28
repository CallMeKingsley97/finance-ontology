"""知识包测试辅助：真实知识包复制、隔离配置、规则注入与库内计数。

所有辅助函数只操作 ``settings.knowledge_dir`` / ``settings.knowledge_db_path``
指向的临时目录，绝不读写仓库 ``data/*.db`` 或修改 ``backend/knowledge/*.json``。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.knowledge import loader as knowledge_loader
from app.services.knowledge import service as knowledge_service

REPO_PACK_DIR = Path(__file__).resolve().parents[1] / "knowledge"
PACK_VERSION = "2026.11.1"
EXPECTED_ENTRIES = 71
EXPECTED_SOURCES = 8

MUTATOR = Callable[[dict[str, Any]], None]


def copy_packs(target: Path) -> Path:
    """把仓库真实知识包复制到临时目录，供可变更场景使用。"""
    shutil.copytree(REPO_PACK_DIR, target)
    return target


def apply_env(monkeypatch: pytest.MonkeyPatch, pack_dir: Path, db_path: Path) -> None:
    """把知识包目录与知识库指向临时路径，并失效内存快照。"""
    monkeypatch.setattr(settings, "knowledge_dir", str(pack_dir))
    monkeypatch.setattr(settings, "knowledge_database_path", str(db_path))
    knowledge_service.invalidate_snapshot()


def load_packs(force: bool = True) -> dict[str, Any]:
    """调用真实加载器（自动初始化 knowledge.db）。"""
    return knowledge_loader.load_knowledge_packs(force=force)


def read_pack(pack_dir: Path, file_name: str) -> dict[str, Any]:
    return json.loads((pack_dir / file_name).read_text(encoding="utf-8"))


def update_pack(pack_dir: Path, file_name: str, mutate: MUTATOR) -> None:
    """读取-修改-写回知识包 JSON，用于构造校验/孤儿场景。"""
    data = read_pack(pack_dir, file_name)
    mutate(data)
    (pack_dir / file_name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def remove_knowledge_db() -> None:
    """删除知识库及 WAL/SHM 附属文件，模拟运行期库不可用。"""
    base = settings.knowledge_db_path
    for suffix in ("", "-wal", "-shm"):
        Path(f"{base}{suffix}").unlink(missing_ok=True)
    knowledge_service.invalidate_snapshot()


def knowledge_scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    """以普通连接读取知识库标量（仅测试断言用）。"""
    conn = sqlite3.connect(str(settings.knowledge_db_path))
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def insert_reconciliation_rule(
    entry_id: str,
    title: str,
    left_sql: str,
    right_sql: str,
) -> None:
    """直接写库注入一条知识勾稽规则。

    模拟知识包被扩充/篡改后 reload 的场景：loader 只校验 SQL 形式与来源，
    敏感列与非法关键字必须在执行前由 ``guard_sql`` 拦截。
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = json.dumps(
        {"left_sql": left_sql, "right_sql": right_sql},
        ensure_ascii=False,
        sort_keys=True,
    )
    conn = sqlite3.connect(str(settings.knowledge_db_path))
    try:
        conn.execute(
            """INSERT OR REPLACE INTO knowledge_entry
               (entry_id, kind, domain, title, statement, basis, source_id, version,
                effective_from, confidence, payload_json, pack_version, loaded_at)
               VALUES (?, 'reconciliation_rule', 'accounting_reconciliation', ?, ?, ?,
                       'double-entry', '1.0.0', '2026-01-01', 'practice', ?, ?, ?)""",
            (
                entry_id,
                title,
                f"{title}（TASK-04 测试注入）",
                "测试注入规则",
                payload,
                PACK_VERSION,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    knowledge_service.invalidate_snapshot()


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    """真实知识包的可变副本。"""
    return copy_packs(tmp_path / "knowledge_packs")


@pytest.fixture
def knowledge_env(
    pack_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """隔离知识包目录与 knowledge.db，测试结束后失效快照。"""
    apply_env(monkeypatch, pack_dir, tmp_path / "knowledge.db")
    yield pack_dir
    knowledge_service.invalidate_snapshot()


@pytest.fixture
def pack_client(knowledge_env: Path, seeded_db: dict) -> TestClient:
    """在隔离知识包上启动、且已完成业务播种的 TestClient。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def broken_pack_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seeded_db: dict
) -> TestClient:
    """知识包 JSON 非法的启动场景（应用不得因此 500）。"""
    broken = tmp_path / "broken_packs"
    broken.mkdir()
    (broken / "broken.json").write_text("{not-json", encoding="utf-8")
    apply_env(monkeypatch, broken, tmp_path / "knowledge.db")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def empty_pack_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seeded_db: dict
) -> TestClient:
    """知识包目录为空的启动场景（应用不得因此 500）。"""
    empty = tmp_path / "empty_packs"
    empty.mkdir()
    apply_env(monkeypatch, empty, tmp_path / "knowledge.db")
    with TestClient(app) as test_client:
        yield test_client
