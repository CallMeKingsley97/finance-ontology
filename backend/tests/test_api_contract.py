"""19 个 HTTP 端点成功路径与关键错误路径契约测试。"""

from __future__ import annotations

import pytest

from tests.support import ask, error_data, headers, ok_data, term_id_by_code

BUSINESS_TABLES = {
    "dim_company",
    "dim_account",
    "dim_customer",
    "dim_vendor",
    "dim_period",
    "fact_journal",
    "fact_invoice",
    "fact_payment",
    "fact_balance",
    "fact_budget",
}


# --- 1. health / dashboard ---


def test_health_ok(client) -> None:
    data = ok_data(client.get("/api/health"))
    assert data["status"] == "ok"
    assert data["db_exists"] is True
    assert data["table_count"] == 16
    assert data["latest_period"] == "2026-10"
    assert data["llm_enabled"] is False
    assert data["version"]


def test_health_degraded_without_database(empty_client) -> None:
    data = ok_data(empty_client.get("/api/health"))
    assert data["status"] == "degraded"
    assert data["db_exists"] is False
    assert data["table_count"] == 0
    assert data["latest_period"] is None


def test_dashboard_summary(client) -> None:
    data = ok_data(client.get("/api/dashboard/summary", headers=headers("viewer")))
    assert data["latest_period"] == "2026-10"
    assert len(data["kpis"]) == 6
    assert [kpi["key"] for kpi in data["kpis"]] == [
        "revenue",
        "net_profit",
        "total_assets",
        "debt_asset_ratio",
        "gross_margin",
        "budget_achievement",
    ]
    assert len(data["trend"]) == 12
    assert data["reconciliation"]["passed"] is True
    assert data["counts"]["tables"] == 10


def test_dashboard_503_without_database(empty_client) -> None:
    response = empty_client.get("/api/dashboard/summary")
    assert response.status_code == 503
    payload = error_data(response)
    assert payload["code"] == "DB_UNAVAILABLE"


# --- 2. sources ---


def test_sources_list(client) -> None:
    data = ok_data(client.get("/api/sources"))
    item = data["items"][0]
    assert item["id"] == "sqlite-local"
    assert item["kind"] == "sqlite"
    assert item["status"] == "ready"
    assert item["table_count"] == 16
    assert item["size_bytes"] > 0


def test_sources_seed_admin_and_conflict(client) -> None:
    forbidden = client.post(
        "/api/sources/seed", json={"reset": True}, headers=headers("analyst")
    )
    assert forbidden.status_code == 403
    assert error_data(forbidden)["code"] == "FORBIDDEN"

    seeded = ok_data(
        client.post(
            "/api/sources/seed", json={"reset": True}, headers=headers("admin")
        )
    )
    assert seeded["seeded"] is True
    assert len(seeded["tables"]) == 16
    assert seeded["rows"]["fact_balance"] == 1320
    assert seeded["reconciliation"]["passed"] is True

    conflict = client.post(
        "/api/sources/seed", json={"reset": False}, headers=headers("admin")
    )
    assert conflict.status_code == 409
    assert error_data(conflict)["code"] == "SOURCE_ALREADY_SEEDED"


def test_sources_test_connection(client) -> None:
    data = ok_data(
        client.post("/api/sources/test", json={}, headers=headers("admin"))
    )
    assert data["success"] is True
    assert data["table_count"] == 16
    assert data["latest_period"] == "2026-10"
    assert data["sqlite_version"]

    forbidden = client.post(
        "/api/sources/test", json={}, headers=headers("analyst")
    )
    assert forbidden.status_code == 403

    missing = client.post(
        "/api/sources/test",
        json={"path": "/tmp/task04-not-exist.db"},
        headers=headers("admin"),
    )
    assert missing.status_code == 422
    assert error_data(missing)["code"] == "TEST_FAILED"


# --- 3. metadata ---


def test_metadata_tables_and_detail(client) -> None:
    data = ok_data(client.get("/api/metadata/tables"))
    assert len(data["items"]) == 10
    assert {item["name"] for item in data["items"]} == BUSINESS_TABLES

    detail = ok_data(
        client.get("/api/metadata/tables/dim_customer", headers=headers("viewer"))
    )
    assert detail["name"] == "dim_customer"
    assert detail["row_count"] == 30
    assert len(detail["columns"]) == 9
    assert len(detail["sample_rows"]) == 5
    assert set(detail["masked_columns"]) == {"phone", "bank_account"}
    assert any(index["unique"] for index in detail["indexes"])

    missing = client.get("/api/metadata/tables/not_a_table")
    assert missing.status_code == 404
    assert error_data(missing)["code"] == "NOT_FOUND"


def test_metadata_foreign_keys_and_indexes(client) -> None:
    detail = ok_data(client.get("/api/metadata/tables/fact_invoice"))
    joined_tables = {fk["to_table"] for fk in detail["foreign_keys"]}
    assert {"dim_period", "dim_customer", "dim_vendor"} <= joined_tables
    index_names = {index["name"] for index in detail["indexes"]}
    assert "idx_invoice_period_type" in index_names
    assert all(column["name"] and column["type"] for column in detail["columns"])


# --- 4. ontology ---


def test_ontology_generate_terms_and_relations(ontology_client) -> None:
    terms = ok_data(
        ontology_client.get("/api/ontology/terms", params={"limit": 200})
    )
    assert terms["total"] == 27
    categories = {}
    for item in terms["items"]:
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    assert categories == {"table": 10, "metric": 14, "sensitive": 3}

    relations = ok_data(ontology_client.get("/api/ontology/relations"))
    assert len(relations["items"]) == 16
    first = relations["items"][0]
    assert {"from_term", "to_term", "relation_type", "cardinality", "join_hint"} <= set(
        first
    )

    forbidden = ontology_client.post(
        "/api/ontology/generate", json={"force": True}, headers=headers("analyst")
    )
    assert forbidden.status_code == 403


def test_ontology_term_detail_and_patch(ontology_client) -> None:
    term_id = term_id_by_code(ontology_client, "term_customer")
    detail = ok_data(
        ontology_client.get(
            f"/api/ontology/terms/{term_id}", headers=headers("viewer")
        )
    )
    assert detail["term_code"] == "term_customer"
    assert detail["columns"]
    assert "change_log" in detail

    updated = ok_data(
        ontology_client.patch(
            f"/api/ontology/terms/{term_id}",
            json={"description": "TASK-04 契约测试描述"},
            headers=headers("admin"),
        )
    )
    assert updated["description"] == "TASK-04 契约测试描述"
    assert any(
        item["field_name"] == "description"
        for item in ok_data(
            ontology_client.get(f"/api/ontology/terms/{term_id}")
        )["change_log"]
    )

    missing = ontology_client.get(
        "/api/ontology/terms/999999", headers=headers("viewer")
    )
    assert missing.status_code == 404

    forbidden = ontology_client.patch(
        f"/api/ontology/terms/{term_id}",
        json={"description": "x"},
        headers=headers("viewer"),
    )
    assert forbidden.status_code == 403


def test_ontology_certify(ontology_client) -> None:
    term_id = term_id_by_code(ontology_client, "sensitive_vendor_bank_account")
    before = ok_data(
        ontology_client.get(f"/api/ontology/terms/{term_id}", headers=headers("admin"))
    )
    assert before["certified"] is False
    after = ok_data(
        ontology_client.post(
            f"/api/ontology/terms/{term_id}/certify",
            json={"certified": True},
            headers=headers("admin"),
        )
    )
    assert after["certified"] is True
    assert after["certified_by"] == "admin"
    assert after["certified_at"]


# --- 5. query ---


def test_query_ask_success_and_unsupported(client) -> None:
    data = ask(client, "2026年10月营业收入", "viewer")
    assert data["answerable"] is True
    assert data["intent"] == "revenue_lookup"
    assert data["engine"] == "rule"
    assert data["sql"].lstrip().upper().startswith("SELECT")
    assert ":start_period" in data["sql"] or ":period_id" in data["sql"]
    assert data["row_count"] == 1
    assert len(data["columns"]) == 2
    assert data["provenance"]["period_code"] == "2026-10"

    unsupported = ask(client, "今天天气怎么样", "viewer")
    assert unsupported["answerable"] is False
    assert unsupported["sql"] is None
    assert unsupported["rows"] == []
    assert len(unsupported["suggestions"]) == 5


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "营业收入", "limit": 0},
        {"question": "营业收入", "limit": 201},
    ],
)
def test_query_validation_errors(client, payload: dict) -> None:
    response = client.post("/api/query/ask", json=payload)
    assert response.status_code == 422
    assert error_data(response)["code"] == "VALIDATION_FAILED"


def test_query_without_database_returns_503(empty_client) -> None:
    response = empty_client.post(
        "/api/query/ask", json={"question": "2026年10月营业收入"}
    )
    assert response.status_code == 503
    assert error_data(response)["code"] == "DB_UNAVAILABLE"


# --- 6. reports ---


def test_report_templates(client) -> None:
    data = ok_data(client.get("/api/reports/templates"))
    templates = {item["code"]: item for item in data["items"]}
    assert set(templates) == {"financial_position", "income_statement"}
    assert len(templates["financial_position"]["sections"]) == 8
    assert len(templates["income_statement"]["sections"]) == 9
    assert len(data["periods"]) == 22
    assert data["periods"][0] == {"code": "2026-10", "label": "2026年10月"}


@pytest.mark.parametrize(
    "template_code,section_count",
    [("financial_position", 8), ("income_statement", 9)],
)
def test_report_generate_success(client, template_code: str, section_count: int) -> None:
    data = ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": template_code,
                "period": "2026-10",
                "comparison": "yoy",
            },
            headers=headers("analyst"),
        )
    )
    assert data["template_code"] == template_code
    assert len(data["sections"]) == section_count
    assert data["reconciliation"]["passed"] is True
    assert data["period"]["comparison"] == "2025-10"
    assert data["meta"]["engine"] == "rule"


def test_report_generate_errors(client) -> None:
    viewer = client.post(
        "/api/reports/generate",
        json={"template_code": "income_statement", "period": "2026-10"},
        headers=headers("viewer"),
    )
    assert viewer.status_code == 403
    assert error_data(viewer)["code"] == "FORBIDDEN"

    unknown_template = client.post(
        "/api/reports/generate",
        json={"template_code": "cash_flow", "period": "2026-10"},
        headers=headers("analyst"),
    )
    assert unknown_template.status_code == 404

    bad_period = client.post(
        "/api/reports/generate",
        json={"template_code": "income_statement", "period": "1999-01"},
        headers=headers("analyst"),
    )
    assert bad_period.status_code == 422

    bad_comparison = client.post(
        "/api/reports/generate",
        json={
            "template_code": "income_statement",
            "period": "2026-10",
            "comparison": "qoq",
        },
        headers=headers("analyst"),
    )
    assert bad_comparison.status_code == 422


# --- 7. security ---


def test_security_endpoints(client) -> None:
    roles = ok_data(client.get("/api/security/roles"))
    assert len(roles["items"]) == 3

    policies = ok_data(client.get("/api/security/policies"))
    assert len(policies["sensitive_columns"]) == 4
    assert policies["audit_fields"]

    ask(client, "客户明细", "viewer", limit=2)
    audit = ok_data(client.get("/api/security/audit", params={"limit": 10}))
    assert audit["total"] >= 1
    assert audit["items"][0]["role"] == "viewer"


# --- 8. 错误信封与角色默认值 ---


def test_unknown_route_uses_error_envelope(client) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    payload = error_data(response)
    assert payload["code"] == "NOT_FOUND"


def test_missing_role_header_defaults_to_viewer(client) -> None:
    data = ok_data(client.get("/api/metadata/tables/dim_customer"))
    assert set(data["masked_columns"]) == {"phone", "bank_account"}
    for row in data["sample_rows"]:
        assert row["phone"] == "***"

    forbidden = client.post("/api/sources/seed", json={"reset": True})
    assert forbidden.status_code == 403


def test_invalid_role_header_defaults_to_viewer(client) -> None:
    data = ok_data(
        client.get(
            "/api/metadata/tables/dim_customer",
            headers={"X-Demo-Role": "superuser"},
        )
    )
    for row in data["sample_rows"]:
        assert row["phone"] == "***"
