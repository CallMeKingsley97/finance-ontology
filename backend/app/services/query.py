"""规则驱动 NL2SQL：期间/意图解析、只读守卫、执行、脱敏、审计与可选 LLM。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings
from app.db.connection import BUSINESS_TABLES, connect, query_all, query_one
from app.schemas.common import AppError
from app.services.security import SENSITIVE_COLUMN_NAMES, mask_rows, record_audit

ONTOLOGY_VERSION = "2026.11.1"
ALLOWED_TABLES = frozenset(BUSINESS_TABLES)
FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REINDEX",
    "TRIGGER",
    "LOAD_EXTENSION",
)

SUGGESTIONS = [
    "2026年10月营业收入",
    "2026Q3 毛利率",
    "2026年10月期间费用构成",
    "2026年10月资产总额和资产负债率",
    "2026年10月 Top 5 客户",
]

METRIC_INTENT_MAP = {
    "metric_revenue_total": "revenue_lookup",
    "metric_cogs_total": "cost_lookup",
    "metric_gross_profit": "gross_margin",
    "metric_gross_margin": "gross_margin",
    "metric_period_expense": "period_expense",
    "metric_operating_profit": "operating_profit",
    "metric_total_profit": "net_profit",
    "metric_net_profit": "net_profit",
    "metric_total_assets": "balance_overview",
    "metric_total_liabilities": "balance_overview",
    "metric_total_equity": "balance_overview",
    "metric_debt_asset_ratio": "balance_overview",
    "metric_current_ratio": "balance_overview",
    "metric_budget_variance": "budget_variance",
    "revenue_total": "revenue_lookup",
    "cogs_total": "cost_lookup",
    "gross_profit": "gross_margin",
    "gross_margin": "gross_margin",
    "period_expense": "period_expense",
    "operating_profit": "operating_profit",
    "total_profit": "net_profit",
    "net_profit": "net_profit",
    "total_assets": "balance_overview",
    "total_liabilities": "balance_overview",
    "total_equity": "balance_overview",
    "debt_asset_ratio": "balance_overview",
    "current_ratio": "balance_overview",
    "budget_variance": "budget_variance",
    "receivable_total": "receivable_payable",
    "inventory_total": "metric_lookup",
    "operating_cash_flow": "metric_lookup",
    "expense_ratio": "metric_lookup",
    "budget_achievement_rate": "budget_variance",
    "active_customer_count": "metric_lookup",
}

TABLE_INTENT_MAP = {
    "term_customer": "customer_detail",
    "term_vendor": "vendor_detail",
    "sensitive_customer_phone": "customer_detail",
    "sensitive_customer_bank_account": "customer_detail",
    "sensitive_vendor_bank_account": "vendor_detail",
}


@dataclass
class PeriodSlots:
    start_code: str
    end_code: str
    label: str
    comparison_start: str | None = None
    comparison_end: str | None = None
    comparison_label: str | None = None
    assumption: str | None = None


@dataclass
class GuardResult:
    ok: bool
    sql: str
    reason: str = ""


def format_amount(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 10_000:
        return f"{number / 10_000:,.2f} 万元"
    return f"{number:,.2f} 元"


def format_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def format_ratio(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f} 倍"
    except (TypeError, ValueError):
        return str(value)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _parse_code(code: str) -> tuple[int, int]:
    year, month = code.split("-")
    return int(year), int(month)


def _format_code(year: int, month: int) -> str:
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}"


def _add_months(code: str, delta: int) -> str:
    year, month = _parse_code(code)
    return _format_code(year, month + delta)


def _period_label(start_code: str, end_code: str) -> str:
    if start_code == end_code:
        year, month = _parse_code(start_code)
        return f"{year}年{month}月"
    return f"{start_code} 至 {end_code}"


def _latest_period() -> str | None:
    try:
        row = query_one("SELECT period_code FROM dim_period ORDER BY period_code DESC LIMIT 1")
    except sqlite3.Error as exc:
        raise AppError(503, "DB_UNAVAILABLE", "数据库不可用或尚未播种", {"error": str(exc)}) from exc
    return row["period_code"] if row else None


def parse_period(text: str, latest: str) -> PeriodSlots:
    """解析中文期间与同比/环比，未指定时默认最新期间。"""
    normalized = _normalize(text)
    start_code = end_code = latest
    assumption: str | None = None

    month_match = re.search(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*月?", text)
    quarter_match = re.search(r"(\d{4})\s*[Qq]\s*([1-4])", text)
    cn_quarter_match = re.search(r"(\d{4})\s*年\s*第?\s*([一二三四1-4])\s*季度", text)
    year_match = re.search(r"(\d{4})\s*年(?:度|全年|整年|会计年度)?", text)
    relative_match = re.search(r"(?:近|过去|最近)\s*(\d{1,2})\s*个?月", text)

    if month_match:
        year, month = int(month_match.group(1)), int(month_match.group(2))
        if 1 <= month <= 12:
            start_code = end_code = f"{year:04d}-{month:02d}"
    elif quarter_match:
        year, quarter = int(quarter_match.group(1)), int(quarter_match.group(2))
        start_code = f"{year:04d}-{(quarter - 1) * 3 + 1:02d}"
        end_code = f"{year:04d}-{quarter * 3:02d}"
    elif cn_quarter_match:
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
        year = int(cn_quarter_match.group(1))
        quarter = mapping[cn_quarter_match.group(2)]
        start_code = f"{year:04d}-{(quarter - 1) * 3 + 1:02d}"
        end_code = f"{year:04d}-{quarter * 3:02d}"
    elif relative_match:
        months = max(1, min(int(relative_match.group(1)), 24))
        start_code = _add_months(latest, -(months - 1))
        end_code = latest
    elif "本年" in normalized or "今年" in normalized:
        year, _ = _parse_code(latest)
        start_code, end_code = f"{year:04d}-01", latest
    elif "上月" in normalized or "上期" in normalized:
        start_code = end_code = _add_months(latest, -1)
    elif "去年同期" in normalized:
        start_code = end_code = _add_months(latest, -12)
    elif "本月" in normalized or "这个月" in normalized:
        current = datetime.now().strftime("%Y-%m")
        if current > latest:
            start_code = end_code = latest
            assumption = f"当前自然月暂无数据，已使用最新期间 {latest}"
        else:
            start_code = end_code = current
    elif year_match:
        year = int(year_match.group(1))
        start_code = f"{year:04d}-01"
        end_code = f"{year:04d}-12"
    else:
        assumption = f"未指定期间，默认使用最新期间 {latest}"

    comparison_start = comparison_end = None
    comparison_label = None
    if "同比" in text:
        comparison_start = _add_months(start_code, -12)
        comparison_end = _add_months(end_code, -12)
        comparison_label = "同比"
    elif "环比" in text:
        comparison_start = _add_months(start_code, -1)
        comparison_end = _add_months(end_code, -1)
        comparison_label = "环比"

    return PeriodSlots(
        start_code=start_code,
        end_code=end_code,
        label=_period_label(start_code, end_code),
        comparison_start=comparison_start,
        comparison_end=comparison_end,
        comparison_label=comparison_label,
        assumption=assumption,
    )


def _ontology_terms() -> list[dict[str, Any]]:
    try:
        rows = query_all(
            """SELECT term_code, display_name, description, synonyms, category
               FROM ontology_term"""
        )
    except sqlite3.Error:
        return []
    terms = []
    for row in rows:
        try:
            synonyms = json.loads(row["synonyms"] or "[]")
        except json.JSONDecodeError:
            synonyms = []
        terms.append(
            {
                "term_code": row["term_code"],
                "display_name": row["display_name"],
                "description": row["description"],
                "synonyms": synonyms,
                "category": row["category"],
            }
        )
    return terms


def _find_term_by_alias(text: str) -> dict[str, Any] | None:
    normalized = _normalize(text)
    best: tuple[int, dict[str, Any]] | None = None
    for term in _ontology_terms():
        for alias in [term["display_name"], *term["synonyms"]]:
            if alias and _normalize(alias) in normalized:
                score = len(alias)
                if best is None or score > best[0]:
                    best = (score, term)
    return best[1] if best else None


def detect_intent(text: str) -> str:
    """按 tech-design §5.3 的优先级识别意图，并读取本体术语别名校准。"""
    normalized = _normalize(text)
    if any(word in normalized for word in ("什么是", "口径", "定义", "含义", "解释")):
        return "definition"
    if any(word in normalized for word in ("top", "排名", "前5", "前五", "前10", "大客户", "客户排名")):
        return "top_customers"
    if any(word in normalized for word in ("预算", "达成率", "预算差异")):
        return "budget_variance"
    if any(word in normalized for word in ("应收", "应付", "回款", "欠款")):
        return "receivable_payable"
    if "趋势" in normalized or (
        re.search(r"(?:近|过去|最近)\s*\d{1,2}\s*个?月", normalized)
        and any(word in normalized for word in ("收入", "营收", "利润", "净利润"))
    ):
        return "trend"
    if any(
        word in normalized
        for word in ("资产总额", "总资产", "资产负债率", "流动比率", "负债总额", "总负债", "所有者权益", "股东权益", "净资产")
    ):
        return "balance_overview"
    if any(word in normalized for word in ("净利润", "净利", "税后利润", "利润总额", "税前利润")):
        return "net_profit"
    if any(word in normalized for word in ("营业利润", "经营利润")):
        return "operating_profit"
    if "利润" in normalized:
        return "net_profit"
    if any(word in normalized for word in ("期间费用", "费用构成", "费用率", "费用总额", "销售费用", "管理费用", "研发费用", "财务费用", "费用")):
        return "period_expense"
    if any(word in normalized for word in ("毛利率", "毛利")):
        return "gross_margin"
    if any(word in normalized for word in ("营业成本", "成本率", "销售成本", "成本")):
        return "cost_lookup"
    if any(word in normalized for word in ("营业收入", "营收", "收入", "营业额", "销售收入")):
        return "revenue_lookup"
    if any(word in normalized for word in ("客户明细", "客户列表", "客户信息", "客户清单", "客户有哪些")):
        return "customer_detail"
    if any(word in normalized for word in ("供应商明细", "供应商列表", "供应商信息", "供应商清单", "供应商有哪些")):
        return "vendor_detail"

    # 本体术语校准：display_name / synonyms 命中后映射到对应意图
    term = _find_term_by_alias(text)
    if term:
        if term["term_code"] in METRIC_INTENT_MAP:
            return METRIC_INTENT_MAP[term["term_code"]]
        if term["term_code"] in TABLE_INTENT_MAP:
            return TABLE_INTENT_MAP[term["term_code"]]
    if "客户" in normalized:
        return "customer_detail"
    if "供应商" in normalized:
        return "vendor_detail"
    return "unsupported"


def _detect_special_intent(question: str, normalized: str) -> str | None:
    """识别必须优先于指标别名的特殊意图。"""
    if any(word in normalized for word in ("什么是", "口径", "定义", "含义", "解释")):
        return "definition"
    if any(word in normalized for word in ("top", "排名", "前5", "前五", "前10", "大客户", "客户排名")):
        return "top_customers"
    if "趋势" in normalized or (
        re.search(r"(?:近|过去|最近)\s*\d{1,2}\s*个?月", normalized)
        and any(word in normalized for word in ("收入", "营收", "利润", "净利润"))
    ):
        return "trend"
    if any(word in normalized for word in ("客户明细", "客户列表", "客户信息", "客户清单", "客户有哪些")):
        return "customer_detail"
    if any(word in normalized for word in ("供应商明细", "供应商列表", "供应商信息", "供应商清单", "供应商有哪些")):
        return "vendor_detail"
    return None


def _detect_keyword_metric_intent(normalized: str) -> str | None:
    """原 14 类意图中的指标/报表关键词识别（保持原判定顺序）。"""
    if any(word in normalized for word in ("预算", "达成率", "预算差异")):
        return "budget_variance"
    if any(word in normalized for word in ("应收", "应付", "回款", "欠款")):
        return "receivable_payable"
    if any(
        word in normalized
        for word in ("资产总额", "总资产", "资产负债率", "流动比率", "负债总额", "总负债", "所有者权益", "股东权益", "净资产")
    ):
        return "balance_overview"
    if any(word in normalized for word in ("净利润", "净利", "税后利润", "利润总额", "税前利润")):
        return "net_profit"
    if any(word in normalized for word in ("营业利润", "经营利润")):
        return "operating_profit"
    if "利润" in normalized:
        return "net_profit"
    if any(word in normalized for word in ("期间费用", "费用构成", "费用率", "费用总额", "销售费用", "管理费用", "研发费用", "财务费用", "费用")):
        return "period_expense"
    if any(word in normalized for word in ("毛利率", "毛利")):
        return "gross_margin"
    if any(word in normalized for word in ("营业成本", "成本率", "销售成本", "成本")):
        return "cost_lookup"
    if any(word in normalized for word in ("营业收入", "营收", "收入", "营业额", "销售收入")):
        return "revenue_lookup"
    return None


def detect_intent_with_resolution(question: str) -> tuple[str, Any | None]:
    """别名优先的意图识别：特殊意图 → 别名 → 关键词 → 本体 → 客户/供应商。"""
    from app.services import alias_resolver

    normalized = _normalize(question)
    special = _detect_special_intent(question, normalized)
    if special:
        return special, None
    resolution = alias_resolver.resolve(question)
    if resolution.kind == "ambiguous":
        return "metric_ambiguous", resolution
    if resolution.kind == "exclusion":
        return "metric_exclusion", resolution
    if resolution.kind == "matched" and resolution.metric_code:
        mapped = METRIC_INTENT_MAP.get(resolution.metric_code)
        return (mapped or "metric_lookup"), resolution
    keyword = _detect_keyword_metric_intent(normalized)
    if keyword:
        return keyword, None
    term = _find_term_by_alias(question)
    if term:
        if term["term_code"] in METRIC_INTENT_MAP:
            return METRIC_INTENT_MAP[term["term_code"]], None
        if term["term_code"] in TABLE_INTENT_MAP:
            return TABLE_INTENT_MAP[term["term_code"]], None
    if "客户" in normalized:
        return "customer_detail", None
    if "供应商" in normalized:
        return "vendor_detail", None
    return "unsupported", None


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _cte_names(sql: str) -> set[str]:
    return {
        match.group(1).lower()
        for match in re.finditer(r"(?:\bWITH\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", sql, re.I)
    }


def guard_sql(
    sql: str,
    allow_no_period: bool = False,
    allow_sensitive: bool = True,
    *,
    allowed_tables: frozenset[str] | None = None,
) -> GuardResult:
    """只读守卫：单条 SELECT、表白名单、禁 DDL/DML、禁 SELECT *、强制 LIMIT。

    allowed_tables 为空时沿用业务表 10 张白名单；质量规则查询 staging 时传入
    staging 白名单。allow_sensitive=False 用于 LLM/指标公式：禁止引用
    phone/bank_account 等敏感列，避免通过列别名或函数包裹绕过结果脱敏。
    """
    table_whitelist = ALLOWED_TABLES if allowed_tables is None else allowed_tables
    if not sql or not str(sql).strip():
        return GuardResult(False, "", "SQL 为空")
    body = _strip_sql_comments(str(sql)).strip()
    while body.endswith(";"):
        body = body[:-1].strip()
    if not body:
        return GuardResult(False, "", "SQL 为空")
    if ";" in body:
        return GuardResult(False, "", "禁止多语句")
    upper = body.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return GuardResult(False, "", "只允许 SELECT 或 WITH 查询")
    if "SELECT" not in upper:
        return GuardResult(False, "", "缺少 SELECT")
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            return GuardResult(False, "", f"禁止关键字 {keyword}")
    if re.search(r"\bSQLITE_", upper):
        return GuardResult(False, "", "禁止访问 sqlite_ 系统对象")
    if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\*", upper):
        return GuardResult(False, "", "禁止 SELECT *，必须明确字段")
    if not allow_sensitive:
        # 覆盖裸列名、表别名（c.phone）、引号（"phone"/[phone]/`phone`）与函数包裹
        for name in sorted(SENSITIVE_COLUMN_NAMES):
            pattern = rf"""(?<![\w])["`\[]?{re.escape(name)}["`\]]?(?![\w])"""
            if re.search(pattern, body, re.I):
                return GuardResult(False, "", f"LLM 路径禁止引用敏感列: {name}")
    cte_names = _cte_names(body)
    table_pattern = re.compile(
        r"""\b(?:FROM|JOIN)\s+(?:"([A-Za-z_][A-Za-z0-9_]*)"|\[([A-Za-z_][A-Za-z0-9_]*)\]|`([A-Za-z_][A-Za-z0-9_]*)`|([A-Za-z_][A-Za-z0-9_]*))""",
        re.I,
    )
    tables = {
        next(group for group in match.groups() if group).lower()
        for match in table_pattern.finditer(body)
    }
    invalid = sorted(name for name in tables if name not in table_whitelist and name not in cte_names)
    if invalid:
        return GuardResult(False, "", f"表不在白名单: {', '.join(invalid)}")
    if not allow_no_period and not re.search(r":(?:period_id|start_period)\b", body, re.I):
        return GuardResult(False, "", "必须包含 :period_id 或 :start_period 参数")

    tail_limit = re.search(r"\bLIMIT\s+(\d+|:[A-Za-z_][A-Za-z0-9_]*)\s*$", body, re.I)
    if not tail_limit:
        body = f"{body}\nLIMIT 1000"
    elif tail_limit.group(1).isdigit() and int(tail_limit.group(1)) > 1000:
        body = re.sub(
            r"\bLIMIT\s+\d+\s*$",
            "LIMIT 1000",
            body,
            flags=re.I,
        )
    return GuardResult(True, body)


def _columns_with_types(columns: list[str], rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for column in columns:
        sample = next((row.get(column) for row in rows if row.get(column) is not None), None)
        result.append({"name": column, "type": "number" if isinstance(sample, (int, float)) else "string"})
    return result


def _rows_as_arrays(rows: list[dict[str, Any]], columns: list[str]) -> list[list[Any]]:
    """按 columns 顺序把字典行转成数组行，保持 QueryAnswer.rows 契约稳定。"""
    return [[row.get(column) for column in columns] for row in rows]


def execute_query(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        # 用户 SQL 的第二道防线：只读作者器仅允许读取 10 张业务表
        conn = connect(
            read_only=True,
            enforce_authorizer=True,
            allowed_tables=frozenset(BUSINESS_TABLES),
        )
    except sqlite3.Error as exc:
        raise AppError(503, "DB_UNAVAILABLE", "数据库不可用", {"error": str(exc)}) from exc
    try:
        cursor = conn.execute(sql, params)
        columns = [item[0] for item in (cursor.description or [])]
        raw_rows = cursor.fetchmany(1001)
        truncated = len(raw_rows) > 1000
        rows = [dict(row) for row in raw_rows[:1000]]
        return {
            "columns": columns,
            "column_types": _columns_with_types(columns, rows),
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.DatabaseError as exc:
        message = str(exc)
        if "prohibited" in message or "not authorized" in message:
            raise AppError(422, "SQL_REJECTED", "SQL 被只读守卫拒绝", {"error": message}) from exc
        raise AppError(500, "QUERY_EXECUTION_FAILED", "SQL 执行失败", {"error": message}) from exc
    finally:
        conn.close()


def _top_n(question: str, default: int) -> int:
    match = re.search(r"(?:top|前)\s*(\d{1,3})", question, re.I)
    if match:
        return max(1, min(int(match.group(1)), 200))
    return default


def _budget_account_type(question: str) -> str | None:
    normalized = _normalize(question)
    if any(word in normalized for word in ("收入", "营收", "销售额")):
        return "revenue"
    if "成本" in normalized:
        return "cogs"
    if "费用" in normalized:
        return "expense"
    return None


def build_sql(intent: str, slots: PeriodSlots, limit: int, question: str = "") -> dict[str, Any]:
    """为意图生成参数化 SQL 模板。"""
    safe_limit = max(1, min(int(limit or 20), 200))
    params: dict[str, Any] = {
        "start_period": slots.start_code,
        "end_period": slots.end_code,
        "comparison_start": slots.comparison_start or slots.start_code,
        "comparison_end": slots.comparison_end or slots.end_code,
        "limit": safe_limit,
    }
    chart_type = "none"
    metric_codes: list[str] = []
    explanation = ""

    if intent == "revenue_lookup":
        sql = """SELECT
    ROUND(COALESCE(SUM(CASE WHEN p.period_code BETWEEN :start_period AND :end_period
        THEN b.credit_amount - b.debit_amount ELSE 0 END), 0), 2) AS current_value,
    ROUND(COALESCE(SUM(CASE WHEN p.period_code BETWEEN :comparison_start AND :comparison_end
        THEN b.credit_amount - b.debit_amount ELSE 0 END), 0), 2) AS comparison_value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'revenue'
  AND (p.period_code BETWEEN :start_period AND :end_period
       OR p.period_code BETWEEN :comparison_start AND :comparison_end)"""
        metric_codes = ["revenue_total"]
        explanation = "取收入类科目本期贷方发生额减借方发生额，并按比较期汇总同比/环比。"
    elif intent == "cost_lookup":
        sql = """SELECT p.period_code AS period,
       ROUND(SUM(b.debit_amount - b.credit_amount), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'cogs'
  AND p.period_code BETWEEN :start_period AND :end_period
GROUP BY p.period_code
ORDER BY p.period_code
LIMIT :limit"""
        metric_codes = ["cogs_total"]
        explanation = "取成本类科目本期借方发生额减贷方发生额，按期间汇总。"
    elif intent == "gross_margin":
        sql = """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
        ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT revenue, cogs,
       ROUND(revenue - cogs, 2) AS gross_profit,
       ROUND((revenue - cogs) / NULLIF(revenue, 0), 4) AS gross_margin
FROM agg"""
        metric_codes = ["gross_profit", "gross_margin"]
        explanation = "营业收入减营业成本得到毛利，再除以营业收入得到毛利率。"
    elif intent == "period_expense":
        sql = """SELECT a.account_name AS name,
       ROUND(SUM(b.debit_amount - b.credit_amount), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'expense'
  AND p.period_code BETWEEN :start_period AND :end_period
GROUP BY a.account_name
ORDER BY value DESC
LIMIT :limit"""
        chart_type = "pie"
        metric_codes = ["period_expense"]
        explanation = "按销售、管理、研发、财务费用科目汇总本期费用发生额。"
    elif intent == "operating_profit":
        sql = """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
        ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs,
        ROUND(SUM(CASE WHEN a.account_type = 'tax_surcharge' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS tax_surcharge,
        ROUND(SUM(CASE WHEN a.account_type = 'expense' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS period_expense
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT revenue, cogs, tax_surcharge, period_expense,
       ROUND(revenue - cogs - tax_surcharge - period_expense, 2) AS operating_profit
FROM agg"""
        metric_codes = ["operating_profit"]
        explanation = "营业利润 = 营业收入 - 营业成本 - 税金及附加 - 期间费用。"
    elif intent == "net_profit":
        sql = """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
        ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs,
        ROUND(SUM(CASE WHEN a.account_type = 'tax_surcharge' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS tax_surcharge,
        ROUND(SUM(CASE WHEN a.account_type = 'expense' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS period_expense,
        ROUND(SUM(CASE WHEN a.account_code = '6301' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS nonop_income,
        ROUND(SUM(CASE WHEN a.account_code = '6711' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS nonop_expense,
        ROUND(SUM(CASE WHEN a.account_code = '6801' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS income_tax
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT revenue, cogs, tax_surcharge, period_expense, nonop_income, nonop_expense, income_tax,
       ROUND(revenue - cogs - tax_surcharge - period_expense + nonop_income - nonop_expense, 2) AS total_profit,
       ROUND(revenue - cogs - tax_surcharge - period_expense + nonop_income - nonop_expense - income_tax, 2) AS net_profit
FROM agg"""
        metric_codes = ["total_profit", "net_profit"]
        explanation = "利润总额 = 营业利润 + 营业外收入 - 营业外支出；净利润 = 利润总额 - 所得税。"
    elif intent == "balance_overview":
        sql = """WITH bal AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'asset' THEN b.closing_balance ELSE 0 END), 2) AS total_assets,
        ROUND(SUM(CASE WHEN a.account_type = 'liability' THEN b.closing_balance ELSE 0 END), 2) AS total_liabilities,
        ROUND(SUM(CASE WHEN a.account_type = 'equity' THEN b.closing_balance ELSE 0 END), 2) AS total_equity,
        ROUND(SUM(CASE WHEN a.account_type = 'asset' AND a.is_current = 1 THEN b.closing_balance ELSE 0 END), 2) AS current_assets,
        ROUND(SUM(CASE WHEN a.account_type = 'liability' AND a.is_current = 1 THEN b.closing_balance ELSE 0 END), 2) AS current_liabilities
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE p.period_code = :end_period
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT total_assets, total_liabilities, total_equity, current_assets, current_liabilities,
       ROUND(total_liabilities / NULLIF(total_assets, 0), 4) AS debt_asset_ratio,
       ROUND(current_assets / NULLIF(current_liabilities, 0), 4) AS current_ratio
FROM bal"""
        chart_type = "bar"
        metric_codes = ["total_assets", "total_liabilities", "total_equity", "debt_asset_ratio", "current_ratio"]
        explanation = "取资产、负债、权益类科目期末余额，并计算资产负债率与流动比率。"
    elif intent == "top_customers":
        params["limit"] = _top_n(question, safe_limit)
        sql = """SELECT c.customer_name AS customer_name,
       ROUND(SUM(i.total_amount), 2) AS amount
FROM fact_invoice i
JOIN dim_customer c ON c.customer_id = i.customer_id
JOIN dim_period p ON p.period_id = i.period_id
WHERE i.invoice_type = 'sales'
  AND p.period_code BETWEEN :start_period AND :end_period
GROUP BY c.customer_id, c.customer_name
ORDER BY amount DESC
LIMIT :limit"""
        chart_type = "bar"
        metric_codes = ["revenue_total"]
        explanation = "按销售发票价税合计汇总客户销售额并降序取 Top N。"
    elif intent == "budget_variance":
        params["account_type"] = _budget_account_type(question)
        sql = """WITH actual AS (
    SELECT a.account_code AS account_code, a.account_name AS account_name,
           ROUND(SUM(CASE WHEN a.balance_direction = 'credit'
                          THEN b.credit_amount - b.debit_amount
                          ELSE b.debit_amount - b.credit_amount END), 2) AS actual_amount
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
      AND (:account_type IS NULL OR a.account_type = :account_type)
      AND p.period_code BETWEEN :start_period AND :end_period
    GROUP BY a.account_code, a.account_name
), budget AS (
    SELECT a.account_code AS account_code, a.account_name AS account_name,
           ROUND(SUM(bu.amount), 2) AS budget_amount
    FROM fact_budget bu
    JOIN dim_account a ON a.account_id = bu.account_id
    JOIN dim_period p ON p.period_id = bu.period_id
    WHERE (:account_type IS NULL OR a.account_type = :account_type)
      AND p.period_code BETWEEN :start_period AND :end_period
    GROUP BY a.account_code, a.account_name
)
SELECT b.account_name AS name, b.budget_amount AS budget_amount,
       COALESCE(a.actual_amount, 0) AS actual_amount,
       ROUND(COALESCE(a.actual_amount, 0) - b.budget_amount, 2) AS variance,
       ROUND(COALESCE(a.actual_amount, 0) / NULLIF(b.budget_amount, 0), 4) AS achievement_rate
FROM budget b
LEFT JOIN actual a ON a.account_code = b.account_code
ORDER BY ABS(COALESCE(a.actual_amount, 0) - b.budget_amount) DESC
LIMIT :limit"""
        chart_type = "bar"
        metric_codes = ["budget_amount", "budget_variance"]
        explanation = "实际发生额与预算金额按科目对比，计算差异与达成率。"
    elif intent == "receivable_payable":
        sql = """SELECT
    ROUND(SUM(CASE WHEN a.account_code = '1122' THEN b.closing_balance ELSE 0 END), 2) AS receivable,
    ROUND(SUM(CASE WHEN a.account_code = '2202' THEN b.closing_balance ELSE 0 END), 2) AS payable
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""
        chart_type = "bar"
        metric_codes = ["receivable_total"]
        explanation = "取应收账款（1122）与应付账款（2202）期末余额。"
    elif intent == "customer_detail":
        sql = """SELECT c.customer_code AS customer_code, c.customer_name AS customer_name,
       c.region AS region, c.industry AS industry, c.phone AS phone,
       c.bank_account AS bank_account, c.credit_terms AS credit_terms
FROM dim_customer c
JOIN dim_period p ON p.period_code = :end_period
WHERE :start_period <= :end_period
ORDER BY c.customer_id
LIMIT :limit"""
        metric_codes = ["customer"]
        explanation = "返回客户维度明细；手机号与银行账号由后端按角色统一脱敏。"
    elif intent == "vendor_detail":
        sql = """SELECT v.vendor_code AS vendor_code, v.vendor_name AS vendor_name,
       v.category AS category, v.phone AS phone, v.bank_account AS bank_account,
       v.payment_terms AS payment_terms
FROM dim_vendor v
JOIN dim_period p ON p.period_code = :end_period
WHERE :start_period <= :end_period
ORDER BY v.vendor_id
LIMIT :limit"""
        metric_codes = ["vendor"]
        explanation = "返回供应商维度明细；手机号与银行账号由后端按角色统一脱敏。"
    elif intent == "trend":
        sql = """SELECT p.period_code AS period,
       ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
       ROUND(SUM(CASE WHEN a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
                      THEN CASE WHEN a.balance_direction = 'credit' THEN b.credit_amount - b.debit_amount
                                ELSE b.debit_amount - b.credit_amount END
                      ELSE 0 END), 2) AS net_profit
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
  AND p.period_code BETWEEN :start_period AND :end_period
GROUP BY p.period_code
ORDER BY p.period_code
LIMIT :limit"""
        chart_type = "line"
        metric_codes = ["revenue_total", "net_profit"]
        explanation = "按期间汇总营业收入与净利润，生成趋势序列。"
    else:
        raise AppError(422, "VALIDATION_FAILED", f"不支持的意图: {intent}")

    return {
        "sql": sql,
        "params": params,
        "chart_type": chart_type,
        "metric_codes": metric_codes,
        "explanation": explanation,
    }


def _chart(kind: str, labels: list[str], series: list[tuple[str, list[float]]]) -> dict[str, Any]:
    if kind == "none" or not labels or not series:
        return {"type": "none", "spec": {"labels": [], "series": []}}
    return {
        "type": kind,
        "spec": {
            "labels": labels,
            "series": [{"name": name, "data": [float(value) for value in data]} for name, data in series],
        },
    }


def _comparison_text(current: float, comparison: float, label: str) -> str:
    if comparison is None or float(comparison) == 0:
        return f"；{label}比较期无数据，无法计算变化率。"
    change = (float(current) - float(comparison)) / abs(float(comparison))
    direction = "增长" if change >= 0 else "下降"
    return f"，{label}{direction} {abs(change) * 100:.1f}%。"


def _resolution_provenance(resolution: Any | None) -> dict[str, Any]:
    if resolution is None or getattr(resolution, "kind", None) != "matched":
        return {
            "metric_definition_code": None,
            "metric_version": None,
            "alias_matched": None,
            "alias_type": None,
        }
    return {
        "metric_definition_code": resolution.metric_code,
        "metric_version": resolution.metric_version,
        "alias_matched": resolution.alias,
        "alias_type": resolution.alias_type,
    }


def _apply_resolution_provenance(answer: dict[str, Any], resolution: Any | None) -> dict[str, Any]:
    provenance = answer.setdefault("provenance", {})
    provenance.update(_resolution_provenance(resolution))
    if (
        resolution is not None
        and getattr(resolution, "kind", None) == "matched"
        and not provenance.get("metric_codes")
    ):
        provenance["metric_codes"] = [resolution.metric_code]
    return answer


def build_answer(
    intent: str,
    result: dict[str, Any],
    slots: PeriodSlots,
    role: str,
    *,
    metric_codes: list[str],
    chart_type: str,
    explanation: str,
    masked_columns: list[str],
    warnings: list[str],
    elapsed_ms: int,
    engine: str = "rule",
    resolution: Any | None = None,
) -> dict[str, Any]:
    rows = result["rows"]
    row0 = rows[0] if rows else {}
    chart = _chart("none", [], [])
    answer = "未查询到符合条件的数据。"

    if intent == "revenue_lookup" and row0:
        current = row0.get("current_value") or 0.0
        comparison = row0.get("comparison_value")
        answer = f"{slots.label}营业收入为 {format_amount(current)}"
        if slots.comparison_label:
            answer += _comparison_text(current, comparison, slots.comparison_label)
        else:
            answer += "。"
    elif intent == "cost_lookup" and rows:
        if len(rows) == 1:
            answer = f"{slots.label}营业成本为 {format_amount(rows[0]['value'])}。"
        else:
            total = sum(float(row["value"] or 0) for row in rows)
            peak = max(rows, key=lambda row: float(row["value"] or 0))
            answer = (
                f"{slots.label}营业成本合计 {format_amount(total)}；"
                f"{peak['period']} 最高，为 {format_amount(peak['value'])}。"
            )
            chart = _chart(
                "bar",
                [str(row["period"]) for row in rows],
                [("营业成本", [float(row["value"] or 0) for row in rows])],
            )
    elif intent == "gross_margin" and row0:
        answer = (
            f"{slots.label}营业收入 {format_amount(row0.get('revenue'))}，"
            f"营业成本 {format_amount(row0.get('cogs'))}，"
            f"毛利 {format_amount(row0.get('gross_profit'))}，"
            f"毛利率 {format_pct(row0.get('gross_margin'))}。"
        )
    elif intent == "period_expense" and rows:
        total = sum(float(row["value"] or 0) for row in rows)
        top = rows[0]
        share = float(top["value"] or 0) / total if total else 0
        answer = (
            f"{slots.label}期间费用合计 {format_amount(total)}；"
            f"{top['name']} 占比最高（{format_amount(top['value'])}，{format_pct(share)}）。"
        )
        chart = _chart(
            "pie",
            [str(row["name"]) for row in rows],
            [("费用金额", [float(row["value"] or 0) for row in rows])],
        )
    elif intent == "operating_profit" and row0:
        answer = (
            f"{slots.label}营业利润为 {format_amount(row0.get('operating_profit'))}；"
            f"营业收入 {format_amount(row0.get('revenue'))}，"
            f"营业成本 {format_amount(row0.get('cogs'))}，"
            f"期间费用 {format_amount(row0.get('period_expense'))}。"
        )
    elif intent == "net_profit" and row0:
        answer = (
            f"{slots.label}利润总额 {format_amount(row0.get('total_profit'))}，"
            f"净利润 {format_amount(row0.get('net_profit'))}，"
            f"所得税 {format_amount(row0.get('income_tax'))}。"
        )
    elif intent == "balance_overview" and row0:
        answer = (
            f"{slots.label}资产总额 {format_amount(row0.get('total_assets'))}，"
            f"负债总额 {format_amount(row0.get('total_liabilities'))}，"
            f"所有者权益 {format_amount(row0.get('total_equity'))}，"
            f"资产负债率 {format_pct(row0.get('debt_asset_ratio'))}，"
            f"流动比率 {format_ratio(row0.get('current_ratio'))}。"
        )
        chart = _chart(
            "bar",
            ["资产总额", "负债总额", "所有者权益"],
            [(
                "金额",
                [
                    float(row0.get("total_assets") or 0),
                    float(row0.get("total_liabilities") or 0),
                    float(row0.get("total_equity") or 0),
                ],
            )],
        )
    elif intent == "top_customers" and rows:
        top = rows[0]
        answer = (
            f"{slots.label}按客户销售额看，{top['customer_name']} 最高"
            f"（{format_amount(top['amount'])}），共 {len(rows)} 家客户。"
        )
        chart = _chart(
            "bar",
            [str(row["customer_name"]) for row in rows],
            [("销售额", [float(row["amount"] or 0) for row in rows])],
        )
    elif intent == "budget_variance" and rows:
        top = rows[0]
        answer = (
            f"{slots.label}预算差异最大的是 {top['name']}，"
            f"差异 {format_amount(top.get('variance'))}，"
            f"达成率 {format_pct(top.get('achievement_rate'))}。"
        )
        chart = _chart(
            "bar",
            [str(row["name"]) for row in rows],
            [("预算差异", [float(row["variance"] or 0) for row in rows])],
        )
    elif intent == "receivable_payable" and row0:
        answer = (
            f"{slots.label}应收账款余额 {format_amount(row0.get('receivable'))}，"
            f"应付账款余额 {format_amount(row0.get('payable'))}。"
        )
        chart = _chart(
            "bar",
            ["应收账款", "应付账款"],
            [("余额", [float(row0.get("receivable") or 0), float(row0.get("payable") or 0)])],
        )
    elif intent == "customer_detail":
        answer = f"共查询到 {len(rows)} 位客户。"
        if masked_columns:
            answer += f"手机号与银行账号已按 {role} 角色脱敏。"
    elif intent == "vendor_detail":
        answer = f"共查询到 {len(rows)} 家供应商。"
        if masked_columns:
            answer += f"银行账号已按 {role} 角色脱敏。"
    elif intent == "trend" and rows:
        first, last = rows[0], rows[-1]
        answer = (
            f"{slots.label}收入趋势：{first['period']} 至 {last['period']}，"
            f"营业收入从 {format_amount(first.get('revenue'))} 变为 {format_amount(last.get('revenue'))}。"
        )
        chart = _chart(
            "line",
            [str(row["period"]) for row in rows],
            [
                ("营业收入", [float(row["revenue"] or 0) for row in rows]),
                ("净利润", [float(row["net_profit"] or 0) for row in rows]),
            ],
        )

    assumptions = ["默认合并全部主体", "金额单位为人民币元；比率以小数返回"]
    if slots.assumption:
        assumptions.insert(0, slots.assumption)

    return {
        "answerable": True,
        "answer": answer,
        "intent": intent,
        "engine": engine,
        "sql": result.get("sql"),
        "columns": result["column_types"],
        "rows": _rows_as_arrays(rows, result["columns"]),
        "row_count": result["row_count"],
        "chart": chart,
        "explanation": explanation,
        "assumptions": assumptions,
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": slots.comparison_end if slots.comparison_label else None,
            "metric_codes": metric_codes,
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
            **_resolution_provenance(resolution),
        },
        "masked_columns": masked_columns,
        "warnings": warnings,
        "suggestions": [],
    }


def _definition_answer(
    question: str,
    slots: PeriodSlots,
    role: str,
    elapsed_ms: int,
    resolution: Any | None = None,
) -> dict[str, Any]:
    from app.services import metrics

    metric = None
    if resolution is not None and getattr(resolution, "kind", None) == "matched":
        metric = metrics.get_certified_metric(resolution.metric_code or "")
    if metric is None:
        metric = metrics.find_certified_metric_by_alias(question)
    if metric:
        answer = f"{metric['name']}：{metric['definition']}"
        metric_codes = [metric["code"]]
        metric_card = {
            "code": metric["code"],
            "name": metric["name"],
            "definition": metric["definition"],
            "formula_text": metric["formula_text"],
            "boundary": metric["boundary"],
            "owner": metric["owner"],
            "unit": metric["unit"],
            "version": metric.get("version"),
            "certified_by": metric.get("certified_by"),
            "certified_at": metric.get("certified_at"),
        }
        provenance_extra = {
            "metric_definition_code": metric["code"],
            "metric_version": metric.get("version"),
            "alias_matched": getattr(resolution, "alias", None),
            "alias_type": getattr(resolution, "alias_type", None),
        }
        explanation = "命中认证指标定义卡片，展示版本化口径，不查询业务数据。"
    else:
        metric_card = None
        term = _find_term_by_alias(question)
        if term:
            answer = f"{term['display_name']}：{term['description']}"
            metric_codes = [term["term_code"]]
        else:
            answer = "未在本体术语库中找到该定义，请先在本体页生成或校准术语。"
            metric_codes = []
        provenance_extra = {
            "metric_definition_code": None,
            "metric_version": None,
            "alias_matched": None,
            "alias_type": None,
        }
        explanation = "命中本体术语定义，不查询业务表。"
    response = {
        "answerable": True,
        "answer": answer,
        "intent": "definition",
        "engine": "rule",
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": {"type": "none", "spec": {"labels": [], "series": []}},
        "explanation": explanation,
        "assumptions": ["定义来自本体术语/指标目录，未查询业务数据"],
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": None,
            "metric_codes": metric_codes,
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
            **provenance_extra,
        },
        "masked_columns": [],
        "warnings": [],
        "suggestions": [],
    }
    if metric_card:
        response["metric_card"] = metric_card
    return response


def _metric_lookup_answer(
    question: str,
    slots: PeriodSlots,
    role: str,
    resolution: Any,
    elapsed_ms: int,
) -> dict[str, Any]:
    from app.services import metrics

    code = str(getattr(resolution, "metric_code", "") or "")
    execution = metrics.execute_metric(
        code,
        slots.end_code,
        start_period=slots.start_code,
    )
    value = float(execution["value"])
    unit = str(execution.get("unit") or "")
    if unit == "%":
        value_text = format_pct(value)
    elif unit == "ratio":
        value_text = format_ratio(value)
    elif unit in ("CNY", "元"):
        value_text = format_amount(value)
    elif unit in ("家", "个", "笔", "天", "次"):
        value_text = f"{int(round(value))} {unit}"
    else:
        value_text = f"{value:,.2f}" + (f" {unit}" if unit else "")
    answer = (
        f"{slots.label}{execution['name']}为 {value_text}"
        f"（指标 {code} {execution['version'] or 'draft'}）。"
    )
    metric_card = {
        "code": code,
        "name": execution["name"],
        "definition": execution["definition"],
        "formula_text": execution["formula_text"],
        "boundary": execution["boundary"],
        "unit": unit,
        "version": execution["version"],
        "certified_by": execution.get("certified_by"),
        "certified_at": execution.get("certified_at"),
    }
    return {
        "answerable": True,
        "answer": answer,
        "intent": "metric_lookup",
        "engine": "rule",
        "sql": execution["sql"],
        "columns": [
            {"name": "value", "type": "number"},
            {"name": "unit", "type": "string"},
        ],
        "rows": [[value, unit]],
        "row_count": 1,
        "chart": {"type": "none", "spec": {"labels": [], "series": []}},
        "explanation": (
            f"命中认证指标 {code}，按指标版本快照公式执行只读查询，"
            "不经过普通意图 SQL 模板。"
        ),
        "assumptions": ["指标公式来自认证版本快照", "金额单位为人民币元；比率以小数返回"],
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": None,
            "metric_codes": [code],
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
            "metric_definition_code": code,
            "metric_version": execution["version"],
            "alias_matched": getattr(resolution, "alias", None),
            "alias_type": getattr(resolution, "alias_type", None),
        },
        "metric_card": metric_card,
        "masked_columns": [],
        "warnings": [],
        "suggestions": [],
    }


def _ambiguous_answer(
    question: str,
    slots: PeriodSlots,
    resolution: Any,
    elapsed_ms: int,
) -> dict[str, Any]:
    candidates = list(getattr(resolution, "candidates", []) or [])
    names = "、".join(
        f"{item['metric_name']}（{item['metric_code']}）" for item in candidates[:5]
    )
    answer = f"该问法可能对应多个指标：{names}。请补充指标名称或编码。"
    return {
        "answerable": False,
        "answer": answer,
        "intent": "metric_ambiguous",
        "engine": "rule",
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": {"type": "none", "spec": {"labels": [], "series": []}},
        "explanation": "别名命中多个指标，按不猜测原则返回候选。",
        "assumptions": ["候选按别名长度、优先级、指标编码稳定排序"],
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": None,
            "metric_codes": [],
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
            "metric_definition_code": None,
            "metric_version": None,
            "alias_matched": getattr(resolution, "alias", None),
            "alias_type": getattr(resolution, "alias_type", None),
        },
        "candidates": candidates,
        "masked_columns": [],
        "warnings": [],
        "suggestions": [f"{item['metric_code']} 是什么" for item in candidates[:3]],
    }


def _exclusion_answer(
    question: str,
    slots: PeriodSlots,
    resolution: Any,
    elapsed_ms: int,
) -> dict[str, Any]:
    alias = getattr(resolution, "alias", None) or ""
    metric_name = getattr(resolution, "metric_name", None) or ""
    answer = (
        f"「{alias}」属于需确认的排除口径，不直接返回数值。"
        f"如需按 {metric_name} 口径查询，请确认是否为不含税/不含该口径的金额。"
    )
    return {
        "answerable": False,
        "answer": answer,
        "intent": "metric_exclusion",
        "engine": "rule",
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": {"type": "none", "spec": {"labels": [], "series": []}},
        "explanation": "命中排除别名，按不猜测原则要求用户确认口径。",
        "assumptions": ["排除项不会自动映射为指标数值"],
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": None,
            "metric_codes": [],
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
            "metric_definition_code": None,
            "metric_version": None,
            "alias_matched": alias,
            "alias_type": getattr(resolution, "alias_type", None),
        },
        "candidates": list(getattr(resolution, "candidates", []) or []),
        "masked_columns": [],
        "warnings": [],
        "suggestions": [f"{getattr(resolution, 'metric_code', '')} 是什么"],
    }


def _unsupported_answer(question: str, slots: PeriodSlots, elapsed_ms: int) -> dict[str, Any]:
    return {
        "answerable": False,
        "answer": "暂未理解该问题。可以尝试询问收入、成本、毛利率、费用、利润、资产负债、预算、客户/供应商明细或趋势。",
        "intent": "unsupported",
        "engine": "rule",
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": {"type": "none", "spec": {"labels": [], "series": []}},
        "explanation": "规则引擎未命中 14 类意图，未生成 SQL。",
        "assumptions": [],
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": None,
            "metric_codes": [],
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
            "metric_definition_code": None,
            "metric_version": None,
            "alias_matched": None,
            "alias_type": None,
        },
        "masked_columns": [],
        "warnings": [],
        "suggestions": SUGGESTIONS,
    }


def _llm_prompt(question: str, slots: PeriodSlots) -> tuple[str, str]:
    metrics = _ontology_terms()
    metric_lines = [
        f"- {term['display_name']}（{term['term_code']}）：{term['description']}"
        for term in metrics
        if term["category"] == "metric"
    ]
    system = (
        "你是企业财务 NL2SQL 助手。只返回 JSON，不要输出 Markdown。\n"
        'JSON 结构：{"sql": "...", "explanation": "...", "chart_type": "none|line|bar|pie"}\n'
        "可用表：dim_company, dim_account, dim_customer, dim_vendor, dim_period, "
        "fact_journal, fact_invoice, fact_payment, fact_balance, fact_budget。\n"
        "约束：只允许单条 SELECT；禁止 SELECT *；禁止 DDL/DML；禁止访问 sqlite_ 对象；"
        "禁止查询 phone、bank_account 等敏感列；"
        "必须使用命名参数 :start_period、:end_period（可选 :comparison_start、:comparison_end、:limit）；"
        "不得把问题文本拼接进 SQL；LIMIT 不超过 200。\n"
        "金额取 fact_balance 的发生额/期末余额；收入类 credit-debit，成本费用类 debit-credit。\n"
        "指标目录：\n" + "\n".join(metric_lines)
    )
    user = (
        f"问题：{question}\n"
        f"当前期间：{slots.start_code} 至 {slots.end_code}\n"
        f"比较期间：{slots.comparison_start or slots.start_code} 至 {slots.comparison_end or slots.end_code}\n"
        "请生成 SQL、中文解释与合适的图表类型。"
    )
    return system, user


def _try_llm(question: str, slots: PeriodSlots, limit: int) -> tuple[dict[str, Any] | None, str | None]:
    if not settings.llm_enabled:
        return None, "未配置 LLM"
    try:
        system, user = _llm_prompt(question, slots)
        response = httpx.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.openai_model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=settings.llm_timeout_seconds,
        )
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        payload = response.json()
        content = payload["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        data = json.loads(content)
        sql = data.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return None, "LLM 未返回 SQL"
        guarded = guard_sql(sql, allow_sensitive=False)
        if not guarded.ok:
            return None, f"LLM SQL 未通过守卫：{guarded.reason}"
        chart_type = data.get("chart_type")
        if chart_type not in ("none", "line", "bar", "pie"):
            chart_type = "none"
        return {
            "sql": guarded.sql,
            "explanation": str(data.get("explanation") or ""),
            "chart_type": chart_type,
            "limit": limit,
        }, None
    except Exception as exc:  # noqa: BLE001 - 任何 LLM 失败都必须回退规则
        return None, str(exc)


def _llm_answer(
    intent: str,
    slots: PeriodSlots,
    role: str,
    result: dict[str, Any],
    masked_columns: list[str],
    llm: dict[str, Any],
    warnings: list[str],
    elapsed_ms: int,
) -> dict[str, Any]:
    rows = result["rows"]
    columns = result["columns"]
    chart = {"type": "none", "spec": {"labels": [], "series": []}}
    chart_type = llm["chart_type"]
    if chart_type != "none" and rows and columns:
        label_key = columns[0]
        numeric = [
            col for col in columns[1:]
            if any(isinstance(row.get(col), (int, float)) for row in rows)
        ]
        if numeric:
            labels = [str(row.get(label_key, "")) for row in rows]
            series = [(col, [float(row.get(col) or 0) for row in rows]) for col in numeric[:3]]
            chart = _chart(chart_type, labels, series)
    answer = llm.get("explanation") or f"已生成查询，返回 {len(rows)} 行结果。"
    return {
        "answerable": True,
        "answer": answer,
        "intent": intent,
        "engine": "llm",
        "sql": llm["sql"],
        "columns": result["column_types"],
        "rows": _rows_as_arrays(rows, result["columns"]),
        "row_count": result["row_count"],
        "chart": chart,
        "explanation": llm.get("explanation") or "由可选 LLM 生成 SQL，并经过只读守卫与参数化执行。",
        "assumptions": ["默认合并全部主体", "LLM 仅生成 SQL，不接触业务行数据"],
        "provenance": {
            "period_code": slots.end_code,
            "comparison_period": slots.comparison_end if slots.comparison_label else None,
            "metric_codes": [],
            "ontology_version": ONTOLOGY_VERSION,
            "elapsed_ms": elapsed_ms,
        },
        "masked_columns": masked_columns,
        "warnings": warnings,
        "suggestions": [],
    }


def answer_question(question: str, role: str, limit: int = 20) -> dict[str, Any]:
    started = time.perf_counter()
    question = (question or "").strip()
    if not question:
        raise AppError(422, "VALIDATION_FAILED", "question 不能为空")
    latest = _latest_period()
    if not latest:
        raise AppError(503, "DB_UNAVAILABLE", "数据库中没有会计期间，请先播种")
    slots = parse_period(question, latest)
    intent, resolution = detect_intent_with_resolution(question)
    metric_audit: dict[str, Any] = {}
    if resolution is not None and getattr(resolution, "kind", None) == "matched":
        metric_audit = {
            "metric_code": getattr(resolution, "metric_code", None),
            "metric_version": getattr(resolution, "metric_version", None),
            "alias_matched": getattr(resolution, "alias", None),
        }
    if intent == "top_customers":
        limit = _top_n(question, limit)

    if intent == "definition":
        elapsed = int((time.perf_counter() - started) * 1000)
        answer = _definition_answer(question, slots, role, elapsed, resolution)
        record_audit(
            question=question,
            intent=intent,
            sql_text=None,
            row_count=0,
            status="succeeded",
            role=role,
            masked_columns=[],
            duration_ms=elapsed,
            **metric_audit,
        )
        return answer
    if intent in ("metric_ambiguous", "metric_exclusion"):
        elapsed = int((time.perf_counter() - started) * 1000)
        if intent == "metric_ambiguous":
            answer = _ambiguous_answer(question, slots, resolution, elapsed)
        else:
            answer = _exclusion_answer(question, slots, resolution, elapsed)
        record_audit(
            question=question,
            intent=intent,
            sql_text=None,
            row_count=0,
            status="unanswered",
            role=role,
            masked_columns=[],
            duration_ms=elapsed,
            metric_code=None,
            metric_version=None,
            alias_matched=getattr(resolution, "alias", None),
        )
        return answer
    if intent == "unsupported":
        elapsed = int((time.perf_counter() - started) * 1000)
        answer = _unsupported_answer(question, slots, elapsed)
        record_audit(
            question=question,
            intent=intent,
            sql_text=None,
            row_count=0,
            status="unanswered",
            role=role,
            masked_columns=[],
            duration_ms=elapsed,
        )
        return answer
    if intent == "metric_lookup":
        try:
            elapsed = int((time.perf_counter() - started) * 1000)
            answer = _metric_lookup_answer(question, slots, role, resolution, elapsed)
            record_audit(
                question=question,
                intent=intent,
                sql_text=answer.get("sql"),
                row_count=answer["row_count"],
                status="succeeded",
                role=role,
                masked_columns=[],
                duration_ms=elapsed,
                **metric_audit,
            )
            return answer
        except AppError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            record_audit(
                question=question,
                intent=intent,
                sql_text=None,
                row_count=0,
                status="failed",
                role=role,
                masked_columns=[],
                duration_ms=elapsed,
                error_message=exc.message,
                **metric_audit,
            )
            raise

    safe_limit = max(1, min(int(limit or 20), 200))
    params = {
        "start_period": slots.start_code,
        "end_period": slots.end_code,
        "comparison_start": slots.comparison_start or slots.start_code,
        "comparison_end": slots.comparison_end or slots.end_code,
        "limit": safe_limit,
    }
    warnings: list[str] = []
    sql_for_audit: str | None = None
    masked_columns: list[str] = []
    sensitive_hint: list[str] | None = None
    if intent == "customer_detail":
        sensitive_hint = ["phone", "bank_account"]
    elif intent == "vendor_detail":
        sensitive_hint = ["phone", "bank_account"]

    try:
        if settings.llm_enabled:
            llm, llm_error = _try_llm(question, slots, safe_limit)
            if llm is None:
                warnings.append(f"LLM 增强不可用，已回退规则引擎：{llm_error}")
            else:
                try:
                    result = execute_query(llm["sql"], params)
                    result["sql"] = llm["sql"]
                    # LLM 路径已禁止引用敏感列；输出侧再按敏感列名兜底掩码，防止别名场景
                    masked_rows, masked_columns = mask_rows(
                        result["columns"], result["rows"], role, sorted(SENSITIVE_COLUMN_NAMES)
                    )
                    result["rows"] = masked_rows
                    elapsed = int((time.perf_counter() - started) * 1000)
                    answer = _llm_answer(
                        intent, slots, role, result, masked_columns, llm, warnings, elapsed
                    )
                    _apply_resolution_provenance(answer, resolution)
                    record_audit(
                        question=question,
                        intent=intent,
                        sql_text=llm["sql"],
                        row_count=result["row_count"],
                        status="succeeded",
                        role=role,
                        masked_columns=masked_columns,
                        duration_ms=elapsed,
                        **metric_audit,
                    )
                    return answer
                except AppError as exc:
                    warnings.append(f"LLM SQL 执行失败，已回退规则引擎：{exc.message}")

        built = build_sql(intent, slots, safe_limit, question)
        guarded = guard_sql(built["sql"])
        if not guarded.ok:
            raise AppError(422, "SQL_REJECTED", guarded.reason, {"intent": intent})
        sql_for_audit = guarded.sql
        result = execute_query(guarded.sql, built["params"])
        result["sql"] = guarded.sql
        masked_rows, masked_columns = mask_rows(
            result["columns"], result["rows"], role, sensitive_hint
        )
        result["rows"] = masked_rows
        if result.get("truncated"):
            warnings.append("结果超过 1000 行，已截断。")
        elapsed = int((time.perf_counter() - started) * 1000)
        answer = build_answer(
            intent,
            result,
            slots,
            role,
            metric_codes=built["metric_codes"],
            chart_type=built["chart_type"],
            explanation=built["explanation"],
            masked_columns=masked_columns,
            warnings=warnings,
            elapsed_ms=elapsed,
            resolution=resolution,
        )
        answer["engine"] = "rule"
        record_audit(
            question=question,
            intent=intent,
            sql_text=sql_for_audit,
            row_count=result["row_count"],
            status="succeeded",
            role=role,
            masked_columns=masked_columns,
            duration_ms=elapsed,
            **metric_audit,
        )
        return answer
    except AppError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        record_audit(
            question=question,
            intent=intent,
            sql_text=sql_for_audit,
            row_count=0,
            status="failed",
            role=role,
            masked_columns=masked_columns,
            duration_ms=elapsed,
            error_message=exc.message,
            **metric_audit,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        record_audit(
            question=question,
            intent=intent,
            sql_text=sql_for_audit,
            row_count=0,
            status="failed",
            role=role,
            masked_columns=masked_columns,
            duration_ms=elapsed,
            error_message=str(exc),
            **metric_audit,
        )
        raise AppError(500, "INTERNAL_ERROR", "问数处理失败", {"error": str(exc)}) from exc
