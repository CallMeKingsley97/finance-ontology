"""报表包 API：3 模板、质量前置、blocker、Excel 导出与旧报告兼容。"""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from tests.quality_support import create_rule
from tests.support import error_data, headers, ok_data

TEMPLATES = ["balance_sheet", "profit_statement", "management_report"]


def test_report_package_templates(client) -> None:
    data = ok_data(
        client.get("/api/report-packages/templates", headers=headers("viewer"))
    )
    assert [item["code"] for item in data["items"]] == TEMPLATES
    assert data["periods"]
    assert all(item["sections"] for item in data["items"])


def test_generate_package_with_three_templates(client) -> None:
    package = ok_data(
        client.post(
            "/api/report-packages/generate",
            json={
                "period": "2026-10",
                "templates": TEMPLATES,
                "comparison": "none",
            },
            headers=headers("admin"),
        )
    )
    assert package["period"]["code"] == "2026-10"
    assert [item["code"] for item in package["templates"]] == TEMPLATES
    for template in package["templates"]:
        types = {section["type"] for section in template["sections"]}
        assert template["sections"]
        assert "kpi" in types
        assert "table" in types

    assert package["quality"]["status"] == "succeeded"
    assert package["quality"]["rules_total"] == 10
    assert package["quality"]["rules_passed"] == 10
    assert len(package["quality"]["results"]) == 10
    assert package["reconciliation"]["passed"] is True
    assert len(package["reconciliation"]["checks"]) == 5
    assert package["appendix"]["metrics"]
    assert package["appendix"]["assumptions"]


def test_report_package_viewer_and_invalid_template(client) -> None:
    viewer = client.post(
        "/api/report-packages/generate",
        json={"period": "2026-10", "templates": TEMPLATES},
        headers=headers("viewer"),
    )
    assert viewer.status_code == 403
    assert error_data(viewer)["code"] == "FORBIDDEN"

    invalid = client.post(
        "/api/report-packages/generate",
        json={"period": "2026-10", "templates": ["not_a_template"]},
        headers=headers("admin"),
    )
    assert invalid.status_code == 404
    assert error_data(invalid)["code"] == "REPORT_PACKAGE_TEMPLATE_NOT_FOUND"


def test_report_package_blocker_rule_blocks_generation(client) -> None:
    create_rule(
        client,
        "t04_blocker_rule",
        expression={"sql": "SELECT 0 AS value", "operator": "==", "expected": 1},
        severity="blocker",
        schedule="report",
    )
    response = client.post(
        "/api/report-packages/generate",
        json={"period": "2026-10", "templates": TEMPLATES},
        headers=headers("admin"),
    )
    assert response.status_code == 409
    payload = error_data(response)
    assert payload["code"] == "QUALITY_BLOCKER_FAILED"
    assert payload["details"]["run_id"]
    assert payload["details"]["failed"]


def test_report_package_export_xlsx(client) -> None:
    response = client.post(
        "/api/report-packages/export",
        json={"period": "2026-10", "templates": TEMPLATES},
        headers=headers("admin"),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "report-package-2026-10.xlsx" in response.headers["content-disposition"]

    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == [
        "封面",
        "资产负债表",
        "利润表",
        "管理报表",
        "勾稽校验",
    ]
    for sheet in workbook.worksheets:
        assert sheet.max_row <= 2001
        assert sheet.max_column <= 50


def test_legacy_reports_contract_unchanged(client) -> None:
    templates = ok_data(client.get("/api/reports/templates"))
    assert len(templates["items"]) == 2

    report = ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": "financial_position",
                "period": "2026-10",
                "comparison": "none",
            },
            headers=headers("admin"),
        )
    )
    assert len(report["sections"]) == 8
    assert len(report["reconciliation"]["checks"]) == 5
    assert report["reconciliation"]["passed"] is True
    assert "quality" not in report
    assert {"template_code", "title", "period", "sections", "reconciliation", "warnings"} <= set(
        report
    )
