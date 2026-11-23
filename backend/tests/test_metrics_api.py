"""指标注册表 API/服务：seed 幂等、版本状态机、认证、别名与权限。"""

from __future__ import annotations

import pytest

from app.services import metric_seed, metrics as metrics_service, quality_seed
from app.services.ingest import store
from tests.metric_support import (
    add_alias,
    certify_metric,
    create_metric,
    created_data,
    staging_query,
)
from tests.support import ask, error_data, headers, ok_data


def test_metric_catalog_seed_is_idempotent() -> None:
    """首次播种 20 指标/版本/别名/用例；重复执行不增加任何行。"""
    first = metric_seed.ensure_metric_catalog()
    second = metric_seed.ensure_metric_catalog()

    assert first["seeded"] is True
    assert (first["metrics"], first["versions"], first["test_cases"]) == (20, 20, 20)
    assert first["aliases"] >= 40
    assert second["seeded"] is False
    assert {key: second[key] for key in ("metrics", "versions", "aliases", "test_cases")} == {
        key: first[key] for key in ("metrics", "versions", "aliases", "test_cases")
    }

    definitions = staging_query(
        "SELECT code, status, current_version FROM metric_definition"
    )
    assert len(definitions) == 20
    assert all(item["status"] == "certified" for item in definitions)
    assert all(item["current_version"] == "v1.0" for item in definitions)

    versions = staging_query(
        "SELECT metric_code, version, status FROM metric_version"
    )
    assert len(versions) == 20
    assert all(item["version"] == "v1.0" and item["status"] == "certified" for item in versions)

    cases = staging_query(
        "SELECT metric_code, COUNT(*) AS n FROM metric_test_case GROUP BY metric_code"
    )
    assert len(cases) == 20
    assert all(int(item["n"]) >= 1 for item in cases)

    aliases = staging_query(
        "SELECT metric_code, COUNT(*) AS n FROM metric_alias GROUP BY metric_code"
    )
    assert len(aliases) == 20
    assert all(int(item["n"]) >= 2 for item in aliases)


def test_quality_rules_seed_is_idempotent() -> None:
    """10 条质量规则覆盖 5 种类型、2 类目标库，重复播种不重复。"""
    first = quality_seed.ensure_quality_rules()
    second = quality_seed.ensure_quality_rules()

    assert first["seeded"] is True
    assert first["rules"] == 10
    assert second["seeded"] is False
    assert second["rules"] == 10

    rules = staging_query(
        "SELECT code, rule_type, target_db, expression_json, severity, schedule "
        "FROM quality_rule"
    )
    assert len(rules) == 10
    assert {item["rule_type"] for item in rules} == {
        "reconciliation",
        "completeness",
        "uniqueness",
        "range",
        "custom_sql",
    }
    assert {item["target_db"] for item in rules} == {"business", "staging"}
    assert all(item["expression_json"] not in ("", "{}") for item in rules)
    assert all(item["severity"] != "blocker" for item in rules)


def test_metric_summary_and_list_filters(client) -> None:
    summary = ok_data(
        client.get("/api/metrics/summary", headers=headers("viewer"))
    )
    assert summary["total"] == 20
    assert summary["certified"] == 20
    assert summary["draft"] == 0
    assert summary["pending"] == 0
    assert summary["deprecated"] == 0
    assert summary["tests_total"] == 20
    assert summary["tests_passed"] == 0
    assert summary["test_pass_rate"] == 0.0

    listing = ok_data(
        client.get(
            "/api/metrics",
            params={"status": "certified", "page": 1, "page_size": 5},
            headers=headers("viewer"),
        )
    )
    assert listing["total"] == 20
    assert listing["page"] == 1
    assert listing["page_size"] == 5
    assert len(listing["items"]) == 5
    assert all(item["status"] == "certified" for item in listing["items"])
    assert all(item["test_total"] == 1 for item in listing["items"])

    profit_domain = ok_data(
        client.get(
            "/api/metrics",
            params={"business_domain": "利润", "page_size": 20},
            headers=headers("viewer"),
        )
    )
    assert profit_domain["total"] == 5

    search = ok_data(
        client.get(
            "/api/metrics", params={"q": "revenue"}, headers=headers("viewer")
        )
    )
    assert any(item["code"] == "revenue_total" for item in search["items"])

    clamped = ok_data(
        client.get(
            "/api/metrics", params={"page_size": 500}, headers=headers("viewer")
        )
    )
    assert clamped["page_size"] == 100


def test_metric_detail_versions_and_not_found(client) -> None:
    detail = ok_data(
        client.get("/api/metrics/revenue_total", headers=headers("viewer"))
    )
    assert detail["code"] == "revenue_total"
    assert detail["status"] == "certified"
    assert detail["current_version"] == "v1.0"
    assert detail["unit"] == "CNY"
    assert detail["boundary"]["tax_basis"] == "不含税"
    assert detail["current_certified"]["version"] == "v1.0"
    assert detail["current_certified"]["certified_by"] == "system"
    assert detail["draft_version"] is None
    assert detail["test_summary"]["total"] == 1
    assert detail["test_summary"]["not_run"] == 1
    assert len(detail["versions"]) == 1
    assert len(detail["test_cases"]) == 1
    assert len(detail["aliases"]) == 8
    alias_names = {item["alias"] for item in detail["aliases"]}
    assert {"营收", "营业收入", "含税收入"} <= alias_names

    versions = ok_data(
        client.get(
            "/api/metrics/revenue_total/versions", headers=headers("viewer")
        )
    )
    assert len(versions["items"]) == 1
    assert versions["items"][0]["version"] == "v1.0"

    missing = client.get("/api/metrics/not_a_metric", headers=headers("viewer"))
    assert missing.status_code == 404
    assert error_data(missing)["code"] == "METRIC_NOT_FOUND"

    missing_versions = client.get(
        "/api/metrics/not_a_metric/versions", headers=headers("viewer")
    )
    assert missing_versions.status_code == 404
    assert error_data(missing_versions)["code"] == "METRIC_NOT_FOUND"


def test_metric_update_creates_draft_and_certify_deprecates_old_version(client) -> None:
    updated = ok_data(
        client.patch(
            "/api/metrics/revenue_total",
            json={"definition": "TASK-04 更新后的收入定义"},
            headers=headers("analyst"),
        )
    )
    assert updated["status"] == "certified"
    assert updated["current_version"] == "v1.0"
    assert updated["draft_version"]["version"] == "v1.1"
    assert updated["draft_version"]["status"] == "draft"
    statuses = {item["version"]: item["status"] for item in updated["versions"]}
    assert statuses == {"v1.0": "certified", "v1.1": "draft"}

    test_run = ok_data(
        client.post(
            "/api/metrics/revenue_total/test-cases/run",
            json={"version": "v1.1"},
            headers=headers("analyst"),
        )
    )
    assert test_run["version"] == "v1.1"
    assert test_run["total"] == 1
    assert test_run["passed"] == 1
    assert test_run["failed"] == 0

    certified = certify_metric(client, "revenue_total", "v1.1")
    assert certified["status"] == "certified"
    assert certified["current_version"] == "v1.1"
    assert certified["draft_version"] is None
    statuses = {item["version"]: item["status"] for item in certified["versions"]}
    assert statuses == {"v1.0": "deprecated", "v1.1": "certified"}
    assert sum(1 for status in statuses.values() if status == "certified") == 1

    answer = ask(client, "2026年10月营业收入", "viewer")
    assert answer["provenance"]["metric_definition_code"] == "revenue_total"
    assert answer["provenance"]["metric_version"] == "v1.1"


def test_metric_certify_fails_when_test_case_mismatch(client) -> None:
    updated = ok_data(
        client.patch(
            "/api/metrics/revenue_total",
            json={"formula_sql": "SELECT 0.0 AS value WHERE :start_period = :end_period"},
            headers=headers("admin"),
        )
    )
    assert updated["draft_version"]["version"] == "v1.1"

    test_run = ok_data(
        client.post(
            "/api/metrics/revenue_total/test-cases/run",
            json={"version": "v1.1"},
            headers=headers("admin"),
        )
    )
    assert test_run["failed"] == 1
    assert test_run["passed"] == 0

    response = client.post(
        "/api/metrics/revenue_total/versions/v1.1/certify",
        json={},
        headers=headers("admin"),
    )
    assert response.status_code == 422
    payload = error_data(response)
    assert payload["code"] == "METRIC_TEST_FAILED"
    assert payload["details"]["version"] == "v1.1"
    assert len(payload["details"]["failed"]) == 1
    assert payload["details"]["failed"][0]["name"]

    detail = ok_data(
        client.get("/api/metrics/revenue_total", headers=headers("admin"))
    )
    assert detail["current_version"] == "v1.0"
    statuses = {item["version"]: item["status"] for item in detail["versions"]}
    assert statuses["v1.0"] == "certified"
    assert statuses["v1.1"] == "draft"


def test_metric_certify_rejects_empty_formula_without_test_cases(client) -> None:
    created = create_metric(
        client,
        "t04_empty_formula",
        name="TASK-04 空公式指标",
        formula_sql="",
    )
    assert created["status"] == "draft"
    assert created["current_version"] is None
    assert created["versions"][0]["version"] == "v0.1"
    assert created["test_cases"] == []

    submitted = ok_data(
        client.post(
            "/api/metrics/t04_empty_formula/versions/v0.1/submit",
            json={"change_note": "TASK-04 提交空公式指标"},
            headers=headers("analyst"),
        )
    )
    assert submitted["status"] == "pending"

    response = client.post(
        "/api/metrics/t04_empty_formula/versions/v0.1/certify",
        json={},
        headers=headers("admin"),
    )
    assert response.status_code == 422
    payload = error_data(response)
    assert payload["code"] == "VALIDATION_FAILED"
    assert "缺少公式" in payload["message"]

    detail = ok_data(
        client.get("/api/metrics/t04_empty_formula", headers=headers("admin"))
    )
    assert detail["current_version"] is None
    assert all(item["status"] != "certified" for item in detail["versions"])


def test_metric_alias_crud_and_conflict(client) -> None:
    alias = add_alias(
        client, "revenue_total", "TASK04营收", priority=5
    )
    assert alias["alias"] == "TASK04营收"
    assert alias["normalized_alias"] == "task04营收"
    assert alias["alias_type"] == "business"
    assert alias["status"] == "active"

    duplicate = client.post(
        "/api/metrics/revenue_total/aliases",
        json={"alias": " TASK04 营收 ", "alias_type": "synonym", "priority": 9},
        headers=headers("admin"),
    )
    assert duplicate.status_code == 409
    assert error_data(duplicate)["code"] == "METRIC_ALIAS_CONFLICT"

    updated = ok_data(
        client.patch(
            f"/api/metrics/revenue_total/aliases/{alias['id']}",
            json={"priority": 7, "status": "disabled"},
            headers=headers("admin"),
        )
    )
    assert updated["priority"] == 7
    assert updated["status"] == "disabled"
    active = metrics_service.active_aliases()
    assert all(item["id"] != alias["id"] for item in active)

    missing_update = client.patch(
        "/api/metrics/revenue_total/aliases/999999",
        json={"priority": 1},
        headers=headers("admin"),
    )
    assert missing_update.status_code == 404
    assert error_data(missing_update)["code"] == "METRIC_ALIAS_NOT_FOUND"

    deleted = ok_data(
        client.delete(
            f"/api/metrics/revenue_total/aliases/{alias['id']}",
            headers=headers("admin"),
        )
    )
    assert deleted["deleted"] is True

    detail = ok_data(
        client.get("/api/metrics/revenue_total", headers=headers("admin"))
    )
    assert all(item["id"] != alias["id"] for item in detail["aliases"])


def test_metric_write_permission_matrix(client) -> None:
    write_cases = [
        ("post", "/api/metrics", {"code": "t04_viewer_metric", "name": "viewer"}),
        ("patch", "/api/metrics/revenue_total", {"definition": "viewer 修改"}),
        (
            "post",
            "/api/metrics/revenue_total/aliases",
            {"alias": "viewer别名", "alias_type": "business"},
        ),
        ("post", "/api/metrics/revenue_total/test-cases/run", {}),
        ("post", "/api/metrics/revenue_total/versions/v1.0/certify", {}),
    ]
    for method, path, payload in write_cases:
        response = getattr(client, method)(
            path, json=payload, headers=headers("viewer")
        )
        assert response.status_code == 403, (method, path, response.text)
        assert error_data(response)["code"] == "FORBIDDEN"

    # 无角色头默认 viewer，同样拒绝写操作。
    missing_role = client.post(
        "/api/metrics", json={"code": "t04_no_role", "name": "无角色"}
    )
    assert missing_role.status_code == 403

    # analyst 可编辑但不可认证。
    analyst_patch = ok_data(
        client.patch(
            "/api/metrics/revenue_total",
            json={"definition": "analyst 可编辑"},
            headers=headers("analyst"),
        )
    )
    assert analyst_patch["draft_version"]["version"] == "v1.1"

    analyst_certify = client.post(
        "/api/metrics/revenue_total/versions/v1.1/certify",
        json={},
        headers=headers("analyst"),
    )
    assert analyst_certify.status_code == 403
    assert error_data(analyst_certify)["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "formula_sql",
    [
        "SELECT phone FROM dim_customer WHERE :start_period = :end_period",
        "DROP TABLE fact_balance",
        "SELECT 1 AS a, 2 AS b WHERE :start_period = :end_period",
        "SELECT value FROM secret_table WHERE :start_period = :end_period",
    ],
    ids=["sensitive", "ddl", "not-single-value", "table-not-allowed"],
)
def test_metric_formula_injection_rejected(client, formula_sql: str) -> None:
    response = client.post(
        "/api/metrics",
        json={
            "code": f"t04_bad_formula_{abs(hash(formula_sql)) % 10000}",
            "name": "非法公式指标",
            "formula_sql": formula_sql,
        },
        headers=headers("admin"),
    )
    assert response.status_code == 422
    assert error_data(response)["code"] == "METRIC_FORMULA_INVALID"


def test_metric_create_validation_rejects_bad_code(client) -> None:
    response = client.post(
        "/api/metrics",
        json={"code": "BadCode", "name": "非法编码"},
        headers=headers("admin"),
    )
    assert response.status_code == 422
    assert error_data(response)["code"] == "VALIDATION_FAILED"


def test_staging_schema_has_metric_and_quality_tables(client) -> None:
    store.init_staging()
    names = {
        item["name"]
        for item in staging_query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert {
        "metric_definition",
        "metric_version",
        "metric_test_case",
        "metric_alias",
        "quality_rule",
        "quality_run",
        "quality_result",
    } <= names
