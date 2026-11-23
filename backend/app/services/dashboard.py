"""仪表盘汇总：最新期间 KPI、12 期趋势、勾稽与计数。"""

from __future__ import annotations

from typing import Any

from app.db.connection import query_all, query_one
from app.schemas.common import AppError
from app.services.query import format_amount, format_pct, format_ratio
from app.services.reports import (
    _add_months,
    _balance_values,
    _period_exists,
    _pnl_values,
    check_reconciliation,
)


def _kpi(
    key: str,
    label: str,
    value: float,
    unit: str,
    change_pct: float | None,
) -> dict[str, Any]:
    if unit == "CNY":
        formatted = format_amount(value)
    elif unit == "%":
        formatted = format_pct(value)
    elif unit == "倍":
        formatted = format_ratio(value)
    else:
        formatted = f"{value:,.2f}"
    if change_pct is None:
        trend = "flat"
    elif change_pct > 0:
        trend = "up"
    elif change_pct < 0:
        trend = "down"
    else:
        trend = "flat"
    return {
        "key": key,
        "label": label,
        "value": round(value, 4),
        "unit": unit,
        "formatted": formatted,
        "change_pct": change_pct,
        "trend": trend,
    }


def _change(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous), 4)


def _trend_series(latest: str) -> list[dict[str, Any]]:
    start = _add_months(latest, -11)
    rows = query_all(
        """SELECT p.period_code AS period,
                  ROUND(SUM(CASE WHEN a.account_type = 'revenue'
                                 THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
                  ROUND(SUM(CASE WHEN a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
                                 THEN CASE WHEN a.balance_direction = 'credit'
                                           THEN b.credit_amount - b.debit_amount
                                           ELSE b.debit_amount - b.credit_amount END
                                 ELSE 0 END), 2) AS net_profit
           FROM fact_balance b
           JOIN dim_account a ON a.account_id = b.account_id
           JOIN dim_period p ON p.period_id = b.period_id
           WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
             AND p.period_code BETWEEN :start_period AND :end_period
           GROUP BY p.period_code
           ORDER BY p.period_code""",
        {"start_period": start, "end_period": latest},
    )
    return [
        {
            "period": row["period"],
            "revenue": float(row["revenue"] or 0),
            "net_profit": float(row["net_profit"] or 0),
        }
        for row in rows
    ]


def _budget_achievement(period_code: str, revenue: float) -> float | None:
    row = query_one(
        """SELECT ROUND(COALESCE(SUM(bu.amount), 0), 2) AS budget
           FROM fact_budget bu
           JOIN dim_account a ON a.account_id = bu.account_id
           JOIN dim_period p ON p.period_id = bu.period_id
           WHERE a.account_type = 'revenue'
             AND p.period_code = :period_id""",
        {"period_id": period_code},
    )
    budget = float(row["budget"] or 0) if row else 0.0
    if budget == 0:
        return None
    return round(revenue / budget, 4)


def _count(table: str) -> int:
    row = query_one(f"SELECT COUNT(*) AS n FROM {table}")
    return int(row["n"]) if row else 0


def get_summary(role: str) -> dict[str, Any]:
    latest_row = query_one("SELECT period_code FROM dim_period ORDER BY period_code DESC LIMIT 1")
    if not latest_row:
        raise AppError(503, "DB_UNAVAILABLE", "数据库中没有会计期间，请先播种")
    latest = latest_row["period_code"]
    previous_code = _add_months(latest, -1)
    previous_available = _period_exists(previous_code)

    pnl = _pnl_values(latest)
    balance = _balance_values(latest)
    previous_pnl = _pnl_values(previous_code) if previous_available else None
    previous_balance = _balance_values(previous_code) if previous_available else None

    kpis = [
        _kpi(
            "revenue",
            "营业收入",
            pnl["revenue"],
            "CNY",
            _change(pnl["revenue"], previous_pnl["revenue"] if previous_pnl else None),
        ),
        _kpi(
            "net_profit",
            "净利润",
            pnl["net_profit"],
            "CNY",
            _change(pnl["net_profit"], previous_pnl["net_profit"] if previous_pnl else None),
        ),
        _kpi(
            "total_assets",
            "资产总额",
            balance["total_assets"],
            "CNY",
            _change(balance["total_assets"], previous_balance["total_assets"] if previous_balance else None),
        ),
        _kpi(
            "debt_asset_ratio",
            "资产负债率",
            balance["debt_asset_ratio"],
            "%",
            round(balance["debt_asset_ratio"] - previous_balance["debt_asset_ratio"], 4)
            if previous_balance
            else None,
        ),
        _kpi("gross_margin", "毛利率", pnl["gross_margin"], "%", None),
        _kpi(
            "budget_achievement",
            "收入预算达成率",
            _budget_achievement(latest, pnl["revenue"]) or 0.0,
            "%",
            None,
        ),
    ]

    reconciliation = check_reconciliation(latest)
    return {
        "latest_period": latest,
        "kpis": kpis,
        "trend": _trend_series(latest),
        "reconciliation": reconciliation,
        "counts": {
            "tables": 10,
            "terms": _count("ontology_term"),
            "relations": _count("ontology_relation"),
            "sql_attributes": _count("ontology_sql_attribute"),
            "queries": _count("query_audit"),
        },
    }
