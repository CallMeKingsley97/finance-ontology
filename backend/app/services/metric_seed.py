"""20 个 P0 指标、别名与测试用例的幂等播种。

指标/别名/用例全部落在 staging.db（schema v2），与 ``seed_database(reset=True)``
对 finance.db 的重建互不影响。公式只读复用 ``ontology.SQL_ATTRIBUTES``，
不修改本体内容（27 术语 / 16 关系 / 20 SQL 属性保持不变）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app.services.ingest.store import (
    ensure_staging_available,
    staging_connection,
    staging_now,
)
from app.services.ontology import SQL_ATTRIBUTES

METRIC_SEED_VERSION = "1"
DEFAULT_PERIOD = "2026-10"

_FORMULA_BY_CODE: dict[str, str] = {
    item["code"]: item["formula_sql"] for item in SQL_ATTRIBUTES
}

OPERATING_CASH_FLOW_SQL = """WITH flows AS (
    SELECT
        ROUND(SUM(CASE WHEN p.period_code BETWEEN :start_period AND :end_period
                       AND fp.payment_type = 'receipt' THEN fp.amount ELSE 0 END), 2) AS receipts,
        ROUND(SUM(CASE WHEN p.period_code BETWEEN :start_period AND :end_period
                       AND fp.payment_type = 'payment' THEN fp.amount ELSE 0 END), 2) AS payments
    FROM fact_payment fp
    JOIN dim_period p ON p.period_id = fp.period_id
)
SELECT ROUND(receipts - payments, 2) AS value FROM flows"""

EXPENSE_RATIO_SQL = """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'expense'
                       THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS expense,
        ROUND(SUM(CASE WHEN a.account_type = 'revenue'
                       THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('expense', 'revenue')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(expense / NULLIF(revenue, 0), 4) AS value FROM agg"""

BUDGET_ACHIEVEMENT_RATE_SQL = """WITH actual AS (
    SELECT ROUND(COALESCE(SUM(b.credit_amount - b.debit_amount), 0), 2) AS actual_revenue
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type = 'revenue'
      AND p.period_code BETWEEN :start_period AND :end_period
), budget AS (
    SELECT ROUND(COALESCE(SUM(bu.amount), 0), 2) AS budget_revenue
    FROM fact_budget bu
    JOIN dim_account a ON a.account_id = bu.account_id
    JOIN dim_period p ON p.period_id = bu.period_id
    WHERE a.account_type = 'revenue'
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(actual.actual_revenue / NULLIF(budget.budget_revenue, 0), 4) AS value
FROM actual, budget"""

ACTIVE_CUSTOMER_COUNT_SQL = """SELECT COUNT(DISTINCT i.customer_id) AS value
FROM fact_invoice i
JOIN dim_period p ON p.period_id = i.period_id
WHERE i.invoice_type = 'sales'
  AND p.period_code BETWEEN :start_period AND :end_period"""


def _boundary(note: str, *, tax_basis: str = "不含税", period_basis: str = "月度") -> dict[str, str]:
    return {
        "tax_basis": tax_basis,
        "period_basis": period_basis,
        "entity_scope": "合并主体",
        "currency": "CNY",
        "internal_trade": "不抵消",
        "note": note,
    }


METRIC_SEED: list[dict[str, Any]] = [
    {
        "code": "revenue_total",
        "name": "营业收入",
        "definition": "企业日常经营活动形成的经济利益总流入，取收入类科目本期贷方发生额减借方发生额。",
        "formula_text": "收入类科目本期贷方发生额 − 借方发生额",
        "formula_sql": _FORMULA_BY_CODE["revenue_total"],
        "boundary": _boundary("不含税收入口径；默认合并制造与贸易主体。"),
        "owner": "财务共享中心",
        "business_domain": "收入",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_revenue_total",
        "sort_order": 1,
    },
    {
        "code": "cogs_total",
        "name": "营业成本",
        "definition": "与营业收入对应的营业成本，取成本类科目本期借方发生额减贷方发生额。",
        "formula_text": "成本类科目本期借方发生额 − 贷方发生额",
        "formula_sql": _FORMULA_BY_CODE["cogs_total"],
        "boundary": _boundary("与收入配比的营业成本，不含期间费用。"),
        "owner": "财务共享中心",
        "business_domain": "成本",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_cogs_total",
        "sort_order": 2,
    },
    {
        "code": "gross_profit",
        "name": "毛利",
        "definition": "营业收入减营业成本，反映产品/服务的直接盈利空间。",
        "formula_text": "营业收入 − 营业成本",
        "formula_sql": _FORMULA_BY_CODE["gross_profit"],
        "boundary": _boundary("不含税口径；由收入与成本两个认证指标推导。"),
        "owner": "财务共享中心",
        "business_domain": "利润",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_gross_profit",
        "sort_order": 3,
    },
    {
        "code": "gross_margin",
        "name": "毛利率",
        "definition": "毛利除以营业收入，比率指标，0.25 表示 25%。",
        "formula_text": "毛利 ÷ 营业收入",
        "formula_sql": _FORMULA_BY_CODE["gross_margin"],
        "boundary": _boundary("收入为 0 时返回 0；比率以小数返回。"),
        "owner": "财务共享中心",
        "business_domain": "利润",
        "unit": "%",
        "sensitivity_level": "internal",
        "term_code": "metric_gross_margin",
        "sort_order": 4,
    },
    {
        "code": "period_expense",
        "name": "期间费用",
        "definition": "销售费用、管理费用、研发费用与财务费用之和，取费用类科目本期净发生额。",
        "formula_text": "销售费用 + 管理费用 + 研发费用 + 财务费用",
        "formula_sql": _FORMULA_BY_CODE["period_expense"],
        "boundary": _boundary("费用类科目净发生额，不含税金及附加。"),
        "owner": "财务共享中心",
        "business_domain": "费用",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_period_expense",
        "sort_order": 5,
    },
    {
        "code": "operating_profit",
        "name": "营业利润",
        "definition": "营业收入减营业成本、税金及附加与期间费用后的经营成果。",
        "formula_text": "营业收入 − 营业成本 − 税金及附加 − 期间费用",
        "formula_sql": _FORMULA_BY_CODE["operating_profit"],
        "boundary": _boundary("不含营业外收支与所得税。"),
        "owner": "财务共享中心",
        "business_domain": "利润",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_operating_profit",
        "sort_order": 6,
    },
    {
        "code": "total_profit",
        "name": "利润总额",
        "definition": "营业利润加营业外收入减营业外支出，为所得税前利润。",
        "formula_text": "营业利润 + 营业外收入 − 营业外支出",
        "formula_sql": _FORMULA_BY_CODE["total_profit"],
        "boundary": _boundary("所得税前口径。"),
        "owner": "财务共享中心",
        "business_domain": "利润",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_total_profit",
        "sort_order": 7,
    },
    {
        "code": "net_profit",
        "name": "净利润",
        "definition": "利润总额减所得税费用后的最终经营成果。",
        "formula_text": "利润总额 − 所得税费用",
        "formula_sql": _FORMULA_BY_CODE["net_profit"],
        "boundary": _boundary("税后口径；默认合并主体。"),
        "owner": "财务共享中心",
        "business_domain": "利润",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_net_profit",
        "sort_order": 8,
    },
    {
        "code": "total_assets",
        "name": "资产总额",
        "definition": "全部资产类科目期末余额合计。",
        "formula_text": "资产类科目期末余额合计",
        "formula_sql": _FORMULA_BY_CODE["total_assets"],
        "boundary": _boundary("期末时点口径。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "资产",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_total_assets",
        "sort_order": 9,
    },
    {
        "code": "total_liabilities",
        "name": "负债总额",
        "definition": "全部负债类科目期末余额合计。",
        "formula_text": "负债类科目期末余额合计",
        "formula_sql": _FORMULA_BY_CODE["total_liabilities"],
        "boundary": _boundary("期末时点口径。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "负债",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_total_liabilities",
        "sort_order": 10,
    },
    {
        "code": "total_equity",
        "name": "所有者权益",
        "definition": "全部权益类科目期末余额合计。",
        "formula_text": "权益类科目期末余额合计",
        "formula_sql": _FORMULA_BY_CODE["total_equity"],
        "boundary": _boundary("期末时点口径。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "权益",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_total_equity",
        "sort_order": 11,
    },
    {
        "code": "debt_asset_ratio",
        "name": "资产负债率",
        "definition": "负债总额除以资产总额，比率指标，用于衡量长期偿债能力。",
        "formula_text": "负债总额 ÷ 资产总额",
        "formula_sql": _FORMULA_BY_CODE["debt_asset_ratio"],
        "boundary": _boundary("资产为 0 时返回 0；比率以小数返回。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "负债",
        "unit": "%",
        "sensitivity_level": "internal",
        "term_code": "metric_debt_asset_ratio",
        "sort_order": 12,
    },
    {
        "code": "current_ratio",
        "name": "流动比率",
        "definition": "流动资产除以流动负债，倍数指标。",
        "formula_text": "流动资产 ÷ 流动负债",
        "formula_sql": _FORMULA_BY_CODE["current_ratio"],
        "boundary": _boundary("流动负债为 0 时返回 0。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "偿债",
        "unit": "倍",
        "sensitivity_level": "internal",
        "term_code": "metric_current_ratio",
        "sort_order": 13,
    },
    {
        "code": "budget_variance",
        "name": "预算差异",
        "definition": "实际发生额减预算金额；达成率为实际发生额除以预算金额。",
        "formula_text": "实际发生额 − 预算金额",
        "formula_sql": _FORMULA_BY_CODE["budget_variance"],
        "boundary": _boundary("预算版本 V1；按年度预算按月展开。"),
        "owner": "预算管理部",
        "business_domain": "预算",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": "metric_budget_variance",
        "sort_order": 14,
    },
    {
        "code": "receivable_total",
        "name": "应收账款",
        "definition": "科目 1122 的期末余额合计。",
        "formula_text": "科目 1122 期末余额",
        "formula_sql": _FORMULA_BY_CODE["receivable_total"],
        "boundary": _boundary("期末余额口径，不扣减坏账准备。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "营运",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": None,
        "sort_order": 15,
    },
    {
        "code": "inventory_total",
        "name": "存货",
        "definition": "科目 1405 的期末余额合计。",
        "formula_text": "科目 1405 期末余额",
        "formula_sql": _FORMULA_BY_CODE["inventory_total"],
        "boundary": _boundary("期末余额口径，不扣减跌价准备。", period_basis="月末"),
        "owner": "财务共享中心",
        "business_domain": "营运",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": None,
        "sort_order": 16,
    },
    {
        "code": "operating_cash_flow",
        "name": "经营现金净流入",
        "definition": "期间收款合计减付款合计；为收付款口径，不等同于现金流量表。",
        "formula_text": "期间收款合计 − 期间付款合计",
        "formula_sql": OPERATING_CASH_FLOW_SQL,
        "boundary": _boundary(
            "收付款口径，不含投资/筹资活动，不声称等同现金流量表准则口径。"
        ),
        "owner": "财务共享中心",
        "business_domain": "营运",
        "unit": "CNY",
        "sensitivity_level": "internal",
        "term_code": None,
        "sort_order": 17,
    },
    {
        "code": "expense_ratio",
        "name": "费用率",
        "definition": "期间费用除以营业收入。",
        "formula_text": "期间费用 ÷ 营业收入",
        "formula_sql": EXPENSE_RATIO_SQL,
        "boundary": _boundary("收入为 0 时返回 0；比率以小数返回。"),
        "owner": "财务共享中心",
        "business_domain": "费用",
        "unit": "%",
        "sensitivity_level": "internal",
        "term_code": None,
        "sort_order": 18,
    },
    {
        "code": "budget_achievement_rate",
        "name": "预算达成率",
        "definition": "实际营业收入除以预算营业收入。",
        "formula_text": "实际营业收入 ÷ 预算营业收入",
        "formula_sql": BUDGET_ACHIEVEMENT_RATE_SQL,
        "boundary": _boundary("预算为 0 时返回 0；比率以小数返回。"),
        "owner": "预算管理部",
        "business_domain": "预算",
        "unit": "%",
        "sensitivity_level": "internal",
        "term_code": None,
        "sort_order": 19,
    },
    {
        "code": "active_customer_count",
        "name": "活跃客户数",
        "definition": "期间内有销售发票的去重客户数。",
        "formula_text": "期间内有销售发票的 DISTINCT 客户数",
        "formula_sql": ACTIVE_CUSTOMER_COUNT_SQL,
        "boundary": _boundary("按销售发票口径统计，采购发票与无发票客户不计入。"),
        "owner": "客户运营部",
        "business_domain": "客户",
        "unit": "户",
        "sensitivity_level": "internal",
        "term_code": None,
        "sort_order": 20,
    },
]


def _alias(metric_code: str, alias: str, alias_type: str, priority: int) -> dict[str, Any]:
    return {
        "metric_code": metric_code,
        "alias": alias,
        "alias_type": alias_type,
        "priority": priority,
    }


METRIC_ALIAS_SEED: list[dict[str, Any]] = [
    _alias("revenue_total", "营业收入", "business", 20),
    _alias("revenue_total", "营收", "synonym", 10),
    _alias("revenue_total", "收入", "synonym", 0),
    _alias("revenue_total", "销售收入", "synonym", 0),
    _alias("revenue_total", "营业额", "synonym", 0),
    _alias("revenue_total", "主营业务收入", "synonym", 0),
    _alias("revenue_total", "含税收入", "exclusion", 100),
    _alias("revenue_total", "含税营收", "exclusion", 100),
    _alias("cogs_total", "营业成本", "business", 20),
    _alias("cogs_total", "成本", "synonym", 0),
    _alias("cogs_total", "主营业务成本", "synonym", 0),
    _alias("cogs_total", "销售成本", "synonym", 0),
    _alias("gross_profit", "毛利", "business", 20),
    _alias("gross_profit", "毛利润", "synonym", 0),
    _alias("gross_profit", "销售毛利", "synonym", 0),
    _alias("gross_margin", "毛利率", "business", 20),
    _alias("gross_margin", "毛利水平", "synonym", 0),
    _alias("gross_margin", "销售毛利率", "synonym", 0),
    _alias("period_expense", "期间费用", "business", 20),
    _alias("period_expense", "费用", "synonym", 0),
    _alias("period_expense", "费用总额", "synonym", 0),
    _alias("period_expense", "三项费用", "synonym", 0),
    _alias("operating_profit", "营业利润", "business", 20),
    _alias("operating_profit", "经营利润", "synonym", 0),
    _alias("total_profit", "利润总额", "business", 20),
    _alias("total_profit", "税前利润", "synonym", 0),
    _alias("net_profit", "净利润", "business", 20),
    _alias("net_profit", "净利", "synonym", 0),
    _alias("net_profit", "税后利润", "synonym", 0),
    _alias("net_profit", "盈利", "synonym", 0),
    _alias("total_assets", "资产总额", "business", 20),
    _alias("total_assets", "总资产", "synonym", 10),
    _alias("total_assets", "资产合计", "synonym", 0),
    _alias("total_liabilities", "负债总额", "business", 20),
    _alias("total_liabilities", "总负债", "synonym", 10),
    _alias("total_liabilities", "负债合计", "synonym", 0),
    _alias("total_equity", "所有者权益", "business", 20),
    _alias("total_equity", "股东权益", "synonym", 0),
    _alias("total_equity", "净资产", "synonym", 0),
    _alias("total_equity", "权益合计", "synonym", 0),
    _alias("debt_asset_ratio", "资产负债率", "business", 20),
    _alias("debt_asset_ratio", "负债率", "synonym", 0),
    _alias("debt_asset_ratio", "负债比率", "synonym", 0),
    _alias("current_ratio", "流动比率", "business", 20),
    _alias("current_ratio", "短期偿债能力", "synonym", 0),
    _alias("budget_variance", "预算差异", "business", 20),
    _alias("budget_variance", "预算执行", "synonym", 0),
    _alias("receivable_total", "应收账款", "business", 20),
    _alias("receivable_total", "应收款", "synonym", 0),
    _alias("receivable_total", "应收", "synonym", 0),
    _alias("inventory_total", "存货", "business", 20),
    _alias("inventory_total", "库存", "synonym", 0),
    _alias("operating_cash_flow", "经营现金净流入", "business", 20),
    _alias("operating_cash_flow", "经营现金流", "synonym", 10),
    _alias("operating_cash_flow", "现金净流入", "synonym", 0),
    _alias("expense_ratio", "费用率", "business", 20),
    _alias("expense_ratio", "费用占比", "synonym", 0),
    _alias("budget_achievement_rate", "预算达成率", "business", 20),
    _alias("budget_achievement_rate", "达成率", "synonym", 10),
    _alias("budget_achievement_rate", "预算完成率", "synonym", 0),
    _alias("active_customer_count", "活跃客户数", "business", 20),
    _alias("active_customer_count", "活跃客户", "synonym", 10),
    _alias("active_customer_count", "客户数", "synonym", 0),
]


# 期望值来自固定随机种子（seed_database(reset=True)）下 2026-10 的实际值，
# 开发期一次性核对后固定，禁止在 seed 时重新取实际值覆盖。
METRIC_TEST_CASE_SEED: list[dict[str, Any]] = [
    {"metric_code": "revenue_total", "expected_value": 14513571.53},
    {"metric_code": "cogs_total", "expected_value": 8803533.98},
    {"metric_code": "gross_profit", "expected_value": 5710037.55},
    {"metric_code": "gross_margin", "expected_value": 0.3934},
    {"metric_code": "period_expense", "expected_value": 2993486.13},
    {"metric_code": "operating_profit", "expected_value": 2562269.90},
    {"metric_code": "total_profit", "expected_value": 2559021.75},
    {"metric_code": "net_profit", "expected_value": 2175168.49},
    {"metric_code": "total_assets", "expected_value": 66858121.53},
    {"metric_code": "total_liabilities", "expected_value": 11807909.45},
    {"metric_code": "total_equity", "expected_value": 55050212.08},
    {"metric_code": "debt_asset_ratio", "expected_value": 0.1766},
    {"metric_code": "current_ratio", "expected_value": 8.8715},
    {"metric_code": "budget_variance", "expected_value": 664928.85},
    {"metric_code": "receivable_total", "expected_value": 5079750.03},
    {"metric_code": "inventory_total", "expected_value": 3961590.29},
    {"metric_code": "operating_cash_flow", "expected_value": 2570236.29},
    {"metric_code": "expense_ratio", "expected_value": 0.2063},
    {"metric_code": "budget_achievement_rate", "expected_value": 1.0474},
    {"metric_code": "active_customer_count", "expected_value": 9.0},
]


def normalize_alias(text: str) -> str:
    """别名归一化：去空白 + 小写（与 alias_resolver.normalize 保持一致）。"""
    return re.sub(r"\s+", "", text or "").lower()


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    def count(table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"]) if row else 0

    return {
        "metrics": count("metric_definition"),
        "versions": count("metric_version"),
        "aliases": count("metric_alias"),
        "test_cases": count("metric_test_case"),
    }


def ensure_metric_catalog() -> dict[str, Any]:
    """幂等播种 20 指标、v1.0 认证版本、别名与测试用例。

    重复执行由 ``catalog_meta.metric_seed_version`` 守卫；所有写入使用
    ``INSERT ... ON CONFLICT DO NOTHING``，不覆盖用户后续修改。
    """
    ensure_staging_available()
    now = staging_now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            guard = conn.execute(
                "SELECT value FROM catalog_meta WHERE key = :key",
                {"key": "metric_seed_version"},
            ).fetchone()
            if guard and str(guard["value"]) == METRIC_SEED_VERSION:
                counts = _counts(conn)
                conn.execute("COMMIT")
                return {"seeded": False, **counts, "version": METRIC_SEED_VERSION}

            for item in METRIC_SEED:
                conn.execute(
                    """INSERT INTO metric_definition
                       (code, name, definition, formula_text, formula_sql, boundary_json,
                        owner, business_domain, unit, sensitivity_level, status,
                        current_version, term_code, sort_order, created_at, updated_at, updated_by)
                       VALUES (:code, :name, :definition, :formula_text, :formula_sql, :boundary_json,
                               :owner, :business_domain, :unit, :sensitivity_level, 'certified',
                               'v1.0', :term_code, :sort_order, :now, :now, 'system')
                       ON CONFLICT(code) DO NOTHING""",
                    {
                        "code": item["code"],
                        "name": item["name"],
                        "definition": item["definition"],
                        "formula_text": item["formula_text"],
                        "formula_sql": item["formula_sql"],
                        "boundary_json": json.dumps(item["boundary"], ensure_ascii=False),
                        "owner": item["owner"],
                        "business_domain": item["business_domain"],
                        "unit": item["unit"],
                        "sensitivity_level": item["sensitivity_level"],
                        "term_code": item["term_code"],
                        "sort_order": item["sort_order"],
                        "now": now,
                    },
                )

            for item in METRIC_SEED:
                snapshot = {
                    "name": item["name"],
                    "definition": item["definition"],
                    "formula_text": item["formula_text"],
                    "formula_sql": item["formula_sql"],
                    "boundary": item["boundary"],
                    "owner": item["owner"],
                    "business_domain": item["business_domain"],
                    "unit": item["unit"],
                    "sensitivity_level": item["sensitivity_level"],
                    "term_code": item["term_code"],
                }
                conn.execute(
                    """INSERT INTO metric_version
                       (metric_code, version, status, snapshot_json, change_note,
                        effective_period, certified_by, certified_at, created_by,
                        created_at, updated_at)
                       VALUES (:metric_code, 'v1.0', 'certified', :snapshot, '初始认证版本',
                               '2026-01', 'system', :now, 'system', :now, :now)
                       ON CONFLICT(metric_code, version) DO NOTHING""",
                    {
                        "metric_code": item["code"],
                        "snapshot": json.dumps(snapshot, ensure_ascii=False),
                        "now": now,
                    },
                )

            for item in METRIC_ALIAS_SEED:
                conn.execute(
                    """INSERT INTO metric_alias
                       (metric_code, alias, normalized_alias, alias_type, priority,
                        status, created_by, created_at, updated_at)
                       VALUES (:metric_code, :alias, :normalized_alias, :alias_type, :priority,
                               'active', 'system', :now, :now)
                       ON CONFLICT(metric_code, normalized_alias) DO NOTHING""",
                    {
                        "metric_code": item["metric_code"],
                        "alias": item["alias"],
                        "normalized_alias": normalize_alias(item["alias"]),
                        "alias_type": item["alias_type"],
                        "priority": item["priority"],
                        "now": now,
                    },
                )

            version_ids = {
                row["metric_code"]: int(row["id"])
                for row in conn.execute(
                    "SELECT id, metric_code FROM metric_version WHERE version = 'v1.0'"
                )
            }
            for item in METRIC_TEST_CASE_SEED:
                conn.execute(
                    """INSERT INTO metric_test_case
                       (metric_code, version_id, name, period_code, expected_value,
                        tolerance_abs, tolerance_rel, last_status, created_at, updated_at)
                       VALUES (:metric_code, :version_id, :name, :period_code, :expected_value,
                               0.01, 0.0001, 'not_run', :now, :now)
                       ON CONFLICT(metric_code, name) DO NOTHING""",
                    {
                        "metric_code": item["metric_code"],
                        "version_id": version_ids.get(item["metric_code"]),
                        "name": f"{DEFAULT_PERIOD} 基线",
                        "period_code": DEFAULT_PERIOD,
                        "expected_value": float(item["expected_value"]),
                        "now": now,
                    },
                )

            conn.execute(
                """INSERT INTO catalog_meta (key, value, updated_at)
                   VALUES ('metric_seed_version', :value, :now)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                                  updated_at = excluded.updated_at""",
                {"value": METRIC_SEED_VERSION, "now": now},
            )
            counts = _counts(conn)
            conn.execute("COMMIT")
            return {"seeded": True, **counts, "version": METRIC_SEED_VERSION}
        except Exception:
            _safe_rollback(conn)
            raise
