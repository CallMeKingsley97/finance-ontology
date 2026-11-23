"""多数据源测试辅助：内存/临时文件构造、上传调用与敏感字段断言。"""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import settings
from tests.support import headers

# CR-001 回归用的唯一标记；响应中绝不应出现。
LEAK_MARKER = "LEAKME_CR1_SECRET"

# 数据源对象中永远不允许出现的字段（secret 为精确键名，不会误伤 has_secret）。
FORBIDDEN_KEYS = frozenset({"secret", "secret_ciphertext", "storage_path"})


def csv_bytes(
    rows: Iterable[Iterable[Any]],
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> bytes:
    """把二维数据编码为 CSV bytes，便于直接作为上传内容。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode(encoding)


def xlsx_bytes(sheets: dict[str, list[list[Any]]]) -> bytes:
    """用 openpyxl 在内存中生成多 sheet 工作簿；不提交二进制夹具。"""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        worksheet = workbook.create_sheet(title=title)
        for row in rows:
            worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_sqlite_db(path: Path) -> Path:
    """创建带主键/外键/索引/数据的临时 SQLite 文件。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE parent (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE child (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL,
                amount REAL,
                note TEXT,
                FOREIGN KEY (parent_id) REFERENCES parent(id)
            );
            CREATE UNIQUE INDEX idx_child_parent ON child(parent_id);
            INSERT INTO parent (id, name) VALUES (1, 'alpha'), (2, 'beta');
            INSERT INTO child (id, parent_id, amount, note)
            VALUES (1, 1, 10.5, 'first'), (2, 2, 20.25, 'second');
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def sqlite_bytes(path: Path) -> bytes:
    """把临时 SQLite 文件读成 bytes，用于上传。"""
    return path.read_bytes()


def upload_file(
    client: TestClient,
    *,
    filename: str,
    content: bytes,
    kind: str,
    name: str = "",
    role: str = "admin",
):
    """调用 POST /api/sources/upload。"""
    return client.post(
        "/api/sources/upload",
        data={"name": name, "kind": kind},
        files={"file": (filename, content, "application/octet-stream")},
        headers=headers(role),
    )


def replace_file(
    client: TestClient,
    source_id: str,
    *,
    filename: str,
    content: bytes,
    role: str = "admin",
):
    """调用 POST /api/sources/{id}/file。"""
    return client.post(
        f"/api/sources/{source_id}/file",
        files={"file": (filename, content, "application/octet-stream")},
        headers=headers(role),
    )


def upload_ok(
    client: TestClient,
    *,
    filename: str,
    content: bytes,
    kind: str,
    name: str = "",
) -> dict[str, Any]:
    """上传并断言 201，返回源对象。"""
    response = upload_file(client, filename=filename, content=content, kind=kind, name=name)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["code"] == "OK", payload
    return payload["data"]


def replace_ok(
    client: TestClient,
    source_id: str,
    *,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    """替换文件并断言 200，返回源对象。"""
    response = replace_file(
        client, source_id, filename=filename, content=content
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "OK", payload
    return payload["data"]


def upload_artifacts() -> list[Path]:
    """返回上传根目录下的直接子项（源目录与临时/备份目录）。"""
    root = settings.uploads_dir
    return sorted(root.iterdir()) if root.exists() else []


def residual_temp_dirs() -> list[Path]:
    """返回遗留的 .tmp-* / .bak-* 目录。"""
    return [
        item
        for item in upload_artifacts()
        if item.name.startswith((".tmp-", ".bak-"))
    ]


def assert_no_secret_fields(payload: Any) -> None:
    """递归断言响应中没有明文 secret、密文或服务器存储路径。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in FORBIDDEN_KEYS, f"响应包含敏感字段: {key}"
            assert_no_secret_fields(value)
    elif isinstance(payload, list):
        for item in payload:
            assert_no_secret_fields(item)
