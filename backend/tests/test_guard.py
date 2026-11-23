"""SQL 只读守卫、期间/意图解析与 LLM 敏感列绕过回归（P1-1）。"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import query as query_module
from app.services.query import (
    answer_question,
    detect_intent,
    guard_sql,
    parse_period,
)

SENSITIVE_SQL_CASES = {
    "bare_column": "SELECT phone FROM dim_customer WHERE :start_period <= :end_period",
    "table_alias": "SELECT c.phone FROM dim_customer c WHERE :start_period <= :end_period",
    "double_quoted": 'SELECT "phone" FROM dim_customer WHERE :start_period <= :end_period',
    "bracket_quoted": "SELECT [phone] FROM dim_customer WHERE :start_period <= :end_period",
    "backtick_quoted": "SELECT `bank_account` FROM dim_vendor WHERE :start_period <= :end_period",
    "function_wrapped": "SELECT substr(phone, 1, 3) AS p FROM dim_customer WHERE :start_period <= :end_period",
    "alias_output": "SELECT phone AS p FROM dim_customer WHERE :start_period <= :end_period",
    "cte": (
        "WITH x AS (SELECT phone AS p FROM dim_customer WHERE :start_period <= :end_period) "
        "SELECT p FROM x"
    ),
}


@pytest.mark.parametrize(
    "sql",
    SENSITIVE_SQL_CASES.values(),
    ids=SENSITIVE_SQL_CASES.keys(),
)
def test_llm_guard_rejects_sensitive_columns(sql: str) -> None:
    """LLM 路径（allow_sensitive=False）必须拒绝所有敏感列写法。"""
    result = guard_sql(sql, allow_sensitive=False)
    assert result.ok is False, result.sql
    assert "敏感列" in result.reason, result.reason


def test_llm_guard_allows_non_sensitive_sql() -> None:
    result = guard_sql(
        "SELECT customer_name, region FROM dim_customer WHERE :start_period <= :end_period",
        allow_sensitive=False,
    )
    assert result.ok is True, result.reason
    assert "LIMIT 1000" in result.sql


def test_rule_guard_still_allows_sensitive_templates() -> None:
    """规则模板默认 allow_sensitive=True，仍可查询敏感列（结果统一掩码）。"""
    result = guard_sql(
        "SELECT phone, bank_account FROM dim_customer WHERE :start_period <= :end_period"
    )
    assert result.ok is True, result.reason


guarded_errors = {
    "ddl": ("DROP TABLE dim_customer", "SELECT"),
    "dml_insert": ("INSERT INTO dim_customer (customer_code) VALUES ('x')", "SELECT"),
    "dml_delete": ("DELETE FROM dim_customer WHERE :start_period <= :end_period", "SELECT"),
    "multi_statement": (
        "SELECT customer_name FROM dim_customer WHERE :start_period <= :end_period; "
        "DROP TABLE dim_customer",
        "多语句",
    ),
    "select_star": ("SELECT * FROM dim_customer WHERE :start_period <= :end_period", "SELECT *"),
    "table_not_whitelisted": (
        "SELECT name FROM ontology_term WHERE :start_period <= :end_period",
        "白名单",
    ),
    "sqlite_object": (
        "SELECT name FROM sqlite_master WHERE :start_period <= :end_period",
        "sqlite",
    ),
    "missing_period": ("SELECT customer_name FROM dim_customer", "period"),
    "empty_sql": ("   ", "为空"),
}


@pytest.mark.parametrize(
    "sql,reason_fragment",
    guarded_errors.values(),
    ids=guarded_errors.keys(),
)
def test_guard_rejects_unsafe_sql(sql: str, reason_fragment: str) -> None:
    result = guard_sql(sql)
    assert result.ok is False
    assert reason_fragment.lower() in result.reason.lower(), result.reason


def test_guard_appends_and_clamps_limit() -> None:
    base = "SELECT customer_name FROM dim_customer WHERE :start_period <= :end_period"
    guarded = guard_sql(base)
    assert guarded.ok is True
    assert guarded.sql.rstrip().endswith("LIMIT 1000")

    clamped = guard_sql(f"{base} LIMIT 5000")
    assert clamped.ok is True
    assert "LIMIT 1000" in clamped.sql
    assert "5000" not in clamped.sql


def test_guard_allows_trailing_semicolon_and_comments() -> None:
    with_semicolon = guard_sql(
        "SELECT customer_name FROM dim_customer WHERE :start_period <= :end_period;"
    )
    assert with_semicolon.ok is True, with_semicolon.reason

    with_comment = guard_sql(
        "-- 只读查询\n"
        "SELECT customer_name FROM dim_customer WHERE :start_period <= :end_period"
    )
    assert with_comment.ok is True, with_comment.reason


@pytest.mark.parametrize(
    "question,start,end,comparison_start,comparison_end",
    [
        ("2026年10月营业收入", "2026-10", "2026-10", None, None),
        ("2026-10 营业收入同比", "2026-10", "2026-10", "2025-10", "2025-10"),
        ("2026Q3 毛利率", "2026-07", "2026-09", None, None),
        ("2026年第三季度毛利率", "2026-07", "2026-09", None, None),
        ("2026年度营业收入", "2026-01", "2026-12", None, None),
        ("近6个月收入趋势", "2026-05", "2026-10", None, None),
        ("上月营业收入", "2026-09", "2026-09", None, None),
        ("去年同期营业收入", "2025-10", "2025-10", None, None),
        ("2026年10月营业收入环比", "2026-10", "2026-10", "2026-09", "2026-09"),
    ],
)
def test_parse_period(
    question: str,
    start: str,
    end: str,
    comparison_start: str | None,
    comparison_end: str | None,
) -> None:
    slots = parse_period(question, "2026-10")
    assert (slots.start_code, slots.end_code) == (start, end)
    assert (slots.comparison_start, slots.comparison_end) == (
        comparison_start,
        comparison_end,
    )


def test_parse_period_defaults_and_current_month_clamp() -> None:
    default = parse_period("营业收入", "2026-10")
    assert (default.start_code, default.end_code) == ("2026-10", "2026-10")
    assert default.assumption and "最新期间" in default.assumption

    current = parse_period("本月营业收入", "2026-10")
    assert (current.start_code, current.end_code) == ("2026-10", "2026-10")
    assert current.assumption and "暂无数据" in current.assumption


@pytest.mark.parametrize(
    "question,intent",
    [
        ("2026年10月营业收入", "revenue_lookup"),
        ("2026年10月营业成本", "cost_lookup"),
        ("2026Q3 毛利率", "gross_margin"),
        ("2026年10月期间费用构成", "period_expense"),
        ("2026年10月营业利润", "operating_profit"),
        ("2026年10月净利润", "net_profit"),
        ("2026年10月资产总额和资产负债率", "balance_overview"),
        ("2026年10月 Top 5 客户", "top_customers"),
        ("2026年10月收入预算达成率", "budget_variance"),
        ("2026年10月应收账款余额", "receivable_payable"),
        ("客户明细", "customer_detail"),
        ("2026年10月供应商明细", "vendor_detail"),
        ("近6个月收入趋势", "trend"),
        ("什么是营业收入", "definition"),
        ("今天天气怎么样", "unsupported"),
    ],
)
def test_detect_intent(seeded_db: dict, question: str, intent: str) -> None:
    assert detect_intent(question) == intent


class _FakeResponse:
    """最小化的 httpx 响应替身，仅供 LLM 回退测试。"""

    def __init__(self, content: str) -> None:
        self.status_code = 200
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _enable_fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_model", "test-model")


def test_llm_alias_sql_is_rejected_and_falls_back_to_rule(
    seeded_db: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-1 回归：LLM 生成 phone AS p 时必须拒绝并回退规则引擎。"""
    _enable_fake_llm(monkeypatch)
    content = json.dumps(
        {
            "sql": (
                "SELECT c.phone AS p FROM dim_customer c "
                "WHERE :start_period <= :end_period LIMIT 10"
            ),
            "explanation": "alias bypass",
            "chart_type": "none",
        }
    )
    monkeypatch.setattr(
        query_module.httpx, "post", lambda *args, **kwargs: _FakeResponse(content)
    )

    answer = answer_question("客户明细", "viewer", 10)

    assert answer["engine"] == "rule"
    assert any("LLM" in warning for warning in answer["warnings"])
    assert set(answer["masked_columns"]) == {"phone", "bank_account"}
    phone_index = [column["name"] for column in answer["columns"]].index("phone")
    assert answer["rows"]
    assert all(row[phone_index] == "***" for row in answer["rows"])


def test_llm_non_sensitive_sql_is_executed(
    seeded_db: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allow_sensitive=False 不应误伤普通列查询。"""
    _enable_fake_llm(monkeypatch)
    content = json.dumps(
        {
            "sql": (
                "SELECT customer_name FROM dim_customer "
                "WHERE :start_period <= :end_period LIMIT 3"
            ),
            "explanation": "普通列查询",
            "chart_type": "none",
        }
    )
    monkeypatch.setattr(
        query_module.httpx, "post", lambda *args, **kwargs: _FakeResponse(content)
    )

    answer = answer_question("客户明细", "viewer", 3)

    assert answer["engine"] == "llm"
    assert answer["row_count"] == 3
    assert answer["sql"].lstrip().upper().startswith("SELECT")
