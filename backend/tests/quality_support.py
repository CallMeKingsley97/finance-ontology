"""质量规则测试辅助：通过公开 API 创建规则并执行。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.metric_support import created_data
from tests.support import headers


def create_rule(
    client: TestClient,
    code: str,
    *,
    expression: dict[str, Any] | None = None,
    rule_type: str = "custom_sql",
    target_db: str = "business",
    severity: str = "warning",
    schedule: str = "manual",
    enabled: bool = True,
    role: str = "admin",
    name: str | None = None,
) -> dict[str, Any]:
    """创建一条质量规则，返回规则对象。"""
    payload = {
        "code": code,
        "name": name or f"TASK-04 规则 {code}",
        "rule_type": rule_type,
        "target_db": target_db,
        "expression": expression
        or {"sql": "SELECT 0 AS value", "operator": "==", "expected": 1},
        "severity": severity,
        "schedule": schedule,
        "enabled": enabled,
    }
    response = client.post("/api/quality/rules", json=payload, headers=headers(role))
    return created_data(response)


def run_quality(
    client: TestClient,
    *,
    rule_codes: list[str] | None = None,
    period: str | None = None,
    role: str = "admin",
) -> dict[str, Any]:
    """执行一次手动质量检查，返回 run 文档。"""
    payload: dict[str, Any] = {}
    if rule_codes is not None:
        payload["rule_codes"] = rule_codes
    if period is not None:
        payload["period"] = period
    response = client.post("/api/quality/run", json=payload, headers=headers(role))
    assert response.status_code == 200, response.text
    return response.json()["data"]
