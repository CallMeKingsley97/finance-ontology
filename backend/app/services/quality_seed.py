"""10 条质量规则的幂等播种（5 条勾稽 + 4 条业务数据质量 + 1 条 staging 完整性）。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.services.ingest.store import (
    ensure_staging_available,
    staging_connection,
    staging_now,
)

QUALITY_SEED_VERSION = "1"

_ASSETS_SQL = """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'asset'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_LIABILITIES_EQUITY_SQL = """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type IN ('liability', 'equity')
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_CURRENT_ASSET_SPLIT_SQL = """SELECT ROUND(
    COALESCE(SUM(CASE WHEN a.is_current = 1 THEN b.closing_balance ELSE 0 END), 0)
    + COALESCE(SUM(CASE WHEN a.is_current = 0 THEN b.closing_balance ELSE 0 END), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'asset'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_LIABILITIES_SQL = """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'liability'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_CURRENT_LIABILITY_SPLIT_SQL = """SELECT ROUND(
    COALESCE(SUM(CASE WHEN a.is_current = 1 THEN b.closing_balance ELSE 0 END), 0)
    + COALESCE(SUM(CASE WHEN a.is_current = 0 THEN b.closing_balance ELSE 0 END), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'liability'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_TOTAL_PROFIT_SQL = """SELECT ROUND(
    COALESCE(SUM(CASE WHEN a.account_type = 'revenue'
                      THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'cogs'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'tax_surcharge'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'expense'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    + COALESCE(SUM(CASE WHEN a.account_code = '6301'
                        THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_code = '6711'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
, 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating')
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_NET_PROFIT_SQL = """SELECT ROUND(
    COALESCE(SUM(CASE WHEN a.account_type = 'revenue'
                      THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'cogs'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'tax_surcharge'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'expense'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    + COALESCE(SUM(CASE WHEN a.account_code = '6301'
                        THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_code = '6711'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_code = '6801'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
, 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_NET_PROFIT_PLUS_TAX_SQL = """SELECT ROUND(
    COALESCE(SUM(CASE WHEN a.account_type = 'revenue'
                      THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'cogs'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'tax_surcharge'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_type = 'expense'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
    + COALESCE(SUM(CASE WHEN a.account_code = '6301'
                        THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
    - COALESCE(SUM(CASE WHEN a.account_code = '6711'
                        THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
, 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating')
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_TOTAL_PROFIT_MINUS_TAX_SQL = """SELECT ROUND(
    (
        SELECT COALESCE(SUM(CASE WHEN a.account_type = 'revenue'
                                 THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
               - COALESCE(SUM(CASE WHEN a.account_type = 'cogs'
                                  THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
               - COALESCE(SUM(CASE WHEN a.account_type = 'tax_surcharge'
                                  THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
               - COALESCE(SUM(CASE WHEN a.account_type = 'expense'
                                  THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
               + COALESCE(SUM(CASE WHEN a.account_code = '6301'
                                  THEN b.credit_amount - b.debit_amount ELSE 0 END), 0)
               - COALESCE(SUM(CASE WHEN a.account_code = '6711'
                                  THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
        FROM fact_balance b
        JOIN dim_account a ON a.account_id = b.account_id
        JOIN dim_period p ON p.period_id = b.period_id
        WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating')
          AND p.period_code = :end_period
          AND p.period_code BETWEEN :start_period AND :end_period
    )
    - (
        SELECT COALESCE(SUM(CASE WHEN a.account_code = '6801'
                                 THEN b.debit_amount - b.credit_amount ELSE 0 END), 0)
        FROM fact_balance b
        JOIN dim_account a ON a.account_id = b.account_id
        JOIN dim_period p ON p.period_id = b.period_id
        WHERE a.account_code = '6801'
          AND p.period_code = :end_period
          AND p.period_code BETWEEN :start_period AND :end_period
    )
, 2) AS value"""

_BALANCE_ROWS_SQL = """SELECT COUNT(*) AS value
FROM fact_balance b
JOIN dim_period p ON p.period_id = b.period_id
WHERE p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_BALANCE_UNIQUE_SQL = """SELECT COUNT(*) AS value FROM (
    SELECT b.period_id AS period_id, b.account_id AS account_id,
           b.company_id AS company_id, COUNT(*) AS row_count
    FROM fact_balance b
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE p.period_code = :end_period
      AND p.period_code BETWEEN :start_period AND :end_period
    GROUP BY b.period_id, b.account_id, b.company_id
    HAVING COUNT(*) > 1
)"""

_REVENUE_SQL = """SELECT ROUND(COALESCE(SUM(b.credit_amount - b.debit_amount), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'revenue'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_RECEIVABLE_SQL = """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_code = '1122'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period"""

_STAGING_CATALOG_SQL = "SELECT COUNT(*) AS value FROM catalog_table"


def _rule(
    code: str,
    name: str,
    rule_type: str,
    target_db: str,
    expression: dict[str, Any],
    *,
    target_ref: str = "",
    tolerance: float = 0.01,
    severity: str = "warning",
    schedule: str = "report",
    owner: str = "财务共享中心",
    description: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "rule_type": rule_type,
        "target_db": target_db,
        "target_ref": target_ref,
        "expression_json": json.dumps(expression, ensure_ascii=False),
        "tolerance": tolerance,
        "severity": severity,
        "schedule": schedule,
        "owner": owner,
        "description": description,
    }


QUALITY_RULE_SEED: list[dict[str, Any]] = [
    _rule(
        "recon_assets_liabilities_equity",
        "资产=负债+所有者权益",
        "reconciliation",
        "business",
        {"left_sql": _ASSETS_SQL, "right_sql": _LIABILITIES_EQUITY_SQL},
        description="核心勾稽 1：资产总额等于负债总额加所有者权益。",
    ),
    _rule(
        "recon_assets_current_split",
        "资产=流动+非流动",
        "reconciliation",
        "business",
        {"left_sql": _ASSETS_SQL, "right_sql": _CURRENT_ASSET_SPLIT_SQL},
        description="核心勾稽 2：资产总额等于流动资产加非流动资产。",
    ),
    _rule(
        "recon_liabilities_current_split",
        "负债=流动+非流动",
        "reconciliation",
        "business",
        {"left_sql": _LIABILITIES_SQL, "right_sql": _CURRENT_LIABILITY_SPLIT_SQL},
        description="负债总额等于流动负债加非流动负债。",
    ),
    _rule(
        "recon_profit_total",
        "利润总额=收入-成本-税金-费用+营业外收支",
        "reconciliation",
        "business",
        {"left_sql": _TOTAL_PROFIT_SQL, "right_sql": _NET_PROFIT_PLUS_TAX_SQL},
        description="利润总额按利润表科目重算，并与净利润加所得税交叉核对。",
    ),
    _rule(
        "recon_net_profit",
        "净利润=利润总额-所得税",
        "reconciliation",
        "business",
        {"left_sql": _NET_PROFIT_SQL, "right_sql": _TOTAL_PROFIT_MINUS_TAX_SQL},
        description="核心勾稽 3：净利润等于利润总额减所得税费用。",
    ),
    _rule(
        "data_balance_rows",
        "科目余额非空",
        "completeness",
        "business",
        {"sql": _BALANCE_ROWS_SQL, "operator": ">=", "expected": 1},
        schedule="sync_report",
        description="每个期间至少存在 1 行科目余额。",
    ),
    _rule(
        "data_balance_unique",
        "科目余额唯一",
        "uniqueness",
        "business",
        {"sql": _BALANCE_UNIQUE_SQL, "operator": "==", "expected": 0},
        schedule="sync_report",
        description="同一期间、科目、主体不得出现重复余额行。",
    ),
    _rule(
        "data_revenue_non_negative",
        "营业收入非负",
        "custom_sql",
        "business",
        {"sql": _REVENUE_SQL, "operator": ">=", "expected": 0},
        schedule="sync_report",
        description="营业收入不得为负数。",
    ),
    _rule(
        "data_receivable_non_negative",
        "应收账款非负",
        "range",
        "business",
        {"sql": _RECEIVABLE_SQL, "min": 0, "max": 1000000000000},
        schedule="sync_report",
        description="应收账款（1122）期末余额不得为负数。",
    ),
    _rule(
        "staging_catalog_registered",
        "目录已登记表数",
        "completeness",
        "staging",
        {"sql": _STAGING_CATALOG_SQL, "operator": ">=", "expected": 1},
        target_ref="catalog_table",
        severity="info",
        schedule="sync_report",
        owner="数据治理组",
        description="staging 目录至少登记 1 张表；目录为空时记为 skipped。",
    ),
]


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute("SELECT COUNT(*) AS n FROM quality_rule").fetchone()
    return {"rules": int(row["n"]) if row else 0}


def ensure_quality_rules() -> dict[str, Any]:
    """幂等播种 10 条质量规则。"""
    ensure_staging_available()
    now = staging_now()
    with staging_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            guard = conn.execute(
                "SELECT value FROM catalog_meta WHERE key = :key",
                {"key": "quality_seed_version"},
            ).fetchone()
            if guard and str(guard["value"]) == QUALITY_SEED_VERSION:
                counts = _counts(conn)
                conn.execute("COMMIT")
                return {"seeded": False, **counts, "version": QUALITY_SEED_VERSION}

            for rule in QUALITY_RULE_SEED:
                conn.execute(
                    """INSERT INTO quality_rule
                       (code, name, rule_type, target_db, target_ref, expression_json,
                        tolerance, severity, enabled, schedule, owner, description,
                        created_at, updated_at, updated_by)
                       VALUES (:code, :name, :rule_type, :target_db, :target_ref, :expression_json,
                               :tolerance, :severity, 1, :schedule, :owner, :description,
                               :now, :now, 'system')
                       ON CONFLICT(code) DO NOTHING""",
                    {**rule, "now": now},
                )

            conn.execute(
                """INSERT INTO catalog_meta (key, value, updated_at)
                   VALUES ('quality_seed_version', :value, :now)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                                  updated_at = excluded.updated_at""",
                {"value": QUALITY_SEED_VERSION, "now": now},
            )
            counts = _counts(conn)
            conn.execute("COMMIT")
            return {"seeded": True, **counts, "version": QUALITY_SEED_VERSION}
        except Exception:
            _safe_rollback(conn)
            raise
