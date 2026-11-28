"""问数知识层：术语层意图、provenance 两键（CR-002）、行业知识基准题（S5）。

沿用 tests/support.ask 与真实知识包；12 道既有基准题不回归的断言在
tests/test_reports.py 与 tests/test_query.py 既有用例中覆盖，本文件只补
行业知识类问题及其溯源。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.knowledge_support import (
    EXPECTED_ENTRIES,
    PACK_VERSION,
    insert_reconciliation_rule,
    remove_knowledge_db,
)
from tests.support import ask, headers, ok_data

# 6 道行业知识基准题：均为知识包 terminology.json 已登记的「行业说法」。
# 「什么是 X」句式会先命中 definition 特殊意图（早于知识术语层，属设计优先级）；
# definition 分支在本体术语未命中时会回落到知识包 glossary/terminology 定义，
# 覆盖见下方 test_definition_phrasing_falls_back_to_glossary 系列用例。
INDUSTRY_QUESTIONS = [
    ("银行存款是流动资产吗", "knowledge_lookup", "glossary.current_assets"),
    ("借记什么科目", "knowledge_lookup", "glossary.double_entry"),
    ("什么导致试算不平衡", "knowledge_lookup", "glossary.trial_balance"),
    ("权责发生制和收付实现制有什么区别", "knowledge_lookup", "glossary.accrual_basis"),
    ("折旧是什么", "knowledge_lookup", "glossary.depreciation"),
    ("会计恒等式是什么", "knowledge_lookup", "glossary.equity_equation"),
]


DEFINITION_PHRASING_CASES = [
    ("什么是复式记账", "glossary.double_entry"),
    ("什么是试算平衡", "glossary.trial_balance"),
    ("什么是会计恒等式", "glossary.equity_equation"),
]


@pytest.mark.parametrize(
    "question,expected_entry",
    DEFINITION_PHRASING_CASES,
    ids=[item[0] for item in DEFINITION_PHRASING_CASES],
)
def test_definition_phrasing_falls_back_to_glossary(
    pack_client: TestClient, question: str, expected_entry: str
) -> None:
    """「什么是X」在 definition 意图下回落到知识包定义并携带 provenance。

    回归自上一轮打回缺陷：knowledge/service.py 的 find_definition_entry 与
    query.py 的 _knowledge_definition_hit 兜底，要求命中条目、返回 statement、
    provenance ids 非空且 explanation 标明知识包来源。
    """
    data = ask(pack_client, question, "viewer")
    assert data["answerable"] is True
    assert data["intent"] == "definition"
    assert data["sql"] is None
    assert data["engine"] == "rule"
    provenance = data["provenance"]
    assert provenance["knowledge_entry_ids"] == [expected_entry]
    assert provenance["knowledge_version"] == PACK_VERSION
    assert "未在本体术语库中找到" not in data["answer"]
    detail = ok_data(pack_client.get(f"/api/knowledge/entries/{expected_entry}"))
    assert detail["statement"] in data["answer"]
    assert data["explanation"].startswith("命中知识包术语定义")
    assert "依据：" in data["answer"]


@pytest.mark.parametrize(
    "question,expected_entry",
    [
        ("什么是流动资产", "glossary.current_assets"),
        ("什么是呆账", "term.bad_debt"),
    ],
    ids=["什么是流动资产", "什么是呆账"],
)
def test_definition_phrasing_hits_terminology_entries(
    pack_client: TestClient, question: str, expected_entry: str
) -> None:
    """terminology 条目在定义兜底中可用：concept 目标归并，metric 目标返回自身。"""
    data = ask(pack_client, question, "viewer")
    assert data["intent"] == "definition"
    assert data["provenance"]["knowledge_entry_ids"] == [expected_entry]
    detail = ok_data(pack_client.get(f"/api/knowledge/entries/{expected_entry}"))
    assert detail["statement"] in data["answer"]


@pytest.mark.parametrize(
    "question",
    ["什么是财务自由", "什么是税务筹划"],
    ids=["财务自由", "税务筹划"],
)
def test_definition_phrasing_unknown_term_returns_no_entry(
    pack_client: TestClient, question: str
) -> None:
    """反例：知识包未登记的术语不误配，provenance ids 为空且提示未找到。"""
    data = ask(pack_client, question, "viewer")
    assert data["intent"] == "definition"
    assert data["answerable"] is True
    assert data["provenance"]["knowledge_entry_ids"] == []
    assert "未在本体术语库中找到" in data["answer"]
    assert "命中知识包术语定义" not in data["explanation"]


def test_metric_question_still_prefers_revenue_lookup_over_glossary(
    pack_client: TestClient,
) -> None:
    """优先级回归：「2026年10月营业收入」仍走 revenue_lookup，不被术语兜底抢占。"""
    data = ask(pack_client, "2026年10月营业收入", "viewer")
    assert data["intent"] == "revenue_lookup"
    assert data["sql"]
    assert data["answer"].startswith("2026年10月营业收入为")
    assert "未在本体术语库中找到" not in data["answer"]
    assert data["provenance"]["knowledge_entry_ids"] == [
        "metric.basis_revenue_total"
    ]


@pytest.mark.parametrize(
    "question,expected_entry",
    [
        ("解释一下权责发生制", "glossary.accrual_basis"),
        ("试算平衡的含义", "glossary.trial_balance"),
    ],
    ids=["解释一下权责发生制", "试算平衡的含义"],
)
def test_definition_phrasing_variants_hit_glossary(
    pack_client: TestClient, question: str, expected_entry: str
) -> None:
    """问法变体（解释/含义）与「什么是X」同样回落到知识包定义。"""
    data = ask(pack_client, question, "viewer")
    assert data["intent"] == "definition"
    assert data["provenance"]["knowledge_entry_ids"] == [expected_entry]
    detail = ok_data(pack_client.get(f"/api/knowledge/entries/{expected_entry}"))
    assert detail["statement"] in data["answer"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "CR3-002 已知偏差（生产代码归 developer）：find_definition_entry 用子串包含"
        "匹配术语名，『非流动资产』包含『流动资产』，且匹配前未做否定/前缀边界判断，"
        "导致『什么是非流动资产』误返回 glossary.current_assets 的流动资产定义。"
        "期望行为：不误配到流动资产，返回未找到或独立的非流动资产定义。"
    ),
)
def test_definition_negated_prefix_does_not_match_substring_term(
    pack_client: TestClient,
) -> None:
    """否定前缀问句不应命中被否定的子串术语（当前误命中，记录为已知偏差）。"""
    data = ask(pack_client, "什么是非流动资产", "viewer")
    assert data["provenance"]["knowledge_entry_ids"] != ["glossary.current_assets"]
    assert "流动资产是预计在一个正常营业周期内变现" not in data["answer"]


@pytest.mark.parametrize(
    "question,expected_intent,expected_entry",
    INDUSTRY_QUESTIONS,
    ids=[item[0] for item in INDUSTRY_QUESTIONS],
)
def test_industry_knowledge_questions_answerable(
    pack_client: TestClient,
    question: str,
    expected_intent: str,
    expected_entry: str,
) -> None:
    """S5：行业知识基准题可答、意图正确、答案非空且溯源到知识条目。"""
    data = ask(pack_client, question, "viewer")
    assert data["answerable"] is True
    assert data["intent"] == expected_intent
    assert data["answer"].strip()
    assert data["sql"] is None
    assert data["engine"] == "rule"
    provenance = data["provenance"]
    assert expected_entry in provenance["knowledge_entry_ids"]
    assert provenance["knowledge_version"] == PACK_VERSION
    assert data["explanation"].startswith("命中知识条目")


def test_knowledge_lookup_answer_carries_basis_and_source(
    pack_client: TestClient,
) -> None:
    """知识解答附依据与来源，便于溯源展示。"""
    data = ask(pack_client, "银行存款是流动资产吗", "viewer")
    assumptions = "\n".join(data["assumptions"])
    assert "依据：" in assumptions
    assert "来源：" in assumptions
    detail = ok_data(
        pack_client.get("/api/knowledge/entries/glossary.current_assets")
    )
    assert detail["statement"] in data["answer"]


def test_twelve_baseline_intents_unchanged(pack_client: TestClient) -> None:
    """R1 回归：12 道既有基准题 answerable 全 True 且意图序列与改动前一致。"""
    questions = [
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
    expected_intents = [
        "revenue_lookup",
        "revenue_lookup",
        "gross_margin",
        "period_expense",
        "operating_profit",
        "net_profit",
        "balance_overview",
        "top_customers",
        "budget_variance",
        "receivable_payable",
        "customer_detail",
        "trend",
    ]
    actual = [ask(pack_client, question, "viewer")["intent"] for question in questions]
    assert actual == expected_intents

    for question in questions:
        data = ask(pack_client, question, "viewer")
        assert data["answerable"] is True, question
        assert data["provenance"]["knowledge_version"] == PACK_VERSION, question


def test_industry_question_does_not_override_keyword_intent(
    pack_client: TestClient,
) -> None:
    """R1：「欠款有多少」仍为 receivable_payable，知识术语层不抢占关键词意图。"""
    data = ask(pack_client, "欠款有多少", "viewer")
    assert data["intent"] == "receivable_payable"
    assert data["answerable"] is True
    assert data["sql"]
    assert data["provenance"]["knowledge_entry_ids"] == [
        "metric.basis_receivable_total"
    ]


def test_all_intents_carry_provenance_keys(pack_client: TestClient) -> None:
    """CR-002 回归：指标/趋势/定义三类意图的 provenance 均带知识两键。"""
    cases = {
        "指标": "2026年10月营业收入",
        "趋势": "近 6 个月收入趋势",
        "定义": "营业收入定义",
    }
    for label, question in cases.items():
        data = ask(pack_client, question, "viewer")
        provenance = data["provenance"]
        assert "knowledge_entry_ids" in provenance, label
        assert "knowledge_version" in provenance, label
        assert isinstance(provenance["knowledge_entry_ids"], list), label
        assert provenance["knowledge_version"] == PACK_VERSION, label
        assert provenance["knowledge_entry_ids"], label

    definition = ask(pack_client, "营业收入定义", "viewer")
    assert definition["intent"] == "definition"
    assert "metric.basis_revenue_total" in (
        definition["provenance"]["knowledge_entry_ids"]
    )
    assert definition.get("metric_card")
    assert definition["metric_card"]["basis"].strip()
    assert definition["metric_card"]["source_ref"]
    assert "依据：" in definition["answer"]

    unsupported = ask(pack_client, "今天天气怎么样", "viewer")
    assert unsupported["intent"] == "unsupported"
    assert unsupported["provenance"]["knowledge_entry_ids"] == []
    assert unsupported["provenance"]["knowledge_version"] == PACK_VERSION


def test_definition_answer_without_knowledge_degrades_without_error(
    pack_client: TestClient,
) -> None:
    """知识不可用时定义回答仍 200，只是不附依据且版本为 null。"""
    remove_knowledge_db()
    data = ask(pack_client, "营业收入定义", "viewer")
    assert data["answerable"] is True
    assert data["intent"] == "definition"
    assert data["provenance"]["knowledge_version"] is None
    assert data["provenance"]["knowledge_entry_ids"] == []
    assert "依据：" not in data["answer"]


def test_knowledge_reload_after_new_rule_enables_new_provenance(
    pack_client: TestClient,
) -> None:
    """新增知识规则后 reload，随即可在报告中看到 skipped 计数与告警。"""
    insert_reconciliation_rule(
        "recon.test_drop",
        "DROP 注入规则",
        "DROP TABLE dim_customer -- :start_period",
        "SELECT 0 AS value FROM dim_customer WHERE :start_period = :start_period",
    )
    summary = ok_data(pack_client.get("/api/knowledge/summary"))
    assert summary["entries"] == EXPECTED_ENTRIES + 1

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
    reconciliation = report["reconciliation"]
    assert reconciliation["skipped"] == 1
    assert len(reconciliation["checks"]) == 10
    assert any(
        "只允许 SELECT 或 WITH 查询" in warning
        for warning in reconciliation["knowledge_warnings"]
    )
    assert all(check["passed"] for check in reconciliation["checks"])
