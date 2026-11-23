"""指标/别名测试辅助：创建、认证与别名维护（只通过公开 API）。"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from app.services.ingest import store
from tests.support import headers

DEFAULT_FORMULA = "SELECT 1.0 AS value WHERE :start_period = :end_period"


def created_data(response) -> dict[str, Any]:
    """断言 HTTP 201 且成功信封 code=OK，返回 data。"""
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload.get("code") == "OK", payload
    return payload["data"]


def create_metric(
    client: TestClient,
    code: str,
    name: str | None = None,
    *,
    role: str = "admin",
    formula_sql: str = DEFAULT_FORMULA,
    **fields: Any,
) -> dict[str, Any]:
    """创建一个 draft 指标，返回详情。"""
    payload = {"code": code, "name": name or code, "formula_sql": formula_sql, **fields}
    response = client.post("/api/metrics", json=payload, headers=headers(role))
    return created_data(response)


def certify_metric(
    client: TestClient,
    code: str,
    version: str = "v0.1",
    *,
    role: str = "admin",
) -> dict[str, Any]:
    """认证指定版本，返回最新详情（测试内断言状态）。"""
    response = client.post(
        f"/api/metrics/{code}/versions/{version}/certify",
        json={},
        headers=headers(role),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def add_alias(
    client: TestClient,
    code: str,
    alias: str,
    *,
    alias_type: str = "business",
    priority: int = 0,
    role: str = "admin",
) -> dict[str, Any]:
    """为指标新增别名。"""
    response = client.post(
        f"/api/metrics/{code}/aliases",
        json={"alias": alias, "alias_type": alias_type, "priority": priority},
        headers=headers(role),
    )
    return created_data(response)


def certified_metric_with_alias(
    client: TestClient,
    code: str,
    alias: str,
    *,
    priority: int = 0,
    alias_type: str = "business",
    name: str | None = None,
) -> dict[str, Any]:
    """创建 + 认证一个指标并新增别名（用于别名歧义/最长匹配场景）。"""
    create_metric(client, code, name=name)
    certify_metric(client, code)
    return add_alias(
        client, code, alias, alias_type=alias_type, priority=priority
    )


def staging_query(sql: str, params: dict[str, Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    """直接读取 staging 库（仅测试断言用，调用方 SQL 明确列名）。"""
    with store.staging_connection() as conn:
        rows = conn.execute(sql, params or {}).fetchall()
    return [dict(row) for row in rows]


def staging_scalar(sql: str, params: dict[str, Any] | tuple[Any, ...] | None = None) -> Any:
    """读取 staging 单标量。"""
    rows = staging_query(sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def staging_execute(sql: str, params: dict[str, Any] | tuple[Any, ...] | None = None) -> None:
    """在 staging 执行测试准备语句（仅测试文件使用）。"""
    with store.staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(sql, params or {})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def sqlite_rows(path: str, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """按原生 sqlite3 读取业务库（用于审计列断言）。"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
