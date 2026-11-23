"""测试辅助函数：角色头、成功信封解包、术语查找与问数调用。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def headers(role: str = "admin") -> dict[str, str]:
    """演示角色请求头。"""
    return {"X-Demo-Role": role}


def ok_data(response) -> Any:
    """断言 HTTP 200 且成功信封 code=OK，返回 data。"""
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("code") == "OK", payload
    return payload["data"]


def error_data(response) -> dict:
    """返回错误信封（code/message/details）。"""
    payload = response.json()
    assert {"code", "message", "details"} <= set(payload), payload
    assert payload["code"] != "OK", payload
    return payload


def term_id_by_code(client: TestClient, term_code: str) -> int:
    """按 term_code 查术语 ID。"""
    data = ok_data(client.get("/api/ontology/terms", params={"limit": 200}))
    for item in data["items"]:
        if item["term_code"] == term_code:
            return int(item["term_id"])
    raise AssertionError(f"未找到术语: {term_code}")


def ask(
    client: TestClient,
    question: str,
    role: str = "viewer",
    limit: int = 20,
) -> dict:
    """调用问数 API 并返回 data。"""
    return ok_data(
        client.post(
            "/api/query/ask",
            json={"question": question, "limit": limit},
            headers=headers(role),
        )
    )


def column_index(data: dict, name: str) -> int:
    """返回 QueryAnswer.columns 中列名的下标。"""
    names = [column["name"] for column in data["columns"]]
    assert name in names, names
    return names.index(name)


def kpi_value(report: dict, key: str) -> float:
    """从报告 kpi 章节中取指标值。"""
    for section in report["sections"]:
        if section["type"] == "kpi" and section.get("data"):
            for item in section["data"]["items"]:
                if item["key"] == key:
                    return float(item["value"])
    raise AssertionError(f"报告中未找到 KPI: {key}")
