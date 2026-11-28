"""勾稽知识化（S4）、跳过可观测性（CR-003）与降级契约（R3/D5）。

注入规则时只写临时 knowledge.db 或临时知识包副本，不改生产代码与仓库数据。
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from tests.knowledge_support import (
    insert_reconciliation_rule,
    remove_knowledge_db,
)
from tests.support import headers, ok_data

BUILTIN_CHECK_NAMES = [
    "资产总额=负债总额+所有者权益",
    "资产总额=流动资产+非流动资产",
    "负债总额=流动负债+非流动负债",
    "利润总额=收入-成本-税金-期间费用+营业外收支",
    "净利润=利润总额-所得税",
]

KNOWLEDGE_RULE_TITLES = {
    "试算平衡：凭证借贷发生额合计相等",
    "余额勾稽：期初余额加本期发生额等于期末余额",
    "销售发票勾稽：不含税金额加税额等于价税合计",
    "采购发票勾稽：不含税金额加税额等于价税合计",
    "费用分类勾稽：明细费用科目合计等于费用类科目合计",
}


def _income_report(client: TestClient) -> dict:
    return ok_data(
        client.post(
            "/api/reports/generate",
            json={
                "template_code": "income_statement",
                "period": "2026-10",
                "comparison": "none",
            },
            headers=headers("analyst"),
        )
    )


def test_reconciliation_extends_past_eight_and_all_pass(pack_client: TestClient) -> None:
    """S4：勾稽 5 条硬编码 + 5 条知识规则全部 passed，名称与顺序可追溯。"""
    reconciliation = _income_report(pack_client)["reconciliation"]
    checks = reconciliation["checks"]

    assert len(checks) >= 8
    assert len(checks) == 5 + reconciliation["knowledge_checks"]
    assert reconciliation["knowledge_checks"] == 5
    assert reconciliation["skipped"] == 0
    assert reconciliation["knowledge_warnings"] == []
    assert reconciliation["passed"] is True
    assert [check["name"] for check in checks[:5]] == BUILTIN_CHECK_NAMES
    assert {check["name"] for check in checks[5:]} == KNOWLEDGE_RULE_TITLES

    for check in checks:
        assert check["passed"] is True
        assert check["diff"] <= 0.01
    for check in checks[5:]:
        assert check["tolerance"] == 0.01
        assert check["basis"].strip()
        assert check["source_id"].strip()
        assert check["source_name"].strip()


def test_reconciliation_knowledge_count_matches_loaded_rules(
    pack_client: TestClient,
) -> None:
    """知识勾稽条数等于知识库 reconciliation_rule 条目数（防止知识层静默失效）。"""
    summary = ok_data(pack_client.get("/api/knowledge/summary"))
    rule_count = next(
        item["entries"] for item in summary["kinds"] if item["kind"] == "reconciliation_rule"
    )
    reconciliation = _income_report(pack_client)["reconciliation"]
    assert reconciliation["knowledge_checks"] == rule_count
    assert len(reconciliation["checks"]) == 5 + rule_count


def test_skipped_rule_logs_warning_and_reports_reason(
    pack_client: TestClient, caplog
) -> None:
    """CR-003 回归：非法规则被跳过时写 WARNING，skipped 如实计数并进入报告告警。"""
    insert_reconciliation_rule(
        "recon.test_drop",
        "DROP 注入规则",
        "DROP TABLE dim_customer -- :start_period",
        "SELECT 0 AS value FROM dim_customer WHERE :start_period = :start_period",
    )

    caplog.clear()
    caplog.set_level(logging.WARNING, logger="finance_ontology.reports")
    report = _income_report(pack_client)
    reconciliation = report["reconciliation"]

    assert reconciliation["skipped"] == 1
    assert reconciliation["knowledge_checks"] == 5
    assert len(reconciliation["checks"]) == 10
    assert reconciliation["passed"] is True
    assert any(
        "只允许 SELECT 或 WITH 查询" in warning
        for warning in reconciliation["knowledge_warnings"]
    ), reconciliation["knowledge_warnings"]

    log_text = caplog.text
    assert "entry_id=recon.test_drop" in log_text
    assert "side=left" in log_text
    assert "只允许 SELECT 或 WITH 查询" in log_text

    assert any("1 条知识勾稽规则已跳过" in warning for warning in report["warnings"])
    assert any("DROP 注入规则" in warning for warning in report["warnings"])


def test_scalar_failure_counts_as_skipped_and_logs(
    pack_client: TestClient, caplog
) -> None:
    """CR-003：SQL 可过 guard 但执行失败（坏列）时 skipped 计数不伪造 0。"""
    insert_reconciliation_rule(
        "recon.test_bad_column",
        "坏列规则",
        "SELECT COALESCE(SUM(nonexistent_col), 0) AS value FROM dim_customer "
        "WHERE :start_period = :start_period",
        "SELECT 0 AS value FROM dim_customer WHERE :start_period = :start_period",
    )

    caplog.clear()
    caplog.set_level(logging.WARNING, logger="finance_ontology.reports")
    reconciliation = _income_report(pack_client)["reconciliation"]

    assert reconciliation["skipped"] == 1
    assert reconciliation["knowledge_checks"] == 5
    assert any(
        "一侧取数结果为空" in warning
        for warning in reconciliation["knowledge_warnings"]
    )
    assert "rule_id=recon.test_bad_column" in caplog.text
    assert "nonexistent_col" in caplog.text


def test_pii_rule_skipped_without_emitting_sensitive_value(
    pack_client: TestClient, caplog
) -> None:
    """CR-001 回归（日志与结果双查）：敏感列拒绝后结果与日志都不含 PII 数值。"""
    insert_reconciliation_rule(
        "recon.test_pii",
        "客户银行账号长度合计",
        "SELECT ROUND(COALESCE(SUM(LENGTH(c.bank_account)), 0), 2) AS value "
        "FROM dim_customer c WHERE :start_period = :start_period",
        "SELECT 0 AS value FROM dim_customer WHERE :start_period = :start_period",
    )
    caplog.clear()
    caplog.set_level(logging.WARNING, logger="finance_ontology.reports")
    report = _income_report(pack_client)
    reconciliation = report["reconciliation"]

    assert reconciliation["skipped"] == 1
    for check in reconciliation["checks"]:
        assert check["left"] != 570.0 and check["right"] != 570.0, check
    assert "bank_account" in caplog.text
    # 日志只记录列名与原因，不输出聚合数值本身。
    assert "570" not in caplog.text


def test_health_check_endpoint_remains_available(pack_client: TestClient) -> None:
    """知识库降级期间健康检查与业务只读接口仍可用。"""
    remove_knowledge_db()
    assert ok_data(pack_client.get("/api/health"))["status"] in {"ok", "degraded"}
    ok_data(pack_client.get("/api/ontology/terms"))
    ok_data(pack_client.get("/api/metrics"))
