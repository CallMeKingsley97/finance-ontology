"""本体生成、术语编辑与映射刷新回归（P1-2 / P1-3）。"""

from __future__ import annotations

from tests.support import headers, ok_data, term_id_by_code


def _term(client, term_code: str) -> dict:
    term_id = term_id_by_code(client, term_code)
    return ok_data(client.get(f"/api/ontology/terms/{term_id}", headers=headers("admin")))


def test_generate_ontology_counts(ontology_client) -> None:
    response = ontology_client.post(
        "/api/ontology/generate", json={"force": True}, headers=headers("admin")
    )
    data = ok_data(response)
    assert data["status"] == "generated"
    assert data["counts"]["terms"] == 27
    assert data["counts"]["relations"] == 16
    assert data["counts"]["sql_attributes"] == 20
    assert data["counts"]["columns"] >= 80
    assert set(data["sensitive_columns"]) == {
        "dim_customer.phone",
        "dim_customer.bank_account",
        "dim_vendor.bank_account",
        "dim_vendor.phone",
    }
    assert data["version"] == "2026.11.1"


def test_generate_ontology_skips_without_force(ontology_client) -> None:
    data = ok_data(
        ontology_client.post(
            "/api/ontology/generate", json={"force": False}, headers=headers("admin")
        )
    )
    assert data["status"] == "skipped"
    assert data["counts"]["terms"] == 27


def test_generate_ontology_requires_admin(ontology_client) -> None:
    response = ontology_client.post(
        "/api/ontology/generate", json={"force": True}, headers=headers("analyst")
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_metric_term_patch_with_empty_mapping_succeeds(ontology_client) -> None:
    """P1-2 回归：旧前端 payload（mapped_table=None + mapped_columns=[]）必须 200。"""
    term = _term(ontology_client, "metric_revenue_total")
    payload = {
        "display_name": term["display_name"],
        "description": term["description"],
        "synonyms": ["营收", "收入", "销售收入", "营业额"],
        "mapped_table": None,
        "mapped_columns": [],
    }
    response = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json=payload,
        headers=headers("admin"),
    )
    updated = ok_data(response)
    assert updated["mapped_table"] is None
    assert updated["mapped_columns"] == []
    assert updated["synonyms"] == payload["synonyms"]

    detail = _term(ontology_client, "metric_revenue_total")
    assert detail["mapped_table"] is None
    assert detail["mapped_columns"] == []
    assert detail["columns"] == []


def test_mapping_change_rebuilds_columns(ontology_client) -> None:
    """P1-3 回归：改表 + 改列后，详情列元数据必须跟随新表。"""
    term = _term(ontology_client, "term_customer")
    assert term["mapped_table"] == "dim_customer"

    response = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={
            "mapped_table": "dim_vendor",
            "mapped_columns": ["vendor_code", "vendor_name", "phone", "bank_account"],
        },
        headers=headers("admin"),
    )
    updated = ok_data(response)
    assert updated["mapped_table"] == "dim_vendor"
    assert updated["mapped_columns"] == [
        "vendor_code",
        "vendor_name",
        "phone",
        "bank_account",
    ]

    detail = _term(ontology_client, "term_customer")
    assert detail["mapped_table"] == "dim_vendor"
    assert {column["table_name"] for column in detail["columns"]} == {"dim_vendor"}
    sensitive = {
        column["column_name"]: column
        for column in detail["columns"]
        if column["is_sensitive"]
    }
    assert set(sensitive) == {"phone", "bank_account"}
    assert sensitive["phone"]["classification"] == "PII_PHONE"
    assert sensitive["bank_account"]["classification"] == "PII_BANK_ACCOUNT"


def test_changing_table_without_columns_is_rejected(ontology_client) -> None:
    term = _term(ontology_client, "term_customer")
    response = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"mapped_table": "dim_vendor"},
        headers=headers("admin"),
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_FAILED"
    assert "mapped_columns" in payload["message"]


def test_clearing_mapping_removes_columns(ontology_client) -> None:
    term = _term(ontology_client, "term_customer")
    response = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"mapped_table": None},
        headers=headers("admin"),
    )
    updated = ok_data(response)
    assert updated["mapped_table"] is None
    assert updated["mapped_columns"] == []

    detail = _term(ontology_client, "term_customer")
    assert detail["mapped_columns"] == []
    assert detail["columns"] == []


def test_invalid_mapped_table_and_columns_rejected(ontology_client) -> None:
    term = _term(ontology_client, "term_customer")
    bad_table = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"mapped_table": "ontology_term", "mapped_columns": []},
        headers=headers("admin"),
    )
    assert bad_table.status_code == 422
    assert "白名单" in bad_table.json()["message"]

    bad_column = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"mapped_columns": ["vendor_code"]},
        headers=headers("admin"),
    )
    assert bad_column.status_code == 422
    assert "非法列" in bad_column.json()["message"]


def test_patch_validation_errors(ontology_client) -> None:
    term = _term(ontology_client, "term_customer")
    unknown_field = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"term_code": "hacked"},
        headers=headers("admin"),
    )
    assert unknown_field.status_code == 422

    empty_payload = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={},
        headers=headers("admin"),
    )
    assert empty_payload.status_code == 422

    empty_name = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"display_name": "   "},
        headers=headers("admin"),
    )
    assert empty_name.status_code == 422

    missing = ontology_client.patch(
        "/api/ontology/terms/999999",
        json={"description": "x"},
        headers=headers("admin"),
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def test_patch_and_certify_require_admin(ontology_client) -> None:
    term = _term(ontology_client, "term_customer")
    patch = ontology_client.patch(
        f"/api/ontology/terms/{term['term_id']}",
        json={"description": "analyst should fail"},
        headers=headers("analyst"),
    )
    assert patch.status_code == 403

    certify = ontology_client.post(
        f"/api/ontology/terms/{term['term_id']}/certify",
        json={"certified": True},
        headers=headers("viewer"),
    )
    assert certify.status_code == 403


def test_certify_is_idempotent_and_logged_once(ontology_client) -> None:
    term = _term(ontology_client, "sensitive_customer_phone")
    assert term["certified"] is False
    term_id = term["term_id"]

    first = ok_data(
        ontology_client.post(
            f"/api/ontology/terms/{term_id}/certify",
            json={"certified": True},
            headers=headers("admin"),
        )
    )
    assert first["certified"] is True
    assert first["certified_by"] == "admin"

    second = ok_data(
        ontology_client.post(
            f"/api/ontology/terms/{term_id}/certify",
            json={"certified": True},
            headers=headers("admin"),
        )
    )
    assert second["certified"] is True

    detail = _term(ontology_client, "sensitive_customer_phone")
    certified_logs = [
        item for item in detail["change_log"] if item["field_name"] == "certified"
    ]
    assert len(certified_logs) == 1
    assert certified_logs[0]["changed_by_role"] == "admin"


def test_sensitive_term_samples_masked_by_role(ontology_client) -> None:
    term_id = term_id_by_code(ontology_client, "sensitive_customer_phone")

    viewer = ok_data(
        ontology_client.get(f"/api/ontology/terms/{term_id}", headers=headers("viewer"))
    )
    assert viewer["columns"][0]["sample_masked"] == "***"

    analyst = ok_data(
        ontology_client.get(f"/api/ontology/terms/{term_id}", headers=headers("analyst"))
    )
    assert len(str(analyst["columns"][0]["sample_masked"])) > 3

    admin = ok_data(
        ontology_client.get(f"/api/ontology/terms/{term_id}", headers=headers("admin"))
    )
    assert str(admin["columns"][0]["sample_masked"]).isdigit()


def test_terms_search_and_category_filter(ontology_client) -> None:
    by_category = ok_data(
        ontology_client.get(
            "/api/ontology/terms", params={"category": "metric", "limit": 200}
        )
    )
    assert by_category["total"] == 14

    search = ok_data(
        ontology_client.get("/api/ontology/terms", params={"q": "毛利", "limit": 200})
    )
    assert search["total"] >= 1
    assert all(
        "毛利" in item["display_name"] or "毛利" in item["synonyms"]
        for item in search["items"]
    )

    invalid = ontology_client.get(
        "/api/ontology/terms", params={"category": "unknown"}
    )
    assert invalid.status_code == 422
