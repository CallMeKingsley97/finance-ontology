"""业务表元数据读取与样本展示。"""

from __future__ import annotations

from typing import Any

from app.db.connection import BUSINESS_TABLES, query_all, quote_identifier
from app.schemas.common import AppError
from app.services.security import SENSITIVE_BY_TABLE, mask_rows, mask_value

TABLE_DESCRIPTIONS: dict[str, str] = {
    "dim_company": "公司主体维度，包含主体编码、名称、行业与记账本位币。",
    "dim_account": "会计科目维度，包含科目类型、余额方向、流动性与父子科目关系。",
    "dim_customer": "客户维度，包含区域、行业、账期与敏感联系方式。",
    "dim_vendor": "供应商维度，包含采购品类、账期与敏感联系方式。",
    "dim_period": "会计期间维度，覆盖 2025-01 至 2026-10 共 22 个已结账期间。",
    "fact_journal": "记账凭证分录，单行借贷互斥，按期间、主体、科目记录。",
    "fact_invoice": "销售/采购发票事实表，记录不含税金额、税额与价税合计。",
    "fact_payment": "收付款事实表，记录客户回款与供应商付款。",
    "fact_balance": "月度科目余额表，含期初、本期借贷发生额与期末余额，是报表主数据源。",
    "fact_budget": "年度预算事实表，按期间、科目、部门与版本记录预算金额。",
}

COLUMN_DESCRIPTIONS: dict[str, str] = {
    "dim_company.company_code": "公司主体编码，唯一。",
    "dim_company.company_name": "公司主体名称。",
    "dim_account.account_code": "会计科目编码，唯一。",
    "dim_account.account_name": "会计科目名称。",
    "dim_account.account_type": "科目类型：asset/liability/equity/revenue/cogs/tax_surcharge/expense/non_operating/income_tax。",
    "dim_account.balance_direction": "余额方向：debit（借）或 credit（贷）。",
    "dim_customer.phone": "客户联系电话（敏感，按角色脱敏）。",
    "dim_customer.bank_account": "客户银行账号（敏感，按角色脱敏）。",
    "dim_vendor.bank_account": "供应商银行账号（敏感，按角色脱敏）。",
    "dim_vendor.phone": "供应商联系电话（敏感，按角色脱敏）。",
    "dim_period.period_code": "会计期间编码 YYYY-MM。",
    "fact_balance.closing_balance": "期末余额，资产/负债/权益表按此汇总。",
    "fact_balance.debit_amount": "本期借方发生额。",
    "fact_balance.credit_amount": "本期贷方发生额。",
    "fact_invoice.total_amount": "价税合计金额。",
    "fact_budget.amount": "预算金额。",
    "query_audit.status": "问数状态：succeeded/unanswered/failed。",
}


def _table_info(table: str) -> list[dict[str, Any]]:
    return query_all(f"PRAGMA table_info({quote_identifier(table)})", enforce_authorizer=False)


def list_tables() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for table in BUSINESS_TABLES:
        columns = _table_info(table)
        count_row = query_all(
            f"SELECT COUNT(*) AS n FROM {quote_identifier(table)}",
            enforce_authorizer=False,
        )[0]
        sensitive = [
            column
            for (tbl, column) in SENSITIVE_BY_TABLE
            if tbl == table
        ]
        items.append(
            {
                "name": table,
                "row_count": int(count_row["n"]),
                "column_count": len(columns),
                "sensitive_columns": sensitive,
                "description": TABLE_DESCRIPTIONS.get(table, ""),
            }
        )
    return {"items": items}


def get_table_detail(table: str, role: str) -> dict[str, Any]:
    if table not in BUSINESS_TABLES:
        raise AppError(404, "NOT_FOUND", f"业务表不存在: {table}")

    columns_info = _table_info(table)
    column_names = [col["name"] for col in columns_info]

    sample_rows: list[dict[str, Any]] = []
    if column_names:
        selected = ", ".join(quote_identifier(name) for name in column_names)
        sample_rows = query_all(
            f"SELECT {selected} FROM {quote_identifier(table)} LIMIT 5",
            enforce_authorizer=False,
        )

    sample_masked_by_column: dict[str, str | None] = {}
    for column in column_names:
        if (table, column) in SENSITIVE_BY_TABLE:
            raw = next((row.get(column) for row in sample_rows if row.get(column) is not None), None)
            # 样本列同样按请求角色二次掩码，避免 viewer 通过列元数据看到部分原值
            sample_masked_by_column[column] = mask_value(column, raw, role)
        else:
            sample_masked_by_column[column] = None

    columns: list[dict[str, Any]] = []
    for col in columns_info:
        key = (table, col["name"])
        classification = SENSITIVE_BY_TABLE[key][0] if key in SENSITIVE_BY_TABLE else None
        columns.append(
            {
                "name": col["name"],
                "type": col["type"] or "TEXT",
                "pk": bool(col["pk"]),
                "notnull": bool(col["notnull"]),
                "default": col["dflt_value"],
                "is_sensitive": key in SENSITIVE_BY_TABLE,
                "classification": classification,
                "sample_masked": sample_masked_by_column.get(col["name"]),
                "description": COLUMN_DESCRIPTIONS.get(f"{table}.{col['name']}", ""),
            }
        )

    foreign_keys = [
        {
            "from_column": row["from"],
            "to_table": row["table"],
            "to_column": row["to"],
        }
        for row in query_all(
            f"PRAGMA foreign_key_list({quote_identifier(table)})",
            enforce_authorizer=False,
        )
    ]

    indexes: list[dict[str, Any]] = []
    for index in query_all(
        f"PRAGMA index_list({quote_identifier(table)})",
        enforce_authorizer=False,
    ):
        index_columns = [
            row["name"]
            for row in query_all(
                f"PRAGMA index_info({quote_identifier(index['name'])})",
                enforce_authorizer=False,
            )
        ]
        indexes.append(
            {
                "name": index["name"],
                "unique": bool(index["unique"]),
                "columns": index_columns,
            }
        )

    table_sensitive = [column for (tbl, column) in SENSITIVE_BY_TABLE if tbl == table]
    masked_rows, masked_columns = mask_rows(column_names, sample_rows, role, table_sensitive)
    count_row = query_all(
        f"SELECT COUNT(*) AS n FROM {quote_identifier(table)}",
        enforce_authorizer=False,
    )[0]
    return {
        "name": table,
        "row_count": int(count_row["n"]),
        "description": TABLE_DESCRIPTIONS.get(table, ""),
        "columns": columns,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
        "sample_rows": masked_rows,
        "masked_columns": masked_columns,
    }


def get_periods() -> list[dict[str, str]]:
    rows = query_all(
        """SELECT period_code, fiscal_year, period_no
           FROM dim_period
           ORDER BY period_code DESC"""
    )
    return [
        {"code": row["period_code"], "label": f"{row['fiscal_year']}年{row['period_no']}月"}
        for row in rows
    ]
