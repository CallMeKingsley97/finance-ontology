"""财务报告模板、指标取数、勾稽校验与文档组装。"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from app.db.connection import query_all, query_one
from app.schemas.common import AppError
from app.services.metadata import get_periods
from app.services.ontology import ONTOLOGY_VERSION, SQL_ATTRIBUTES
from app.services.query import format_amount, format_pct, format_ratio
from app.services.security import mask_rows

logger = logging.getLogger("finance_ontology.reports")

TEMPLATE_DEFS: list[dict[str, Any]] = [
    {
        "code": "financial_position",
        "name": "财务状况分析",
        "description": "资产、负债、权益结构，营运能力，趋势与预算，风险提示与口径附录。",
        "sections": [
            {"key": "summary", "title": "执行摘要", "type": "kpi"},
            {"key": "structure_table", "title": "资产负债结构", "type": "table"},
            {"key": "structure_chart", "title": "资产/负债/权益构成", "type": "chart"},
            {"key": "working_capital", "title": "营运能力", "type": "table"},
            {"key": "trend_chart", "title": "近 12 期资产与收入趋势", "type": "chart"},
            {"key": "budget_table", "title": "预算执行", "type": "table"},
            {"key": "risk_text", "title": "风险提示", "type": "text"},
            {"key": "appendix", "title": "附录：指标口径与 SQL", "type": "appendix"},
        ],
    },
    {
        "code": "income_statement",
        "name": "损益分析",
        "description": "收入、成本、毛利、费用、利润、Top 客户、预算达成与口径附录。",
        "sections": [
            {"key": "summary", "title": "执行摘要", "type": "kpi"},
            {"key": "trend_chart", "title": "收入与毛利率趋势", "type": "chart"},
            {"key": "top_customers", "title": "Top 5 客户", "type": "table"},
            {"key": "cost_expense_chart", "title": "费用构成", "type": "chart"},
            {"key": "cost_expense_table", "title": "成本费用分析", "type": "table"},
            {"key": "pnl_table", "title": "利润表", "type": "table"},
            {"key": "pnl_chart", "title": "利润形成", "type": "chart"},
            {"key": "budget_table", "title": "预算达成", "type": "table"},
            {"key": "appendix", "title": "附录：指标口径与 SQL", "type": "appendix"},
        ],
    },
]

_DEFAULT_ASSUMPTIONS = [
    "默认合并全部主体（制造主体 + 贸易主体）",
    "金额单位为人民币元，比率以小数返回（0.25 = 25%）",
    "数据来自本地 SQLite mock 数据，由固定随机种子生成",
    "报告由规则引擎组装，未使用外部服务",
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _add_months(code: str, delta: int) -> str:
    year, month = (int(part) for part in code.split("-"))
    month += delta
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}"


def _period_label(code: str) -> str:
    year, month = (int(part) for part in code.split("-"))
    return f"{year}年{month}月"


def _period_exists(code: str) -> bool:
    return query_one("SELECT 1 AS ok FROM dim_period WHERE period_code = :code", {"code": code}) is not None


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pnl_values(period_code: str) -> dict[str, float]:
    row = query_one(
        """SELECT
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
             AND p.period_code = :period_id""",
        {"period_id": period_code},
    ) or {}
    revenue = _num(row.get("revenue"))
    cogs = _num(row.get("cogs"))
    tax_surcharge = _num(row.get("tax_surcharge"))
    period_expense = _num(row.get("period_expense"))
    nonop_income = _num(row.get("nonop_income"))
    nonop_expense = _num(row.get("nonop_expense"))
    income_tax = _num(row.get("income_tax"))
    total_profit = round(revenue - cogs - tax_surcharge - period_expense + nonop_income - nonop_expense, 2)
    net_profit = round(total_profit - income_tax, 2)
    gross_profit = round(revenue - cogs, 2)
    gross_margin = round(gross_profit / revenue, 4) if revenue else 0.0
    return {
        "revenue": revenue,
        "cogs": cogs,
        "tax_surcharge": tax_surcharge,
        "period_expense": period_expense,
        "nonop_income": nonop_income,
        "nonop_expense": nonop_expense,
        "income_tax": income_tax,
        "total_profit": total_profit,
        "net_profit": net_profit,
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
    }


def _balance_values(period_code: str) -> dict[str, float]:
    row = query_one(
        """SELECT
               ROUND(SUM(CASE WHEN a.account_type = 'asset' THEN b.closing_balance ELSE 0 END), 2) AS total_assets,
               ROUND(SUM(CASE WHEN a.account_type = 'liability' THEN b.closing_balance ELSE 0 END), 2) AS total_liabilities,
               ROUND(SUM(CASE WHEN a.account_type = 'equity' THEN b.closing_balance ELSE 0 END), 2) AS total_equity,
               ROUND(SUM(CASE WHEN a.account_type = 'asset' AND a.is_current = 1 THEN b.closing_balance ELSE 0 END), 2) AS current_assets,
               ROUND(SUM(CASE WHEN a.account_type = 'asset' AND a.is_current = 0 THEN b.closing_balance ELSE 0 END), 2) AS non_current_assets,
               ROUND(SUM(CASE WHEN a.account_type = 'liability' AND a.is_current = 1 THEN b.closing_balance ELSE 0 END), 2) AS current_liabilities,
               ROUND(SUM(CASE WHEN a.account_type = 'liability' AND a.is_current = 0 THEN b.closing_balance ELSE 0 END), 2) AS non_current_liabilities,
               ROUND(SUM(CASE WHEN a.account_code IN ('1001', '1002') THEN b.closing_balance ELSE 0 END), 2) AS cash_total,
               ROUND(SUM(CASE WHEN a.account_code = '1122' THEN b.closing_balance ELSE 0 END), 2) AS receivable_total,
               ROUND(SUM(CASE WHEN a.account_code = '1405' THEN b.closing_balance ELSE 0 END), 2) AS inventory_total
           FROM fact_balance b
           JOIN dim_account a ON a.account_id = b.account_id
           JOIN dim_period p ON p.period_id = b.period_id
           WHERE p.period_code = :period_id""",
        {"period_id": period_code},
    ) or {}
    total_assets = _num(row.get("total_assets"))
    total_liabilities = _num(row.get("total_liabilities"))
    total_equity = _num(row.get("total_equity"))
    current_assets = _num(row.get("current_assets"))
    current_liabilities = _num(row.get("current_liabilities"))
    return {
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "non_current_assets": _num(row.get("non_current_assets")),
        "non_current_liabilities": _num(row.get("non_current_liabilities")),
        "cash_total": _num(row.get("cash_total")),
        "receivable_total": _num(row.get("receivable_total")),
        "inventory_total": _num(row.get("inventory_total")),
        "debt_asset_ratio": round(total_liabilities / total_assets, 4) if total_assets else 0.0,
        "current_ratio": round(current_assets / current_liabilities, 4) if current_liabilities else 0.0,
    }


def _pct_change(current: Any, previous: Any) -> float | None:
    current_value = _num(current)
    previous_value = _num(previous)
    if previous_value == 0:
        return None
    return round((current_value - previous_value) / abs(previous_value), 4)


def _trend(change: float | None) -> str:
    if change is None:
        return "flat"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _kpi(
    key: str,
    label: str,
    value: float,
    unit: str,
    change_pct: float | None = None,
) -> dict[str, Any]:
    if unit == "CNY":
        formatted = format_amount(value)
    elif unit == "%":
        formatted = format_pct(value)
    elif unit == "倍":
        formatted = format_ratio(value)
    else:
        formatted = f"{value:,.2f}"
    return {
        "key": key,
        "label": label,
        "value": round(value, 4),
        "unit": unit,
        "formatted": formatted,
        "change_pct": change_pct,
        "trend": _trend(change_pct),
    }


def _table(columns: list[dict[str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": rows}


def _chart(
    kind: str,
    x: str,
    labels: list[str],
    series: list[tuple[str, list[float]]],
) -> dict[str, Any]:
    return {
        "type": kind,
        "x": x,
        "labels": labels,
        "series": [{"name": name, "data": [float(value) for value in data]} for name, data in series],
    }


def _balance_series(start_code: str, end_code: str) -> list[dict[str, Any]]:
    return query_all(
        """SELECT p.period_code AS period,
                  ROUND(SUM(CASE WHEN a.account_type = 'asset' THEN b.closing_balance ELSE 0 END), 2) AS total_assets,
                  ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue
           FROM fact_balance b
           JOIN dim_account a ON a.account_id = b.account_id
           JOIN dim_period p ON p.period_id = b.period_id
           WHERE p.period_code BETWEEN :start_period AND :end_period
           GROUP BY p.period_code
           ORDER BY p.period_code""",
        {"start_period": start_code, "end_period": end_code},
    )


def _income_series(start_code: str, end_code: str) -> list[dict[str, Any]]:
    return query_all(
        """SELECT p.period_code AS period,
                  ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
                  ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs
           FROM fact_balance b
           JOIN dim_account a ON a.account_id = b.account_id
           JOIN dim_period p ON p.period_id = b.period_id
           WHERE a.account_type IN ('revenue', 'cogs')
             AND p.period_code BETWEEN :start_period AND :end_period
           GROUP BY p.period_code
           ORDER BY p.period_code""",
        {"start_period": start_code, "end_period": end_code},
    )


def _expense_composition(period_code: str) -> list[dict[str, Any]]:
    return query_all(
        """SELECT a.account_name AS name,
                  ROUND(SUM(b.debit_amount - b.credit_amount), 2) AS value
           FROM fact_balance b
           JOIN dim_account a ON a.account_id = b.account_id
           JOIN dim_period p ON p.period_id = b.period_id
           WHERE a.account_type = 'expense'
             AND p.period_code = :period_id
           GROUP BY a.account_name
           ORDER BY value DESC""",
        {"period_id": period_code},
    )


def _top_customers(period_code: str, limit: int = 5) -> list[dict[str, Any]]:
    return query_all(
        """SELECT c.customer_name AS customer_name,
                  ROUND(SUM(i.total_amount), 2) AS amount
           FROM fact_invoice i
           JOIN dim_customer c ON c.customer_id = i.customer_id
           JOIN dim_period p ON p.period_id = i.period_id
           WHERE i.invoice_type = 'sales'
             AND p.period_code = :period_id
           GROUP BY c.customer_id, c.customer_name
           ORDER BY amount DESC
           LIMIT :limit""",
        {"period_id": period_code, "limit": limit},
    )


def _budget_rows(period_code: str, limit: int = 20) -> list[dict[str, Any]]:
    return query_all(
        """WITH actual AS (
               SELECT a.account_code AS account_code, a.account_name AS account_name,
                      ROUND(SUM(CASE WHEN a.balance_direction = 'credit'
                                     THEN b.credit_amount - b.debit_amount
                                     ELSE b.debit_amount - b.credit_amount END), 2) AS actual_amount
               FROM fact_balance b
               JOIN dim_account a ON a.account_id = b.account_id
               JOIN dim_period p ON p.period_id = b.period_id
               WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
                 AND p.period_code = :period_id
               GROUP BY a.account_code, a.account_name
           ), budget AS (
               SELECT a.account_code AS account_code, a.account_name AS account_name,
                      ROUND(SUM(bu.amount), 2) AS budget_amount
               FROM fact_budget bu
               JOIN dim_account a ON a.account_id = bu.account_id
               JOIN dim_period p ON p.period_id = bu.period_id
               WHERE p.period_code = :period_id
               GROUP BY a.account_code, a.account_name
           )
           SELECT b.account_name AS name, b.budget_amount AS budget_amount,
                  COALESCE(a.actual_amount, 0) AS actual_amount,
                  ROUND(COALESCE(a.actual_amount, 0) - b.budget_amount, 2) AS variance,
                  ROUND(COALESCE(a.actual_amount, 0) / NULLIF(b.budget_amount, 0), 4) AS achievement_rate
           FROM budget b
           LEFT JOIN actual a ON a.account_code = b.account_code
           ORDER BY b.account_code
           LIMIT :limit""",
        {"period_id": period_code, "limit": limit},
    )


def check_reconciliation(period_code: str) -> dict[str, Any]:
    """至少 5 条勾稽校验，容差 0.01。"""
    balance = _balance_values(period_code)
    pnl = _pnl_values(period_code)
    checks: list[dict[str, Any]] = []

    def add(name: str, left: float, right: float) -> None:
        diff = round(abs(left - right), 2)
        checks.append(
            {
                "name": name,
                "left": round(left, 2),
                "right": round(right, 2),
                "diff": diff,
                "passed": diff <= 0.01,
            }
        )

    add(
        "资产总额=负债总额+所有者权益",
        balance["total_assets"],
        balance["total_liabilities"] + balance["total_equity"],
    )
    add(
        "资产总额=流动资产+非流动资产",
        balance["total_assets"],
        balance["current_assets"] + balance["non_current_assets"],
    )
    add(
        "负债总额=流动负债+非流动负债",
        balance["total_liabilities"],
        balance["current_liabilities"] + balance["non_current_liabilities"],
    )
    add(
        "利润总额=收入-成本-税金-期间费用+营业外收支",
        pnl["total_profit"],
        pnl["revenue"] - pnl["cogs"] - pnl["tax_surcharge"] - pnl["period_expense"]
        + pnl["nonop_income"] - pnl["nonop_expense"],
    )
    add(
        "净利润=利润总额-所得税",
        pnl["net_profit"],
        pnl["total_profit"] - pnl["income_tax"],
    )
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def _appendix(codes: list[str], assumptions: list[str]) -> dict[str, Any]:
    placeholders = ", ".join(f":code_{index}" for index in range(len(codes)))
    params = {f"code_{index}": code for index, code in enumerate(codes)}
    rows: list[dict[str, Any]] = []
    try:
        rows = query_all(
            f"""SELECT code, name, formula_sql, description, unit
                FROM ontology_sql_attribute
                WHERE code IN ({placeholders})""",
            params,
        )
    except Exception:  # noqa: BLE001 - 未生成本体时回退静态目录
        rows = []
    if not rows:
        rows = [
            {
                "code": attr["code"],
                "name": attr["name"],
                "formula_sql": attr["formula_sql"],
                "description": attr["description"],
                "unit": attr["unit"],
            }
            for attr in SQL_ATTRIBUTES
            if attr["code"] in codes
        ]
    metrics = [
        {
            "code": row["code"],
            "name": row["name"],
            "formula": row["description"],
            "sql": row["formula_sql"],
            "unit": row["unit"],
        }
        for row in rows
    ]
    return {
        "metrics": metrics,
        "assumptions": assumptions,
        "ontology_version": ONTOLOGY_VERSION,
    }


def _comparison_code(period_code: str, comparison: str) -> str | None:
    if comparison == "none":
        return None
    offset = -12 if comparison == "yoy" else -1
    candidate = _add_months(period_code, offset)
    return candidate if _period_exists(candidate) else None


# ===== 报表包复用包装（薄封装，不改变旧函数行为） =====


def balance_values(period_code: str) -> dict[str, float]:
    return _balance_values(period_code)


def pnl_values(period_code: str) -> dict[str, float]:
    return _pnl_values(period_code)


def budget_rows(period_code: str, limit: int = 20) -> list[dict[str, Any]]:
    return _budget_rows(period_code, limit)


def balance_series(start_code: str, end_code: str) -> list[dict[str, Any]]:
    return _balance_series(start_code, end_code)


def income_series(start_code: str, end_code: str) -> list[dict[str, Any]]:
    return _income_series(start_code, end_code)


def top_customers(period_code: str, limit: int = 5) -> list[dict[str, Any]]:
    return _top_customers(period_code, limit)


def kpi_item(
    key: str, label: str, value: float, unit: str, change_pct: float | None = None
) -> dict[str, Any]:
    return _kpi(key, label, value, unit, change_pct)


def table_block(columns: list[dict[str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _table(columns, rows)


def chart_block(
    kind: str, x: str, labels: list[str], series: list[tuple[str, list[float]]]
) -> dict[str, Any]:
    return _chart(kind, x, labels, series)


def section_block(key: str, title: str, section_type: str, **kwargs: Any) -> dict[str, Any]:
    return _section(key, title, section_type, **kwargs)


def period_label(code: str) -> str:
    return _period_label(code)


def comparison_code(period_code: str, comparison: str) -> str | None:
    return _comparison_code(period_code, comparison)


def _section(
    key: str,
    title: str,
    section_type: str,
    *,
    data: dict[str, Any] | None = None,
    table: dict[str, Any] | None = None,
    chart: dict[str, Any] | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "type": section_type,
        "data": data,
        "table": table,
        "chart": chart,
        "text": text,
    }


def _financial_position(period_code: str, comparison_code: str | None) -> list[dict[str, Any]]:
    balance = _balance_values(period_code)
    pnl = _pnl_values(period_code)
    previous_balance = _balance_values(comparison_code) if comparison_code else None
    previous_pnl = _pnl_values(comparison_code) if comparison_code else None

    kpis = [
        _kpi(
            "total_assets",
            "资产总额",
            balance["total_assets"],
            "CNY",
            _pct_change(balance["total_assets"], previous_balance["total_assets"] if previous_balance else None),
        ),
        _kpi(
            "total_liabilities",
            "负债总额",
            balance["total_liabilities"],
            "CNY",
            _pct_change(balance["total_liabilities"], previous_balance["total_liabilities"] if previous_balance else None),
        ),
        _kpi(
            "total_equity",
            "所有者权益",
            balance["total_equity"],
            "CNY",
            _pct_change(balance["total_equity"], previous_balance["total_equity"] if previous_balance else None),
        ),
        _kpi(
            "debt_asset_ratio",
            "资产负债率",
            balance["debt_asset_ratio"],
            "%",
            round(balance["debt_asset_ratio"] - previous_balance["debt_asset_ratio"], 4) if previous_balance else None,
        ),
        _kpi(
            "current_ratio",
            "流动比率",
            balance["current_ratio"],
            "倍",
            _pct_change(balance["current_ratio"], previous_balance["current_ratio"] if previous_balance else None),
        ),
        _kpi(
            "net_profit",
            "净利润",
            pnl["net_profit"],
            "CNY",
            _pct_change(pnl["net_profit"], previous_pnl["net_profit"] if previous_pnl else None),
        ),
    ]

    structure_rows = [
        {"item": "资产总额", "amount": balance["total_assets"], "ratio_to_assets": 1.0},
        {
            "item": "流动资产",
            "amount": balance["current_assets"],
            "ratio_to_assets": round(balance["current_assets"] / balance["total_assets"], 4) if balance["total_assets"] else 0,
        },
        {
            "item": "非流动资产",
            "amount": balance["non_current_assets"],
            "ratio_to_assets": round(balance["non_current_assets"] / balance["total_assets"], 4) if balance["total_assets"] else 0,
        },
        {"item": "负债总额", "amount": balance["total_liabilities"], "ratio_to_assets": None},
        {"item": "流动负债", "amount": balance["current_liabilities"], "ratio_to_assets": None},
        {"item": "非流动负债", "amount": balance["non_current_liabilities"], "ratio_to_assets": None},
        {"item": "所有者权益", "amount": balance["total_equity"], "ratio_to_assets": None},
    ]

    working_rows = [
        {
            "item": "货币资金",
            "amount": balance["cash_total"],
            "ratio_to_assets": round(balance["cash_total"] / balance["total_assets"], 4) if balance["total_assets"] else 0,
        },
        {
            "item": "应收账款",
            "amount": balance["receivable_total"],
            "ratio_to_assets": round(balance["receivable_total"] / balance["total_assets"], 4) if balance["total_assets"] else 0,
        },
        {
            "item": "存货",
            "amount": balance["inventory_total"],
            "ratio_to_assets": round(balance["inventory_total"] / balance["total_assets"], 4) if balance["total_assets"] else 0,
        },
    ]

    trend_start = _add_months(period_code, -11)
    trend = _balance_series(trend_start, period_code)

    budget_rows = _budget_rows(period_code)
    budget_table_rows = [
        {
            "item": row["name"],
            "budget": row["budget_amount"],
            "actual": row["actual_amount"],
            "variance": row["variance"],
            "achievement_rate": row["achievement_rate"],
        }
        for row in budget_rows
    ]

    risk_lines: list[str] = []
    if balance["debt_asset_ratio"] > 0.7:
        risk_lines.append(f"资产负债率 {format_pct(balance['debt_asset_ratio'])} 高于 70% 阈值。")
    if balance["current_ratio"] < 1:
        risk_lines.append(f"流动比率 {format_ratio(balance['current_ratio'])} 低于 1，短期偿债能力需关注。")
    if pnl["net_profit"] < 0:
        risk_lines.append(f"本期净利润为 {format_amount(pnl['net_profit'])}，处于亏损状态。")
    risk_text = " ".join(risk_lines) if risk_lines else (
        f"资产负债率 {format_pct(balance['debt_asset_ratio'])}，流动比率 {format_ratio(balance['current_ratio'])}，"
        "未触发 70% 负债率、流动比率低于 1 或净利润为负的风险阈值。"
    )

    assumptions = [
        f"报告期间：{_period_label(period_code)}",
        *_DEFAULT_ASSUMPTIONS,
    ]
    appendix = _appendix(
        ["total_assets", "total_liabilities", "total_equity", "debt_asset_ratio", "current_ratio", "net_profit"],
        assumptions,
    )
    return [
        _section("summary", "执行摘要", "kpi", data={"items": kpis}),
        _section(
            "structure_table",
            "资产负债结构",
            "table",
            table=_table(
                [
                    {"key": "item", "label": "项目"},
                    {"key": "amount", "label": "金额（元）"},
                    {"key": "ratio_to_assets", "label": "占资产比"},
                ],
                structure_rows,
            ),
        ),
        _section(
            "structure_chart",
            "资产/负债/权益构成",
            "chart",
            chart=_chart(
                "bar",
                "item",
                ["资产总额", "负债总额", "所有者权益"],
                [(
                    "金额",
                    [balance["total_assets"], balance["total_liabilities"], balance["total_equity"]],
                )],
            ),
        ),
        _section(
            "working_capital",
            "营运能力",
            "table",
            table=_table(
                [
                    {"key": "item", "label": "项目"},
                    {"key": "amount", "label": "金额（元）"},
                    {"key": "ratio_to_assets", "label": "占资产比"},
                ],
                working_rows,
            ),
        ),
        _section(
            "trend_chart",
            "近 12 期资产与收入趋势",
            "chart",
            chart=_chart(
                "line",
                "period",
                [row["period"] for row in trend],
                [
                    ("资产总额", [_num(row["total_assets"]) for row in trend]),
                    ("营业收入", [_num(row["revenue"]) for row in trend]),
                ],
            ),
        ),
        _section(
            "budget_table",
            "预算执行",
            "table",
            table=_table(
                [
                    {"key": "item", "label": "科目"},
                    {"key": "budget", "label": "预算（元）"},
                    {"key": "actual", "label": "实际（元）"},
                    {"key": "variance", "label": "差异（元）"},
                    {"key": "achievement_rate", "label": "达成率"},
                ],
                budget_table_rows,
            ),
        ),
        _section("risk_text", "风险提示", "text", text=risk_text),
        _section(
            "appendix",
            "附录：指标口径与 SQL",
            "appendix",
            data={
                "metrics": appendix["metrics"],
                "assumptions": assumptions,
                "ontology_version": ONTOLOGY_VERSION,
            },
        ),
    ]


def _income_statement(period_code: str, comparison_code: str | None) -> list[dict[str, Any]]:
    pnl = _pnl_values(period_code)
    previous = _pnl_values(comparison_code) if comparison_code else None

    kpis = [
        _kpi("revenue", "营业收入", pnl["revenue"], "CNY", _pct_change(pnl["revenue"], previous["revenue"] if previous else None)),
        _kpi("cogs", "营业成本", pnl["cogs"], "CNY", _pct_change(pnl["cogs"], previous["cogs"] if previous else None)),
        _kpi("gross_profit", "毛利", pnl["gross_profit"], "CNY", _pct_change(pnl["gross_profit"], previous["gross_profit"] if previous else None)),
        _kpi("gross_margin", "毛利率", pnl["gross_margin"], "%", round(pnl["gross_margin"] - previous["gross_margin"], 4) if previous else None),
        _kpi("operating_profit", "营业利润", pnl["revenue"] - pnl["cogs"] - pnl["tax_surcharge"] - pnl["period_expense"], "CNY", None),
        _kpi("net_profit", "净利润", pnl["net_profit"], "CNY", _pct_change(pnl["net_profit"], previous["net_profit"] if previous else None)),
    ]

    trend_start = _add_months(period_code, -5)
    trend = _income_series(trend_start, period_code)
    trend_labels = [row["period"] for row in trend]
    trend_margins = [
        round((_num(row["revenue"]) - _num(row["cogs"])) / _num(row["revenue"]), 4) if _num(row["revenue"]) else 0.0
        for row in trend
    ]

    top_customers = _top_customers(period_code, 5)
    expense_rows = _expense_composition(period_code)

    operating_profit = pnl["revenue"] - pnl["cogs"] - pnl["tax_surcharge"] - pnl["period_expense"]
    cost_rate = round(pnl["cogs"] / pnl["revenue"], 4) if pnl["revenue"] else 0.0
    expense_rate = round(pnl["period_expense"] / pnl["revenue"], 4) if pnl["revenue"] else 0.0

    cost_expense_rows = [
        {"item": "营业成本", "value": pnl["cogs"], "formatted": format_amount(pnl["cogs"])},
        {"item": "营业成本率", "value": cost_rate, "formatted": format_pct(cost_rate)},
        {"item": "期间费用", "value": pnl["period_expense"], "formatted": format_amount(pnl["period_expense"])},
        {"item": "费用率", "value": expense_rate, "formatted": format_pct(expense_rate)},
        {"item": "毛利", "value": pnl["gross_profit"], "formatted": format_amount(pnl["gross_profit"])},
        {"item": "毛利率", "value": pnl["gross_margin"], "formatted": format_pct(pnl["gross_margin"])},
    ]

    pnl_rows = [
        {"item": "营业收入", "amount": pnl["revenue"]},
        {"item": "营业成本", "amount": pnl["cogs"]},
        {"item": "毛利", "amount": pnl["gross_profit"]},
        {"item": "税金及附加", "amount": pnl["tax_surcharge"]},
        {"item": "期间费用", "amount": pnl["period_expense"]},
        {"item": "营业利润", "amount": round(operating_profit, 2)},
        {"item": "营业外收入", "amount": pnl["nonop_income"]},
        {"item": "营业外支出", "amount": pnl["nonop_expense"]},
        {"item": "利润总额", "amount": pnl["total_profit"]},
        {"item": "所得税费用", "amount": pnl["income_tax"]},
        {"item": "净利润", "amount": pnl["net_profit"]},
    ]

    budget_rows = _budget_rows(period_code)
    budget_table_rows = [
        {
            "item": row["name"],
            "budget": row["budget_amount"],
            "actual": row["actual_amount"],
            "variance": row["variance"],
            "achievement_rate": row["achievement_rate"],
        }
        for row in budget_rows
    ]

    assumptions = [
        f"报告期间：{_period_label(period_code)}",
        *_DEFAULT_ASSUMPTIONS,
    ]
    appendix = _appendix(
        [
            "revenue_total",
            "cogs_total",
            "gross_profit",
            "gross_margin",
            "period_expense",
            "operating_profit",
            "total_profit",
            "net_profit",
            "budget_variance",
        ],
        assumptions,
    )
    return [
        _section("summary", "执行摘要", "kpi", data={"items": kpis}),
        _section(
            "trend_chart",
            "收入与毛利率趋势",
            "chart",
            chart=_chart(
                "line",
                "period",
                trend_labels,
                [
                    ("营业收入", [_num(row["revenue"]) for row in trend]),
                    ("毛利率", trend_margins),
                ],
            ),
        ),
        _section(
            "top_customers",
            "Top 5 客户",
            "table",
            table=_table(
                [
                    {"key": "customer_name", "label": "客户"},
                    {"key": "amount", "label": "销售额（元）"},
                ],
                [{"customer_name": row["customer_name"], "amount": row["amount"]} for row in top_customers],
            ),
        ),
        _section(
            "cost_expense_chart",
            "费用构成",
            "chart",
            chart=_chart(
                "pie",
                "name",
                [row["name"] for row in expense_rows],
                [("费用金额", [_num(row["value"]) for row in expense_rows])],
            ),
        ),
        _section(
            "cost_expense_table",
            "成本费用分析",
            "table",
            table=_table(
                [
                    {"key": "item", "label": "项目"},
                    {"key": "value", "label": "数值"},
                    {"key": "formatted", "label": "展示"},
                ],
                cost_expense_rows,
            ),
        ),
        _section(
            "pnl_table",
            "利润表",
            "table",
            table=_table(
                [
                    {"key": "item", "label": "项目"},
                    {"key": "amount", "label": "金额（元）"},
                ],
                pnl_rows,
            ),
        ),
        _section(
            "pnl_chart",
            "利润形成",
            "chart",
            chart=_chart(
                "bar",
                "item",
                [row["item"] for row in pnl_rows],
                [("金额", [_num(row["amount"]) for row in pnl_rows])],
            ),
        ),
        _section(
            "budget_table",
            "预算达成",
            "table",
            table=_table(
                [
                    {"key": "item", "label": "科目"},
                    {"key": "budget", "label": "预算（元）"},
                    {"key": "actual", "label": "实际（元）"},
                    {"key": "variance", "label": "差异（元）"},
                    {"key": "achievement_rate", "label": "达成率"},
                ],
                budget_table_rows,
            ),
        ),
        _section(
            "appendix",
            "附录：指标口径与 SQL",
            "appendix",
            data={
                "metrics": appendix["metrics"],
                "assumptions": assumptions,
                "ontology_version": ONTOLOGY_VERSION,
            },
        ),
    ]


def list_templates() -> dict[str, Any]:
    return {
        "items": TEMPLATE_DEFS,
        "periods": get_periods(),
    }


def generate_report(
    template_code: str,
    period_code: str,
    comparison: str,
    role: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    if template_code not in ("financial_position", "income_statement"):
        raise AppError(404, "NOT_FOUND", f"报告模板不存在: {template_code}")
    if comparison not in ("yoy", "mom", "none"):
        raise AppError(422, "VALIDATION_FAILED", "comparison 仅支持 yoy/mom/none")
    if not _period_exists(period_code):
        raise AppError(422, "VALIDATION_FAILED", f"期间不存在: {period_code}")
    if role == "viewer":
        raise AppError(403, "FORBIDDEN", "viewer 角色不能生成报告")

    comparison_code = _comparison_code(period_code, comparison)
    reconciliation = check_reconciliation(period_code)
    warnings: list[str] = []
    if not reconciliation["passed"]:
        failed = [item["name"] for item in reconciliation["checks"] if not item["passed"]]
        warnings.append(f"勾稽校验未通过：{', '.join(failed)}")

    if template_code == "financial_position":
        title = f"{_period_label(period_code)}财务状况分析"
        sections = _financial_position(period_code, comparison_code)
        appendix_codes = ["total_assets", "total_liabilities", "total_equity", "debt_asset_ratio", "current_ratio", "net_profit"]
    else:
        title = f"{_period_label(period_code)}损益分析"
        sections = _income_statement(period_code, comparison_code)
        appendix_codes = [
            "revenue_total", "cogs_total", "gross_profit", "gross_margin",
            "period_expense", "operating_profit", "total_profit", "net_profit", "budget_variance",
        ]

    assumptions = [f"报告期间：{_period_label(period_code)}", *_DEFAULT_ASSUMPTIONS]
    appendix = _appendix(appendix_codes, assumptions)
    masked_columns: set[str] = set()
    for section in sections:
        table = section.get("table")
        if not table:
            continue
        column_names = [column["key"] for column in table.get("columns", [])]
        masked_rows, masked = mask_rows(column_names, table.get("rows", []), role)
        table["rows"] = masked_rows
        masked_columns.update(masked)
    elapsed = int((time.perf_counter() - started) * 1000)
    document = {
        "template_code": template_code,
        "title": title,
        "period": {
            "code": period_code,
            "label": _period_label(period_code),
            "comparison": comparison_code,
        },
        "generated_at": _now(),
        "sections": sections,
        "reconciliation": reconciliation,
        "appendix": appendix,
        "warnings": warnings,
        "meta": {
            "role": role,
            "masked_columns": sorted(masked_columns),
            "duration_ms": elapsed,
            "ontology_version": ONTOLOGY_VERSION,
            "engine": "rule",
        },
    }
    try:
        from app.services import quality

        quality.run_quality(trigger_type="report", period_code=period_code, role=role)
    except Exception:  # noqa: BLE001 - 质量留痕失败不影响旧报告
        logger.exception("legacy report quality hook failed period=%s", period_code)
    return document
