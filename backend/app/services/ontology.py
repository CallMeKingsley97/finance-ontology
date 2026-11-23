"""规则驱动本体生成、术语校准与关系/属性查询。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from app.db.connection import BUSINESS_TABLES, query_all, query_one, write_connection
from app.schemas.common import AppError
from app.services.metadata import COLUMN_DESCRIPTIONS
from app.services.security import SENSITIVE_BY_TABLE, mask_value

ONTOLOGY_VERSION = "2026.11.1"
ALLOWED_CATEGORIES = {"table", "metric", "sensitive"}
UPDATE_FIELDS = {"display_name", "description", "synonyms", "mapped_table", "mapped_columns"}

TABLE_TERMS: list[dict[str, Any]] = [
    {"term_code": "term_company", "name": "company", "display_name": "公司主体", "synonyms": ["公司", "主体", "法人", "核算主体"], "mapped_table": "dim_company", "description": "公司主体维度，用于区分制造主体与贸易主体的财务数据。"},
    {"term_code": "term_account", "name": "account", "display_name": "会计科目", "synonyms": ["科目", "账户", "会计账户", "科目表"], "mapped_table": "dim_account", "description": "会计科目维度，按资产、负债、权益、收入、成本、费用等类型组织，是报表取数的分类基础。"},
    {"term_code": "term_customer", "name": "customer", "display_name": "客户", "synonyms": ["客户", "买方", "购货方", "经销商"], "mapped_table": "dim_customer", "description": "客户维度，记录客户名称、区域、行业、账期以及敏感联系方式。"},
    {"term_code": "term_vendor", "name": "vendor", "display_name": "供应商", "synonyms": ["供应商", "卖方", "供货方", "厂商"], "mapped_table": "dim_vendor", "description": "供应商维度，记录采购品类、账期以及敏感联系方式与银行账号。"},
    {"term_code": "term_period", "name": "period", "display_name": "会计期间", "synonyms": ["期间", "月份", "会计期", "账期"], "mapped_table": "dim_period", "description": "会计期间维度，覆盖 2025-01 至 2026-10 的月度已结账期间。"},
    {"term_code": "term_journal", "name": "journal", "display_name": "记账凭证", "synonyms": ["凭证", "分录", "记账凭证", "日记账"], "mapped_table": "fact_journal", "description": "记账凭证分录事实表，每行借贷互斥，按期间、主体、科目记录。"},
    {"term_code": "term_invoice", "name": "invoice", "display_name": "发票", "synonyms": ["发票", "单据", "销售发票", "采购发票"], "mapped_table": "fact_invoice", "description": "销售与采购发票事实表，记录不含税金额、税额与价税合计。"},
    {"term_code": "term_payment", "name": "payment", "display_name": "收付款", "synonyms": ["收款", "付款", "回款", "结算", "收付款"], "mapped_table": "fact_payment", "description": "客户回款与供应商付款事实表，记录收付款方式与金额。"},
    {"term_code": "term_balance", "name": "balance", "display_name": "科目余额", "synonyms": ["余额", "科目余额", "期末余额", "发生额"], "mapped_table": "fact_balance", "description": "月度科目余额事实表，含期初、本期借贷发生额与期末余额，是财务报表主数据源。"},
    {"term_code": "term_budget", "name": "budget", "display_name": "预算", "synonyms": ["预算", "预算数", "计划", "年度预算"], "mapped_table": "fact_budget", "description": "年度预算事实表，按期间、科目、部门与版本记录预算金额。"},
]

METRIC_TERMS: list[dict[str, Any]] = [
    {"term_code": "metric_revenue_total", "name": "revenue_total", "display_name": "营业收入", "synonyms": ["营收", "收入", "销售收入", "营业额", "主营业务收入"], "description": "企业日常经营活动形成的经济利益总流入，取收入类科目本期贷方发生额减借方发生额，默认合并全部主体。"},
    {"term_code": "metric_cogs_total", "name": "cogs_total", "display_name": "营业成本", "synonyms": ["成本", "营业成本", "主营业务成本", "销售成本"], "description": "与营业收入对应的营业成本，取成本类科目本期借方发生额减贷方发生额。"},
    {"term_code": "metric_gross_profit", "name": "gross_profit", "display_name": "毛利", "synonyms": ["毛利", "毛利润", "销售毛利"], "description": "营业收入减营业成本，反映产品/服务的直接盈利空间。"},
    {"term_code": "metric_gross_margin", "name": "gross_margin", "display_name": "毛利率", "synonyms": ["毛利率", "毛利水平", "销售毛利率"], "description": "毛利除以营业收入，比率指标，0.25 表示 25%。"},
    {"term_code": "metric_period_expense", "name": "period_expense", "display_name": "期间费用", "synonyms": ["期间费用", "费用", "费用总额", "三项费用"], "description": "销售费用、管理费用、研发费用与财务费用之和，取费用类科目本期净发生额。"},
    {"term_code": "metric_operating_profit", "name": "operating_profit", "display_name": "营业利润", "synonyms": ["营业利润", "经营利润"], "description": "营业收入减营业成本、税金及附加与期间费用后的经营成果。"},
    {"term_code": "metric_total_profit", "name": "total_profit", "display_name": "利润总额", "synonyms": ["利润总额", "税前利润"], "description": "营业利润加营业外收入减营业外支出，为所得税前利润。"},
    {"term_code": "metric_net_profit", "name": "net_profit", "display_name": "净利润", "synonyms": ["净利润", "净利", "税后利润", "盈利"], "description": "利润总额减所得税费用后的最终经营成果。"},
    {"term_code": "metric_total_assets", "name": "total_assets", "display_name": "资产总额", "synonyms": ["资产总额", "总资产", "资产合计"], "description": "全部资产类科目期末余额合计。"},
    {"term_code": "metric_total_liabilities", "name": "total_liabilities", "display_name": "负债总额", "synonyms": ["负债总额", "总负债", "负债合计"], "description": "全部负债类科目期末余额合计。"},
    {"term_code": "metric_total_equity", "name": "total_equity", "display_name": "所有者权益", "synonyms": ["所有者权益", "股东权益", "净资产", "权益合计"], "description": "全部权益类科目期末余额合计。"},
    {"term_code": "metric_debt_asset_ratio", "name": "debt_asset_ratio", "display_name": "资产负债率", "synonyms": ["资产负债率", "负债率", "负债比率"], "description": "负债总额除以资产总额，比率指标，用于衡量长期偿债能力。"},
    {"term_code": "metric_current_ratio", "name": "current_ratio", "display_name": "流动比率", "synonyms": ["流动比率", "短期偿债能力"], "description": "流动资产除以流动负债，倍数指标。"},
    {"term_code": "metric_budget_variance", "name": "budget_variance", "display_name": "预算差异", "synonyms": ["预算差异", "预算达成率", "达成率", "预算执行"], "description": "实际发生额减预算金额；达成率为实际发生额除以预算金额。"},
]

SENSITIVE_TERMS: list[dict[str, Any]] = [
    {"term_code": "sensitive_customer_phone", "name": "customer_phone", "display_name": "客户手机号", "synonyms": ["手机号", "电话", "联系电话", "客户电话"], "mapped_table": "dim_customer", "mapped_columns": ["phone"], "description": "客户联系电话，属于个人敏感信息，按角色掩码后输出。"},
    {"term_code": "sensitive_customer_bank_account", "name": "customer_bank_account", "display_name": "客户银行账号", "synonyms": ["银行账号", "银行卡号", "客户账号", "收款账号"], "mapped_table": "dim_customer", "mapped_columns": ["bank_account"], "description": "客户银行账号，属于个人/企业敏感支付信息，按角色掩码后输出。"},
    {"term_code": "sensitive_vendor_bank_account", "name": "vendor_bank_account", "display_name": "供应商银行账号", "synonyms": ["供应商账号", "供应商银行账号", "付款账号"], "mapped_table": "dim_vendor", "mapped_columns": ["bank_account"], "description": "供应商银行账号，属于企业敏感支付信息，按角色掩码后输出。"},
]

RELATIONS: list[dict[str, Any]] = [
    {"relation_code": "rel_account_parent", "from_term": "term_account", "to_term": "term_account", "relation_type": "parentOf", "cardinality": "1:N", "join_hint": "dim_account.parent_code = dim_account.account_code", "description": "会计科目的父子层级关系。"},
    {"relation_code": "rel_journal_account", "from_term": "term_journal", "to_term": "term_account", "relation_type": "postsTo", "cardinality": "N:1", "join_hint": "fact_journal.account_id = dim_account.account_id", "description": "每条凭证分录记入一个会计科目。"},
    {"relation_code": "rel_journal_period", "from_term": "term_journal", "to_term": "term_period", "relation_type": "belongsTo", "cardinality": "N:1", "join_hint": "fact_journal.period_id = dim_period.period_id", "description": "凭证分录归属于一个会计期间。"},
    {"relation_code": "rel_journal_company", "from_term": "term_journal", "to_term": "term_company", "relation_type": "belongsTo", "cardinality": "N:1", "join_hint": "fact_journal.company_id = dim_company.company_id", "description": "凭证分录归属于一个公司主体。"},
    {"relation_code": "rel_invoice_customer", "from_term": "term_invoice", "to_term": "term_customer", "relation_type": "billedTo", "cardinality": "N:1", "join_hint": "fact_invoice.customer_id = dim_customer.customer_id", "description": "销售发票开给一个客户。"},
    {"relation_code": "rel_invoice_vendor", "from_term": "term_invoice", "to_term": "term_vendor", "relation_type": "fromVendor", "cardinality": "N:1", "join_hint": "fact_invoice.vendor_id = dim_vendor.vendor_id", "description": "采购发票来自一个供应商。"},
    {"relation_code": "rel_invoice_period", "from_term": "term_invoice", "to_term": "term_period", "relation_type": "belongsTo", "cardinality": "N:1", "join_hint": "fact_invoice.period_id = dim_period.period_id", "description": "发票归属于一个会计期间。"},
    {"relation_code": "rel_payment_customer", "from_term": "term_payment", "to_term": "term_customer", "relation_type": "receivedFrom", "cardinality": "N:1", "join_hint": "fact_payment.customer_id = dim_customer.customer_id", "description": "收款来自一个客户。"},
    {"relation_code": "rel_payment_vendor", "from_term": "term_payment", "to_term": "term_vendor", "relation_type": "paidTo", "cardinality": "N:1", "join_hint": "fact_payment.vendor_id = dim_vendor.vendor_id", "description": "付款支付给一个供应商。"},
    {"relation_code": "rel_balance_account", "from_term": "term_balance", "to_term": "term_account", "relation_type": "measures", "cardinality": "N:1", "join_hint": "fact_balance.account_id = dim_account.account_id", "description": "科目余额度量一个会计科目。"},
    {"relation_code": "rel_balance_period", "from_term": "term_balance", "to_term": "term_period", "relation_type": "belongsTo", "cardinality": "N:1", "join_hint": "fact_balance.period_id = dim_period.period_id", "description": "科目余额归属于一个会计期间。"},
    {"relation_code": "rel_budget_period", "from_term": "term_budget", "to_term": "term_period", "relation_type": "forPeriod", "cardinality": "N:1", "join_hint": "fact_budget.period_id = dim_period.period_id", "description": "预算按会计期间编制。"},
    {"relation_code": "rel_budget_account", "from_term": "term_budget", "to_term": "term_account", "relation_type": "forAccount", "cardinality": "N:1", "join_hint": "fact_budget.account_id = dim_account.account_id", "description": "预算按会计科目编制。"},
    {"relation_code": "rel_customer_phone", "from_term": "term_customer", "to_term": "sensitive_customer_phone", "relation_type": "hasAttribute", "cardinality": "1:1", "join_hint": "dim_customer.phone（敏感）", "description": "客户拥有一个敏感手机号属性。"},
    {"relation_code": "rel_customer_bank", "from_term": "term_customer", "to_term": "sensitive_customer_bank_account", "relation_type": "hasAttribute", "cardinality": "1:1", "join_hint": "dim_customer.bank_account（敏感）", "description": "客户拥有一个敏感银行账号属性。"},
    {"relation_code": "rel_vendor_bank", "from_term": "term_vendor", "to_term": "sensitive_vendor_bank_account", "relation_type": "hasAttribute", "cardinality": "1:1", "join_hint": "dim_vendor.bank_account（敏感）", "description": "供应商拥有一个敏感银行账号属性。"},
]

SQL_ATTRIBUTES: list[dict[str, Any]] = [
    {
        "code": "revenue_total",
        "name": "营业收入",
        "term_code": "metric_revenue_total",
        "unit": "CNY",
        "description": "收入类科目本期贷方发生额减借方发生额。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.credit_amount - b.debit_amount), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'revenue'
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "cogs_total",
        "name": "营业成本",
        "term_code": "metric_cogs_total",
        "unit": "CNY",
        "description": "成本类科目本期借方发生额减贷方发生额。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.debit_amount - b.credit_amount), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'cogs'
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "gross_profit",
        "name": "毛利",
        "term_code": "metric_gross_profit",
        "unit": "CNY",
        "description": "营业收入减营业成本。",
        "formula_sql": """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
        ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(revenue - cogs, 2) AS value FROM agg""",
    },
    {
        "code": "gross_margin",
        "name": "毛利率",
        "term_code": "metric_gross_margin",
        "unit": "%",
        "description": "毛利除以营业收入。",
        "formula_sql": """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
        ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND((revenue - cogs) / NULLIF(revenue, 0), 4) AS value FROM agg""",
    },
    {
        "code": "period_expense",
        "name": "期间费用",
        "term_code": "metric_period_expense",
        "unit": "CNY",
        "description": "销售、管理、研发、财务费用合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.debit_amount - b.credit_amount), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'expense'
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "operating_profit",
        "name": "营业利润",
        "term_code": "metric_operating_profit",
        "unit": "CNY",
        "description": "收入减成本、税金及附加、期间费用。",
        "formula_sql": """WITH agg AS (
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
SELECT ROUND(revenue - cogs - tax_surcharge - period_expense, 2) AS value FROM agg""",
    },
    {
        "code": "total_profit",
        "name": "利润总额",
        "term_code": "metric_total_profit",
        "unit": "CNY",
        "description": "营业利润加营业外收入减营业外支出。",
        "formula_sql": """WITH agg AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'revenue' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
        ROUND(SUM(CASE WHEN a.account_type = 'cogs' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs,
        ROUND(SUM(CASE WHEN a.account_type = 'tax_surcharge' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS tax_surcharge,
        ROUND(SUM(CASE WHEN a.account_type = 'expense' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS period_expense,
        ROUND(SUM(CASE WHEN a.account_code = '6301' THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS nonop_income,
        ROUND(SUM(CASE WHEN a.account_code = '6711' THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS nonop_expense
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating')
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(revenue - cogs - tax_surcharge - period_expense + nonop_income - nonop_expense, 2) AS value FROM agg""",
    },
    {
        "code": "net_profit",
        "name": "净利润",
        "term_code": "metric_net_profit",
        "unit": "CNY",
        "description": "利润总额减所得税费用。",
        "formula_sql": """WITH agg AS (
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
SELECT ROUND(revenue - cogs - tax_surcharge - period_expense + nonop_income - nonop_expense - income_tax, 2) AS value FROM agg""",
    },
    {
        "code": "total_assets",
        "name": "资产总额",
        "term_code": "metric_total_assets",
        "unit": "CNY",
        "description": "资产类科目期末余额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'asset'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "total_liabilities",
        "name": "负债总额",
        "term_code": "metric_total_liabilities",
        "unit": "CNY",
        "description": "负债类科目期末余额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'liability'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "total_equity",
        "name": "所有者权益",
        "term_code": "metric_total_equity",
        "unit": "CNY",
        "description": "权益类科目期末余额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'equity'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "current_assets",
        "name": "流动资产",
        "term_code": "metric_current_ratio",
        "unit": "CNY",
        "description": "is_current=1 的资产类科目期末余额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'asset' AND a.is_current = 1
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "current_liabilities",
        "name": "流动负债",
        "term_code": "metric_current_ratio",
        "unit": "CNY",
        "description": "is_current=1 的负债类科目期末余额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_type = 'liability' AND a.is_current = 1
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "debt_asset_ratio",
        "name": "资产负债率",
        "term_code": "metric_debt_asset_ratio",
        "unit": "%",
        "description": "负债总额除以资产总额。",
        "formula_sql": """WITH bal AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'asset' THEN b.closing_balance ELSE 0 END), 2) AS total_assets,
        ROUND(SUM(CASE WHEN a.account_type = 'liability' THEN b.closing_balance ELSE 0 END), 2) AS total_liabilities
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE p.period_code = :end_period
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(total_liabilities / NULLIF(total_assets, 0), 4) AS value FROM bal""",
    },
    {
        "code": "current_ratio",
        "name": "流动比率",
        "term_code": "metric_current_ratio",
        "unit": "倍",
        "description": "流动资产除以流动负债。",
        "formula_sql": """WITH bal AS (
    SELECT
        ROUND(SUM(CASE WHEN a.account_type = 'asset' AND a.is_current = 1 THEN b.closing_balance ELSE 0 END), 2) AS current_assets,
        ROUND(SUM(CASE WHEN a.account_type = 'liability' AND a.is_current = 1 THEN b.closing_balance ELSE 0 END), 2) AS current_liabilities
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE p.period_code = :end_period
      AND p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(current_assets / NULLIF(current_liabilities, 0), 4) AS value FROM bal""",
    },
    {
        "code": "cash_total",
        "name": "货币资金",
        "term_code": "metric_total_assets",
        "unit": "CNY",
        "description": "库存现金与银行存款期末余额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_code IN ('1001', '1002')
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "receivable_total",
        "name": "应收账款",
        "term_code": "metric_total_assets",
        "unit": "CNY",
        "description": "科目 1122 的期末余额。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_code = '1122'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "inventory_total",
        "name": "存货",
        "term_code": "metric_total_assets",
        "unit": "CNY",
        "description": "科目 1405 的期末余额。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(b.closing_balance), 0), 2) AS value
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_period p ON p.period_id = b.period_id
WHERE a.account_code = '1405'
  AND p.period_code = :end_period
  AND p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "budget_amount",
        "name": "预算金额",
        "term_code": "metric_budget_variance",
        "unit": "CNY",
        "description": "指定期间内预算金额合计。",
        "formula_sql": """SELECT ROUND(COALESCE(SUM(bu.amount), 0), 2) AS value
FROM fact_budget bu
JOIN dim_period p ON p.period_id = bu.period_id
WHERE p.period_code BETWEEN :start_period AND :end_period""",
    },
    {
        "code": "budget_variance",
        "name": "预算差异",
        "term_code": "metric_budget_variance",
        "unit": "CNY",
        "description": "实际发生额减预算金额。",
        "formula_sql": """WITH actual AS (
    SELECT ROUND(COALESCE(SUM(
        CASE WHEN a.balance_direction = 'credit' THEN b.credit_amount - b.debit_amount
             ELSE b.debit_amount - b.credit_amount END), 0), 2) AS actual_amount
    FROM fact_balance b
    JOIN dim_account a ON a.account_id = b.account_id
    JOIN dim_period p ON p.period_id = b.period_id
    WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense', 'non_operating', 'income_tax')
      AND p.period_code BETWEEN :start_period AND :end_period
), budget AS (
    SELECT ROUND(COALESCE(SUM(bu.amount), 0), 2) AS budget_amount
    FROM fact_budget bu
    JOIN dim_period p ON p.period_id = bu.period_id
    WHERE p.period_code BETWEEN :start_period AND :end_period
)
SELECT ROUND(actual.actual_amount - budget.budget_amount, 2) AS value
FROM actual, budget""",
    },
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_term_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "term_id": row["term_id"],
        "term_code": row["term_code"],
        "name": row["name"],
        "display_name": row["display_name"],
        "description": row["description"],
        "synonyms": json.loads(row["synonyms"] or "[]"),
        "mapped_table": row["mapped_table"],
        "mapped_columns": json.loads(row["mapped_columns"] or "[]"),
        "category": row["category"],
        "certified": bool(row["certified"]),
        "certified_by": row["certified_by"],
        "certified_at": row["certified_at"],
        "source": row["source"],
        "updated_at": row["updated_at"],
    }


def _attribute_rows(conn: Any, term_id: int | None = None) -> list[dict[str, Any]]:
    if term_id is None:
        rows = conn.execute(
            """SELECT attribute_id, code, name, term_id, formula_sql, description, unit, certified
               FROM ontology_sql_attribute ORDER BY code"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT attribute_id, code, name, term_id, formula_sql, description, unit, certified
               FROM ontology_sql_attribute WHERE term_id = ? ORDER BY code""",
            (term_id,),
        ).fetchall()
    return [
        {
            "attribute_id": row["attribute_id"],
            "code": row["code"],
            "name": row["name"],
            "term_id": row["term_id"],
            "formula_sql": row["formula_sql"],
            "description": row["description"],
            "unit": row["unit"],
            "certified": bool(row["certified"]),
        }
        for row in rows
    ]


def _group_attributes(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row["term_id"] is not None:
            grouped.setdefault(row["term_id"], []).append(row)
    return grouped


def _counts(conn: Any) -> dict[str, int]:
    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    return {
        "terms": count("ontology_term"),
        "relations": count("ontology_relation"),
        "sql_attributes": count("ontology_sql_attribute"),
        "columns": count("ontology_term_column"),
    }


def _table_columns(conn: Any, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_identifier_safe(table)})").fetchall()
    return [row["name"] for row in rows]


def quote_identifier_safe(name: str) -> str:
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"非法标识符: {name}")
    return '"' + name.replace('"', '""') + '"'


def _sensitive_sample(conn: Any, table: str, column: str) -> str | None:
    row = conn.execute(
        f"SELECT {quote_identifier_safe(column)} AS value FROM {quote_identifier_safe(table)} "
        f"WHERE {quote_identifier_safe(column)} IS NOT NULL LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return mask_value(column, row["value"], "admin")


def _insert_columns(
    conn: Any,
    term_id: int,
    term: dict[str, Any],
) -> int:
    conn.execute("DELETE FROM ontology_term_column WHERE term_id = ?", (term_id,))
    table = term.get("mapped_table")
    columns = term.get("mapped_columns") or []
    inserted = 0
    for column in columns:
        is_sensitive = (table, column) in SENSITIVE_BY_TABLE
        classification = SENSITIVE_BY_TABLE.get((table, column), (None, ""))[0]
        sample = _sensitive_sample(conn, table, column) if is_sensitive else None
        conn.execute(
            """INSERT INTO ontology_term_column
               (term_id, table_name, column_name, display_name, description,
                is_sensitive, classification, sample_masked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                term_id,
                table,
                column,
                COLUMN_DESCRIPTIONS.get(f"{table}.{column}", column),
                COLUMN_DESCRIPTIONS.get(f"{table}.{column}", ""),
                1 if is_sensitive else 0,
                classification,
                sample,
            ),
        )
        inserted += 1
    return inserted


def _upsert_term(conn: Any, term: dict[str, Any], category: str, now: str) -> int:
    certified = 0 if category == "sensitive" else 1
    conn.execute(
        """INSERT INTO ontology_term
           (term_code, name, display_name, description, synonyms, mapped_table,
            mapped_columns, category, certified, certified_by, certified_at, source, updated_at)
           VALUES (:term_code, :name, :display_name, :description, :synonyms, :mapped_table,
                   :mapped_columns, :category, :certified, :certified_by, :certified_at, 'rule', :updated_at)
           ON CONFLICT(term_code) DO UPDATE SET
               name = excluded.name,
               display_name = excluded.display_name,
               description = excluded.description,
               synonyms = excluded.synonyms,
               mapped_table = excluded.mapped_table,
               mapped_columns = excluded.mapped_columns,
               category = excluded.category,
               source = 'rule',
               updated_at = excluded.updated_at""",
        {
            "term_code": term["term_code"],
            "name": term["name"],
            "display_name": term["display_name"],
            "description": term.get("description", ""),
            "synonyms": json.dumps(term.get("synonyms", []), ensure_ascii=False),
            "mapped_table": term.get("mapped_table"),
            "mapped_columns": json.dumps(term.get("mapped_columns", []), ensure_ascii=False),
            "category": category,
            "certified": certified,
            "certified_by": "system" if certified else None,
            "certified_at": now if certified else None,
            "updated_at": now,
        },
    )
    row = conn.execute(
        "SELECT term_id FROM ontology_term WHERE term_code = ?", (term["term_code"],)
    ).fetchone()
    return int(row["term_id"])


def _sensitive_column_names() -> list[str]:
    """敏感列清单唯一来源：security.SENSITIVE_BY_TABLE。"""
    return [f"{table}.{column}" for table, column in SENSITIVE_BY_TABLE]


def generate_ontology(force: bool = False) -> dict[str, Any]:
    """按规则生成 27 术语 / 16 关系 / 20 SQL 属性；upsert 保留认证状态。"""
    started = time.perf_counter()
    with write_connection() as conn:
        existing = int(conn.execute("SELECT COUNT(*) AS n FROM ontology_term").fetchone()["n"])
        if existing and not force:
            return {
                "status": "skipped",
                "counts": _counts(conn),
                "sensitive_columns": _sensitive_column_names(),
                "version": ONTOLOGY_VERSION,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }

        from app.services.query import guard_sql

        for attr in SQL_ATTRIBUTES:
            result = guard_sql(attr["formula_sql"])
            if not result.ok:
                raise AppError(
                    500,
                    "ONTOLOGY_GUARD_FAILED",
                    f"SQL 属性 {attr['code']} 未通过只读守卫: {result.reason}",
                )

        now = _now()
        term_ids: dict[str, int] = {}
        column_count = 0
        for category, terms in (
            ("table", TABLE_TERMS),
            ("metric", METRIC_TERMS),
            ("sensitive", SENSITIVE_TERMS),
        ):
            for term in terms:
                term_with_columns = dict(term)
                if category == "table":
                    term_with_columns["mapped_columns"] = _table_columns(conn, term["mapped_table"])
                elif category == "sensitive":
                    term_with_columns.setdefault("mapped_columns", [])
                term_id = _upsert_term(conn, term_with_columns, category, now)
                term_ids[term_with_columns["term_code"]] = term_id
                column_count += _insert_columns(conn, term_id, term_with_columns)

        for relation in RELATIONS:
            if relation["from_term"] not in term_ids or relation["to_term"] not in term_ids:
                continue
            conn.execute(
                """INSERT INTO ontology_relation
                   (relation_code, from_term, to_term, relation_type, cardinality, join_hint, description)
                   VALUES (:relation_code, :from_term, :to_term, :relation_type, :cardinality, :join_hint, :description)
                   ON CONFLICT(relation_code) DO UPDATE SET
                       from_term = excluded.from_term,
                       to_term = excluded.to_term,
                       relation_type = excluded.relation_type,
                       cardinality = excluded.cardinality,
                       join_hint = excluded.join_hint,
                       description = excluded.description""",
                relation,
            )

        for attr in SQL_ATTRIBUTES:
            term_id = term_ids.get(attr["term_code"])
            conn.execute(
                """INSERT INTO ontology_sql_attribute
                   (code, name, term_id, formula_sql, description, unit, certified)
                   VALUES (:code, :name, :term_id, :formula_sql, :description, :unit, 1)
                   ON CONFLICT(code) DO UPDATE SET
                       name = excluded.name,
                       term_id = excluded.term_id,
                       formula_sql = excluded.formula_sql,
                       description = excluded.description,
                       unit = excluded.unit""",
                {
                    "code": attr["code"],
                    "name": attr["name"],
                    "term_id": term_id,
                    "formula_sql": attr["formula_sql"],
                    "description": attr["description"],
                    "unit": attr["unit"],
                },
            )

        counts = _counts(conn)
        counts["columns"] = column_count
        return {
            "status": "generated",
            "counts": counts,
            "sensitive_columns": _sensitive_column_names(),
            "version": ONTOLOGY_VERSION,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def list_terms(
    q: str | None = None,
    certified: bool | None = None,
    category: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if q:
        clauses.append("(display_name LIKE :q OR name LIKE :q OR term_code LIKE :q OR synonyms LIKE :q)")
        params["q"] = f"%{q.strip()}%"
    if certified is not None:
        clauses.append("certified = :certified")
        params["certified"] = 1 if certified else 0
    if category:
        if category not in ALLOWED_CATEGORIES:
            raise AppError(422, "VALIDATION_FAILED", f"不支持的 category: {category}")
        clauses.append("category = :category")
        params["category"] = category
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, min(int(limit or 100), 200))
    params["limit"] = safe_limit

    from app.db.connection import connect

    conn = connect(read_only=True)
    try:
        rows = conn.execute(
            f"""SELECT term_id, term_code, name, display_name, description, synonyms,
                       mapped_table, mapped_columns, category, certified, certified_by,
                       certified_at, source, updated_at
                FROM ontology_term {where}
                ORDER BY category, term_id
                LIMIT :limit""",
            params,
        ).fetchall()
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM ontology_term {where}",
            {k: v for k, v in params.items() if k != "limit"},
        ).fetchone()
        attributes = _group_attributes(_attribute_rows(conn))
        items = []
        for row in rows:
            item = _parse_term_row(dict(row))
            item["sql_attributes"] = attributes.get(item["term_id"], [])
            items.append(item)
        return {"total": int(total_row["n"]), "items": items}
    finally:
        conn.close()


def _get_term_row(conn: Any, term_id: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT term_id, term_code, name, display_name, description, synonyms,
                  mapped_table, mapped_columns, category, certified, certified_by,
                  certified_at, source, updated_at
           FROM ontology_term WHERE term_id = ?""",
        (term_id,),
    ).fetchone()
    if not row:
        raise AppError(404, "NOT_FOUND", f"术语不存在: {term_id}")
    return dict(row)


def get_term(term_id: int, role: str = "viewer") -> dict[str, Any]:
    from app.db.connection import connect

    conn = connect(read_only=True)
    try:
        term = _parse_term_row(_get_term_row(conn, term_id))
        columns = []
        for row in conn.execute(
            """SELECT column_id, table_name, column_name, display_name, description,
                      is_sensitive, classification, sample_masked
               FROM ontology_term_column WHERE term_id = ? ORDER BY column_id""",
            (term_id,),
        ).fetchall():
            sample = row["sample_masked"]
            if sample is not None and row["is_sensitive"]:
                # 库内保存 admin 视角样本，详情接口按请求角色二次掩码
                sample = mask_value(row["column_name"], sample, role)
            columns.append(
                {
                    "column_id": row["column_id"],
                    "table_name": row["table_name"],
                    "column_name": row["column_name"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "is_sensitive": bool(row["is_sensitive"]),
                    "classification": row["classification"],
                    "sample_masked": sample,
                }
            )
        attributes = _attribute_rows(conn, term_id)
        change_log = [
            {
                "change_id": row["change_id"],
                "term_id": row["term_id"],
                "field_name": row["field_name"],
                "old_value": row["old_value"],
                "new_value": row["new_value"],
                "changed_by_role": row["changed_by_role"],
                "changed_at": row["changed_at"],
            }
            for row in conn.execute(
                """SELECT change_id, term_id, field_name, old_value, new_value,
                          changed_by_role, changed_at
                   FROM ontology_change_log WHERE term_id = ?
                   ORDER BY changed_at DESC, change_id DESC LIMIT 20""",
                (term_id,),
            ).fetchall()
        ]
        term["columns"] = columns
        term["sql_attributes"] = attributes
        term["change_log"] = change_log
        return term
    finally:
        conn.close()


def _validate_columns(table: str | None, columns: list[str]) -> None:
    if not table:
        raise AppError(422, "VALIDATION_FAILED", "mapped_columns 需要先设置 mapped_table")
    if table not in BUSINESS_TABLES:
        raise AppError(422, "VALIDATION_FAILED", f"mapped_table 不在业务表白名单: {table}")
    from app.db.connection import connect

    # PRAGMA table_info 属于 schema 内省，authorizer 会拒绝 PRAGMA；表名已先过白名单
    conn = connect(read_only=True, enforce_authorizer=False)
    try:
        valid = set(_table_columns(conn, table))
    finally:
        conn.close()
    invalid = [column for column in columns if column not in valid]
    if invalid:
        raise AppError(422, "VALIDATION_FAILED", f"mapped_columns 含非法列: {', '.join(invalid)}")


def update_term(term_id: int, payload: dict[str, Any], role: str) -> dict[str, Any]:
    unknown = set(payload) - UPDATE_FIELDS
    if unknown:
        raise AppError(422, "VALIDATION_FAILED", f"不允许修改字段: {', '.join(sorted(unknown))}")
    if not payload:
        raise AppError(422, "VALIDATION_FAILED", "至少提供一个待更新字段")

    normalized: dict[str, Any] = {}
    for field, value in payload.items():
        if field == "display_name":
            text = (value or "").strip()
            if not text:
                raise AppError(422, "VALIDATION_FAILED", "display_name 不能为空")
            normalized[field] = text
        elif field == "description":
            normalized[field] = (value or "").strip()
        elif field == "synonyms":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise AppError(422, "VALIDATION_FAILED", "synonyms 必须是字符串数组")
            normalized[field] = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        elif field == "mapped_table":
            normalized[field] = (value or "").strip() or None
            if normalized[field] and normalized[field] not in BUSINESS_TABLES:
                raise AppError(422, "VALIDATION_FAILED", f"mapped_table 不在业务表白名单: {normalized[field]}")
        elif field == "mapped_columns":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise AppError(422, "VALIDATION_FAILED", "mapped_columns 必须是字符串数组")
            normalized[field] = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    existing = _load_current_term(term_id)
    if "mapped_table" in normalized and "mapped_columns" not in normalized:
        new_table = normalized["mapped_table"]
        if new_table is None:
            # 清空映射时同时清空列，避免遗留旧列
            normalized["mapped_columns"] = []
        elif new_table != existing["mapped_table"]:
            raise AppError(
                422,
                "VALIDATION_FAILED",
                "修改 mapped_table 时必须同时提供 mapped_columns（空数组表示清空映射）",
            )
    if "mapped_columns" in normalized:
        columns = normalized["mapped_columns"]
        effective_table = (
            normalized["mapped_table"] if "mapped_table" in normalized else existing["mapped_table"]
        )
        # 空数组表示清空列映射，不校验列；非空才要求表存在且列合法
        if columns:
            _validate_columns(effective_table, columns)

    now = _now()
    with write_connection() as conn:
        current = _parse_term_row(_get_term_row(conn, term_id))
        changes: list[tuple[str, str | None, str | None]] = []
        for field, value in normalized.items():
            old = current.get(field)
            if field in ("synonyms", "mapped_columns"):
                old_value = json.dumps(old or [], ensure_ascii=False)
                new_value = json.dumps(value, ensure_ascii=False)
            else:
                old_value = old if old is not None else None
                new_value = value if value is not None else None
            if old_value == new_value:
                continue
            changes.append((field, old_value, new_value))
            conn.execute(
                f"UPDATE ontology_term SET {field} = :value, updated_at = :updated_at WHERE term_id = :term_id",
                {
                    "value": json.dumps(value, ensure_ascii=False) if field in ("synonyms", "mapped_columns") else value,
                    "updated_at": now,
                    "term_id": term_id,
                },
            )
        for field, old_value, new_value in changes:
            conn.execute(
                """INSERT INTO ontology_change_log
                   (term_id, field_name, old_value, new_value, changed_by_role, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (term_id, field, old_value, new_value, role, now),
            )
        if "mapped_table" in normalized or "mapped_columns" in normalized:
            merged = dict(current)
            for field in ("mapped_table", "mapped_columns"):
                if field in normalized:
                    merged[field] = normalized[field]
            # 同步列元数据，保证详情/敏感标记/样本与生效映射一致
            _insert_columns(conn, term_id, merged)
        updated = _parse_term_row(_get_term_row(conn, term_id))
        updated["sql_attributes"] = _attribute_rows(conn, term_id)
        return updated


def _load_current_term(term_id: int) -> dict[str, Any]:
    from app.db.connection import connect

    conn = connect(read_only=True)
    try:
        return _parse_term_row(_get_term_row(conn, term_id))
    finally:
        conn.close()


def certify_term(term_id: int, role: str, certified: bool = True) -> dict[str, Any]:
    now = _now()
    with write_connection() as conn:
        current = _parse_term_row(_get_term_row(conn, term_id))
        if current["certified"] == certified:
            current["sql_attributes"] = _attribute_rows(conn, term_id)
            return current
        conn.execute(
            """UPDATE ontology_term
               SET certified = ?, certified_by = ?, certified_at = ?, updated_at = ?
               WHERE term_id = ?""",
            (1 if certified else 0, role if certified else None, now if certified else None, now, term_id),
        )
        conn.execute(
            """INSERT INTO ontology_change_log
               (term_id, field_name, old_value, new_value, changed_by_role, changed_at)
               VALUES (?, 'certified', ?, ?, ?, ?)""",
            (term_id, str(current["certified"]).lower(), str(certified).lower(), role, now),
        )
        updated = _parse_term_row(_get_term_row(conn, term_id))
        updated["sql_attributes"] = _attribute_rows(conn, term_id)
        return updated


def list_relations() -> dict[str, Any]:
    rows = query_all(
        """SELECT r.relation_id, r.relation_code, r.from_term, r.to_term,
                  r.relation_type, r.cardinality, r.join_hint, r.description,
                  ft.display_name AS from_term_name, tt.display_name AS to_term_name
           FROM ontology_relation r
           LEFT JOIN ontology_term ft ON ft.term_code = r.from_term
           LEFT JOIN ontology_term tt ON tt.term_code = r.to_term
           ORDER BY r.relation_id"""
    )
    items = [
        {
            "relation_id": row["relation_id"],
            "relation_code": row["relation_code"],
            "from_term": row["from_term_name"] or row["from_term"],
            "from_term_code": row["from_term"],
            "to_term": row["to_term_name"] or row["to_term"],
            "to_term_code": row["to_term"],
            "relation_type": row["relation_type"],
            "cardinality": row["cardinality"],
            "join_hint": row["join_hint"],
            "description": row["description"],
        }
        for row in rows
    ]
    return {"items": items}


def list_sql_attributes() -> dict[str, Any]:
    from app.db.connection import connect

    conn = connect(read_only=True)
    try:
        return {"items": _attribute_rows(conn)}
    finally:
        conn.close()
