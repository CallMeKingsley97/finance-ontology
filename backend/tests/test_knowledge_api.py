"""知识 API 契约：摘要/浏览/筛选/分页/reload 角色矩阵/降级（R3、S7）。

所有请求走真实 FastAPI 路由与真实知识包副本；知识库路径全部落在 tmp_path。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.knowledge_support import (
    EXPECTED_ENTRIES,
    EXPECTED_SOURCES,
    PACK_VERSION,
)
from tests.support import error_data, headers, ok_data


def test_summary_contract(pack_client: TestClient) -> None:
    """GET /summary 返回可用摘要、领域/类型计数与覆盖度。"""
    data = ok_data(pack_client.get("/api/knowledge/summary"))
    assert data["available"] is True
    assert data["pack_version"] == PACK_VERSION
    assert data["entries"] == EXPECTED_ENTRIES
    assert data["sources"] == EXPECTED_SOURCES
    assert data["loaded_at"]
    assert data["last_error"] is None

    domains = {item["domain"]: item["entries"] for item in data["domains"]}
    assert sum(domains.values()) == EXPECTED_ENTRIES
    assert domains["accounting_reconciliation"] > 0
    assert domains["business_finance_integration"] > 0

    kinds = {item["kind"]: item["entries"] for item in data["kinds"]}
    assert kinds["term"] == 13
    assert kinds["process"] == 3
    assert kinds["metric_basis"] == 20
    assert kinds["reconciliation_rule"] == 5
    assert kinds["terminology"] == 25
    assert kinds["correction"] == 5

    coverage = data["coverage"]
    assert coverage["metrics_covered"] == 20
    assert coverage["reconciliation_rules"] == 5
    assert coverage["terminology_phrases"] == 25
    assert coverage["ontology_terms_covered"] == 6


def test_entries_pagination_and_filters(pack_client: TestClient) -> None:
    """条目标表支持分页、类型/领域/来源筛选与关键字搜索。"""
    first = ok_data(pack_client.get("/api/knowledge/entries", params={"page_size": 20}))
    assert first["total"] == EXPECTED_ENTRIES
    assert first["page"] == 1
    assert first["page_size"] == 20
    assert len(first["items"]) == 20
    item = first["items"][0]
    assert {
        "entry_id",
        "kind",
        "domain",
        "title",
        "statement",
        "basis",
        "source_id",
        "source_name",
        "source_type",
        "version",
        "effective_from",
        "confidence",
    } <= set(item)

    second = ok_data(
        pack_client.get("/api/knowledge/entries", params={"page": 2, "page_size": 20})
    )
    assert {row["entry_id"] for row in first["items"]} & {
        row["entry_id"] for row in second["items"]
    } == set()

    capped = ok_data(
        pack_client.get("/api/knowledge/entries", params={"page_size": 500})
    )
    assert capped["page_size"] == 100
    assert len(capped["items"]) == EXPECTED_ENTRIES

    for kind, expected in (
        ("term", 13),
        ("process", 3),
        ("metric_basis", 20),
        ("reconciliation_rule", 5),
        ("terminology", 25),
        ("correction", 5),
    ):
        data = ok_data(
            pack_client.get("/api/knowledge/entries", params={"kind": kind})
        )
        assert data["total"] == expected, kind
        assert all(row["kind"] == kind for row in data["items"]), kind

    integration = ok_data(
        pack_client.get(
            "/api/knowledge/entries",
            params={"domain": "business_finance_integration", "page_size": 100},
        )
    )
    assert integration["total"] > 0
    assert all(
        row["domain"] == "business_finance_integration" for row in integration["items"]
    )

    searched = ok_data(
        pack_client.get("/api/knowledge/entries", params={"q": "试算平衡"})
    )
    assert searched["total"] >= 1
    assert any(row["entry_id"] == "recon.trial_balance" for row in searched["items"])


def test_entries_rejects_invalid_params(pack_client: TestClient) -> None:
    """非法 kind/domain、超长 q/source_id 返回 422（CR-009 回归）。"""
    cases = [
        {"kind": "bogus"},
        {"domain": "bogus"},
        {"q": "x" * 101},
        {"source_id": "x" * 65},
    ]
    for params in cases:
        response = pack_client.get("/api/knowledge/entries", params=params)
        assert response.status_code == 422, params
        payload = error_data(response)
        assert payload["code"] == "VALIDATION_FAILED"
        assert payload["details"]["errors"]


def test_sources_contract(pack_client: TestClient) -> None:
    """GET /sources 返回 8 个登记来源与引用计数，开源来源带 notice。"""
    items = ok_data(pack_client.get("/api/knowledge/sources"))["items"]
    assert len(items) == EXPECTED_SOURCES
    assert sum(item["entry_count"] for item in items) == EXPECTED_ENTRIES
    by_id = {item["source_id"]: item for item in items}
    for source_id in ("fibo-loan", "cube", "datahub-glossary"):
        assert by_id[source_id]["type"] == "opensource"
        assert by_id[source_id]["license"] in {"MIT", "Apache-2.0"}
        assert by_id[source_id]["notice"].strip()
    assert by_id["cas-basic"]["type"] == "standard"
    assert by_id["double-entry"]["type"] == "practice"


def test_reload_role_matrix(pack_client: TestClient) -> None:
    """reload 仅 admin 可执行；viewer/analyst 返回 403 且不改变库。"""
    for role in ("viewer", "analyst"):
        response = pack_client.post(
            "/api/knowledge/reload", headers=headers(role)
        )
        assert response.status_code == 403, role
        assert error_data(response)["code"] == "FORBIDDEN"

    reloaded = ok_data(pack_client.post("/api/knowledge/reload", headers=headers("admin")))
    assert reloaded["reloaded"] is True
    assert reloaded["pack_version"] == PACK_VERSION
    assert reloaded["entries"] == EXPECTED_ENTRIES
    assert reloaded["sources"] == EXPECTED_SOURCES
    assert reloaded["domains"] == 2
    assert reloaded["removed"] == 0


def test_broken_json_startup_degrades_without_500(
    broken_pack_client: TestClient,
) -> None:
    """JSON 非法时应用可启动，知识读接口 200 降级、reload 返回 422。"""
    summary = ok_data(broken_pack_client.get("/api/knowledge/summary"))
    assert summary["available"] is False
    assert summary["entries"] == 0
    assert summary["last_error"], "非法 JSON 应记录 last_load_error"
    assert ok_data(broken_pack_client.get("/api/knowledge/entries"))["total"] == 0

    response = broken_pack_client.post(
        "/api/knowledge/reload", headers=headers("admin")
    )
    assert response.status_code == 422
    assert error_data(response)["code"] == "KNOWLEDGE_VALIDATION_FAILED"

    # 业务接口不因知识包非法而 500。
    report = broken_pack_client.post(
        "/api/reports/generate",
        json={
            "template_code": "income_statement",
            "period": "2026-10",
            "comparison": "none",
        },
        headers=headers("analyst"),
    )
    assert report.status_code == 200, report.text
    assert len(report.json()["data"]["reconciliation"]["checks"]) == 5


def test_knowledge_db_missing_before_startup(empty_pack_client: TestClient) -> None:
    """空目录启动：读接口 200 降级，业务接口不受影响且勾稽退回 5 条。"""
    summary = ok_data(empty_pack_client.get("/api/knowledge/summary"))
    assert summary["available"] is False
    assert summary["entries"] == 0
    assert summary["sources"] == 0
    assert summary["coverage"]["metrics_covered"] == 0
    assert summary["last_error"]

    assert ok_data(empty_pack_client.get("/api/knowledge/sources"))["items"] == []
    response = empty_pack_client.get("/api/knowledge/entries/glossary.double_entry")
    assert response.status_code == 404

    report = ok_data(
        empty_pack_client.post(
            "/api/reports/generate",
            json={
                "template_code": "financial_position",
                "period": "2026-10",
                "comparison": "none",
            },
            headers=headers("analyst"),
        )
    )
    assert len(report["reconciliation"]["checks"]) == 5
    assert report["reconciliation"]["knowledge_checks"] == 0
    assert report["reconciliation"]["passed"] is True

    answer = ok_data(
        empty_pack_client.post(
            "/api/query/ask",
            json={"question": "银行存款是流动资产吗", "limit": 20},
            headers=headers("viewer"),
        )
    )
    assert answer["intent"] == "unsupported"
    assert answer["provenance"]["knowledge_version"] is None

    ontology = ok_data(
        empty_pack_client.post(
            "/api/ontology/generate", json={"force": True}, headers=headers("admin")
        )
    )
    assert ontology["counts"]["terms"] == 27
    assert ontology["counts"]["relations"] == 16
    assert ontology["counts"]["sql_attributes"] == 20
