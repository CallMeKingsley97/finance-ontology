"""业务正确性：12 个代表性问题、问数与报告口径一致、勾稽与章节结构。"""

from __future__ import annotations

import pytest

from tests.support import ask, column_index, headers, kpi_value, ok_data

REPRESENTATIVE_QUESTIONS = [
    "2026年10月营业收入",
    "2026年10月营业收入同比",
    "2026Q3 毛利率",
    "2026年10月期间费用构成",
    "2026年10月营业利润",
    "2026年10月净利润",
    "2026年10月资产总额和资产负债率",
    "2026年10月 Top 5 客户",
    "2026年10月收入预算达成率",
    "2026年10月应收账款余额",
    "客户明细",
    "近 6 个月收入趋势",
]


def test_twelve_representative_questions_answerable(client) -> None:
    """M6 基准：12 个代表性问题全部可答，SQL 为单条参数化 SELECT。"""
    results = {}
    failures = []
    for question in REPRESENTATIVE_QUESTIONS:
        data = ask(client, question, "viewer")
        results[question] = data
        if not data["answerable"]:
            failures.append((question, data.get("intent")))
    assert not failures, f"不可回答: {failures}"
    assert len(results) == 12

    for question, data in results.items():
        sql = data["sql"]
        assert sql, question
        assert sql.lstrip().upper().startswith(("SELECT", "WITH")), question
        assert ";" not in sql.rstrip(";"), question
        assert not any(
            token in sql.upper()
            for token in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ")
        ), question
        assert "SELECT *" not in sql.upper(), question
        assert ":start_period" in sql or ":period_id" in sql, question
        assert data["engine"] == "rule", question
        assert data["row_count"] >= 1, question


def test_revenue_answer_matches_income_statement(client) -> None:
    ask_data = ask(client, "2026年10月营业收入", "viewer")
    revenue = float(ask_data["rows"][0][column_index(ask_data, "current_value")])
    assert revenue == pytest.approx(14_513_571.53, abs=0.01)

    report = ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": "income_statement",
                "period": "2026-10",
                "comparison": "yoy",
            },
            headers=headers("analyst"),
        )
    )
    assert kpi_value(report, "revenue") == pytest.approx(revenue, abs=0.01)


def test_balance_answer_matches_financial_position(client) -> None:
    ask_data = ask(client, "2026年10月资产总额和资产负债率", "analyst")
    assets = float(ask_data["rows"][0][column_index(ask_data, "total_assets")])
    ratio = float(ask_data["rows"][0][column_index(ask_data, "debt_asset_ratio")])

    report = ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": "financial_position",
                "period": "2026-10",
                "comparison": "mom",
            },
            headers=headers("analyst"),
        )
    )
    assert kpi_value(report, "total_assets") == pytest.approx(assets, abs=0.01)
    assert kpi_value(report, "debt_asset_ratio") == pytest.approx(ratio, abs=0.0001)


@pytest.mark.parametrize(
    "template_code,section_count,required_types",
    [
        (
            "financial_position",
            8,
            {"kpi", "table", "chart", "text", "appendix"},
        ),
        (
            "income_statement",
            9,
            {"kpi", "table", "chart", "appendix"},
        ),
    ],
)
def test_report_structure_and_reconciliation(
    client, template_code: str, section_count: int, required_types: set[str]
) -> None:
    report = ok_data(
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
    assert len(report["sections"]) == section_count
    assert {section["type"] for section in report["sections"]} == required_types

    reconciliation = report["reconciliation"]
    assert reconciliation["passed"] is True
    assert len(reconciliation["checks"]) == 5
    assert all(check["passed"] for check in reconciliation["checks"])
    assert all(check["diff"] <= 0.01 for check in reconciliation["checks"])
    assert report["warnings"] == []
    assert report["appendix"]["metrics"]
    assert report["appendix"]["ontology_version"] == "2026.11.1"


@pytest.mark.parametrize(
    "comparison,expected",
    [("yoy", "2025-10"), ("mom", "2026-09"), ("none", None)],
)
def test_report_comparison_modes(client, comparison: str, expected: str | None) -> None:
    report = ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": "income_statement",
                "period": "2026-10",
                "comparison": comparison,
            },
            headers=headers("admin"),
        )
    )
    assert report["period"]["comparison"] == expected


def test_report_without_available_comparison_period(client) -> None:
    """2025-01 无去年同期数据时，comparison 应为 null 而非报错。"""
    report = ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": "financial_position",
                "period": "2025-01",
                "comparison": "yoy",
            },
            headers=headers("analyst"),
        )
    )
    assert report["period"]["comparison"] is None
    assert report["reconciliation"]["passed"] is True


def test_seed_reconciliation_latest_period(client) -> None:
    """播种数据在最新期间的资产、利润勾稽必须通过。"""
    seeded = client.post(
        "/api/sources/seed", json={"reset": True}, headers=headers("admin")
    )
    data = ok_data(seeded)
    assert data["reconciliation"]["passed"] is True
    for check in data["reconciliation"]["checks"]:
        assert check["passed"] is True, check
    assert data["rows"]["fact_balance"] == 1320
    assert data["rows"]["dim_period"] == 22
