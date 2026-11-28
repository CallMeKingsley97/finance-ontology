"""知识消费方：本体增强（S2）、科目修正（S3）、指标依据与报告附录（S6）。

全部通过真实 HTTP 接口断言，知识包使用 tmp_path 副本。
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.knowledge_support import PACK_VERSION, remove_knowledge_db
from tests.support import headers, ok_data

CORRECTED_ACCOUNTS = ("1002", "1123", "2203", "6402", "6051")


def _seed_accounts() -> dict[str, str | None]:
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT account_code, parent_code FROM dim_account"
        ).fetchall()
    finally:
        conn.close()
    return {row["account_code"]: row["parent_code"] for row in rows}


def test_seeded_accounts_have_no_wrong_parent(client: TestClient) -> None:
    """S3：5 个被修正科目 parent_code 均为 NULL，且不存在任何父子关系。"""
    parents = _seed_accounts()
    for code in CORRECTED_ACCOUNTS:
        assert code in parents, code
        assert parents[code] is None, (code, parents[code])
    assert all(value is None for value in parents.values())


def test_seed_reconciliation_still_passes(client: TestClient) -> None:
    """S3 回归：科目修正后播种勾稽仍通过（R2 风险）。"""
    seeded = ok_data(
        client.post("/api/sources/seed", json={"reset": True}, headers=headers("admin"))
    )
    # 播种接口返回的是种子期勾稽（3 条独立校验），与 check_reconciliation 的 5+知识 不同源。
    assert seeded["reconciliation"]["passed"] is True
    assert all(check["passed"] for check in seeded["reconciliation"]["checks"])


def test_ontology_counts_unchanged_and_terms_enriched(
    pack_client: TestClient,
) -> None:
    """S2：本体生成计数 27/16/20 不变，被知识覆盖的术语带来源与追加同义词。"""
    generated = ok_data(
        pack_client.post(
            "/api/ontology/generate", json={"force": False}, headers=headers("admin")
        )
    )
    assert generated["status"] == "generated"
    assert generated["counts"]["terms"] == 27
    assert generated["counts"]["relations"] == 16
    assert generated["counts"]["sql_attributes"] == 20

    terms = {
        item["term_code"]: item
        for item in ok_data(
            pack_client.get("/api/ontology/terms", params={"limit": 200})
        )["items"]
    }
    account = terms["term_account"]
    assert account["source"] == "knowledge"
    assert {"科目", "账户", "会计账户", "科目表"} <= set(account["synonyms"])
    assert {"科目体系", "总账科目"} <= set(account["synonyms"])

    # 未被知识增强的术语保持 rule 来源。
    assert terms["term_company"]["source"] == "rule"

    relations = {
        item["relation_code"]: item
        for item in ok_data(pack_client.get("/api/ontology/relations"))["items"]
    }
    assert set(relations) >= {"rel_account_parent", "rel_journal_account"}


def test_ontology_without_knowledge_falls_back_to_rules(
    pack_client: TestClient,
) -> None:
    """R3：知识库不可用时本体生成计数不变、同义词回落到硬编码、来源为 rule。"""
    ok_data(
        pack_client.post(
            "/api/ontology/generate", json={"force": True}, headers=headers("admin")
        )
    )
    remove_knowledge_db()
    regenerated = ok_data(
        pack_client.post(
            "/api/ontology/generate", json={"force": True}, headers=headers("admin")
        )
    )
    assert regenerated["counts"]["terms"] == 27
    assert regenerated["counts"]["relations"] == 16
    assert regenerated["counts"]["sql_attributes"] == 20

    terms = {
        item["term_code"]: item
        for item in ok_data(
            pack_client.get("/api/ontology/terms", params={"limit": 200})
        )["items"]
    }
    assert terms["term_account"]["source"] == "rule"
    assert "科目体系" not in terms["term_account"]["synonyms"]


def test_metric_detail_contains_knowledge_basis(pack_client: TestClient) -> None:
    """S6：指标详情返回 basis/source_ref/source_name/source_url/knowledge_version。"""
    metric = ok_data(pack_client.get("/api/metrics/revenue_total"))
    assert metric["code"] == "revenue_total"
    assert metric["basis"].strip()
    assert metric["source_ref"] == "metric.basis_revenue_total"
    assert metric["source_name"].strip()
    assert metric["source_url"].startswith("http")
    assert metric["knowledge_version"] == "1.0.0"


def test_report_appendix_contains_definition_and_sources(
    pack_client: TestClient,
) -> None:
    """S6：报告附录每项指标含依据与来源，根部含知识来源清单与包版本。"""
    report = ok_data(
        pack_client.post(
            "/api/reports/generate",
            json={
                "template_code": "income_statement",
                "period": "2026-10",
                "comparison": "none",
            },
            headers=headers("analyst"),
        )
    )
    appendix = report["appendix"]
    assert appendix["ontology_version"] == PACK_VERSION
    assert appendix["knowledge_version"] == PACK_VERSION
    assert len(appendix["knowledge_sources"]) == 8
    for source in appendix["knowledge_sources"]:
        assert source["source_id"]
        assert source["name"]
        assert source["license"]
        assert "notice" in source

    assert appendix["metrics"]
    for metric in appendix["metrics"]:
        assert metric["definition"].strip(), metric["code"]
        assert metric["formula_text"].strip(), metric["code"]
        assert metric["basis"].strip(), metric["code"]
        assert metric["source_ref"], metric["code"]
        assert metric["source_name"], metric["code"]
        assert metric["knowledge_version"], metric["code"]


def test_report_package_appendix_metrics_carry_basis(
    pack_client: TestClient,
) -> None:
    """S6：报表包附录同样透出知识依据（与 metrics.appendix_metrics 一致）。"""
    package = ok_data(
        pack_client.post(
            "/api/report-packages/generate",
            json={
                "period": "2026-10",
                "templates": ["balance_sheet", "profit_statement", "management_report"],
                "comparison": "none",
            },
            headers=headers("admin"),
        )
    )
    assert package["appendix"]["metrics"]
    for metric in package["appendix"]["metrics"]:
        assert metric["source_ref"], metric["code"]
        assert metric["basis"].strip(), metric["code"]
        assert metric["knowledge_version"] == "1.0.0", metric["code"]
