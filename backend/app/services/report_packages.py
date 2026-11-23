"""报表包：3 个模板、质量前置、脱敏、勾稽与附录组装。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from app.db.connection import query_all
from app.schemas.common import AppError
from app.services import metrics, quality, reports
from app.services.ontology import ONTOLOGY_VERSION
from app.services.query import format_amount, format_pct, format_ratio
from app.services.security import mask_rows

PACKAGE_TEMPLATES: list[dict[str, Any]] = [
    {
        "code": "balance_sheet",
        "name": "资产负债表",
        "description": "资产、负债、权益、偿债能力与结构分析。",
        "sections": [
            {"key": "summary", "title": "核心指标", "type": "kpi"},
            {"key": "balance_table", "title": "资产负债表明细", "type": "table"},
            {"key": "structure_chart", "title": "资产/负债/权益构成", "type": "chart"},
            {"key": "structure_text", "title": "结构说明", "type": "text"},
        ],
    },
    {
        "code": "profit_statement",
        "name": "利润表",
        "description": "收入、成本、费用、利润形成与趋势分析。",
        "sections": [
            {"key": "summary", "title": "核心指标", "type": "kpi"},
            {"key": "pnl_table", "title": "利润表明细", "type": "table"},
            {"key": "profit_chart", "title": "利润形成", "type": "chart"},
            {"key": "trend_chart", "title": "近 6 期收入与毛利率", "type": "chart"},
        ],
    },
    {
        "code": "management_report",
        "name": "管理报表",
        "description": "经营 KPI、预算执行、营运指标与质量摘要。",
        "sections": [
            {"key": "summary", "title": "经营 KPI", "type": "kpi"},
            {"key": "budget_table", "title": "预算执行", "type": "table"},
            {"key": "operation_table", "title": "营运指标", "type": "table"},
            {"key": "trend_chart", "title": "近 12 期收入与净利润", "type": "chart"},
            {"key": "quality_text", "title": "数据质量摘要", "type": "text"},
        ],
    },
]

_TEMPLATE_MAP = {item["code"]: item for item in PACKAGE_TEMPLATES}
_COMPARISONS = ("yoy", "mom", "none")
_DEFAULT_ASSUMPTIONS = [
    "默认合并全部主体（制造主体 + 贸易主体）",
    "金额单位为人民币元，比率以小数返回（0.25 = 25%）",
    "数据来自本地 SQLite mock 数据，由固定随机种子生成",
    "报表包由规则引擎组装，未使用外部服务",
]
_APPENDIX_CODES = [
    "revenue_total",
    "cogs_total",
    "gross_profit",
    "gross_margin",
    "period_expense",
    "operating_profit",
    "total_profit",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "debt_asset_ratio",
    "current_ratio",
    "receivable_total",
    "inventory_total",
    "budget_variance",
    "budget_achievement_rate",
    "operating_cash_flow",
    "expense_ratio",
    "active_customer_count",
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


def _pct_change(current: Any, previous: Any) -> float | None:
    try:
        current_value = float(current)
        previous_value = float(previous)
    except (TypeError, ValueError):
        return None
    if previous_value == 0:
        return None
    return round((current_value - previous_value) / abs(previous_value), 4)


def _diff(current: Any, previous: Any) -> float | None:
    try:
        return round(float(current) - float(previous), 2)
    except (TypeError, ValueError):
        return None


def list_package_templates() -> dict[str, Any]:
    return {"items": PACKAGE_TEMPLATES, "periods": reports.list_templates()["periods"]}


def _kpi_from(
    key: str,
    label: str,
    value: float,
    unit: str,
    previous: float | None = None,
    *,
    absolute_change: bool = False,
) -> dict[str, Any]:
    if absolute_change:
        change = round(value - previous, 4) if previous is not None else None
    else:
        change = _pct_change(value, previous)
    return reports.kpi_item(key, label, value, unit, change)


def _period_value(source: str, period_code: str | None) -> dict[str, float] | None:
    if not period_code:
        return None
    if source == "balance":
        return reports.balance_values(period_code)
    return reports.pnl_values(period_code)


def _balance_sheet(
    period_code: str, comparison_code: str | None
) -> list[dict[str, Any]]:
    balance = reports.balance_values(period_code)
    previous = _period_value("balance", comparison_code)
    kpis = [
        _kpi_from("total_assets", "资产总额", balance["total_assets"], "CNY", previous["total_assets"] if previous else None),
        _kpi_from("total_liabilities", "负债总额", balance["total_liabilities"], "CNY", previous["total_liabilities"] if previous else None),
        _kpi_from("total_equity", "所有者权益", balance["total_equity"], "CNY", previous["total_equity"] if previous else None),
        _kpi_from("debt_asset_ratio", "资产负债率", balance["debt_asset_ratio"], "%", previous["debt_asset_ratio"] if previous else None, absolute_change=True),
        _kpi_from("current_ratio", "流动比率", balance["current_ratio"], "倍", previous["current_ratio"] if previous else None),
        _kpi_from("receivable_total", "应收账款", balance["receivable_total"], "CNY", previous["receivable_total"] if previous else None),
        _kpi_from("inventory_total", "存货", balance["inventory_total"], "CNY", previous["inventory_total"] if previous else None),
    ]
    items = [
        ("total_assets", "资产总额"),
        ("current_assets", "流动资产"),
        ("non_current_assets", "非流动资产"),
        ("total_liabilities", "负债总额"),
        ("current_liabilities", "流动负债"),
        ("non_current_liabilities", "非流动负债"),
        ("total_equity", "所有者权益"),
        ("receivable_total", "应收账款"),
        ("inventory_total", "存货"),
    ]
    rows = [
        {
            "item": label,
            "current": round(balance[key], 2),
            "comparison": round(previous[key], 2) if previous else None,
            "change": _diff(balance[key], previous[key]) if previous else None,
            "change_pct": _pct_change(balance[key], previous[key]) if previous else None,
        }
        for key, label in items
    ]
    columns = [
        {"key": "item", "label": "项目", "type": "string"},
        {"key": "current", "label": "期末余额", "type": "number"},
        {"key": "comparison", "label": "对比期余额", "type": "number"},
        {"key": "change", "label": "变动额", "type": "number"},
        {"key": "change_pct", "label": "变动率", "type": "number"},
    ]
    chart = reports.chart_block(
        "bar",
        "label",
        ["资产总额", "负债总额", "所有者权益"],
        [
            (
                "金额",
                [
                    balance["total_assets"],
                    balance["total_liabilities"],
                    balance["total_equity"],
                ],
            )
        ],
    )
    text = (
        f"{reports.period_label(period_code)}资产总额 {format_amount(balance['total_assets'])}，"
        f"负债总额 {format_amount(balance['total_liabilities'])}，"
        f"资产负债率 {format_pct(balance['debt_asset_ratio'])}，"
        f"流动比率 {format_ratio(balance['current_ratio'])}。"
    )
    return [
        reports.section_block("summary", "核心指标", "kpi", data={"items": kpis}),
        reports.section_block(
            "balance_table",
            "资产负债表明细",
            "table",
            table=reports.table_block(columns, rows),
        ),
        reports.section_block("structure_chart", "资产/负债/权益构成", "chart", chart=chart),
        reports.section_block("structure_text", "结构说明", "text", text=text),
    ]


def _profit_statement(
    period_code: str, comparison_code: str | None
) -> list[dict[str, Any]]:
    pnl = reports.pnl_values(period_code)
    previous = _period_value("pnl", comparison_code)
    operating_profit = round(
        pnl["revenue"] - pnl["cogs"] - pnl["tax_surcharge"] - pnl["period_expense"], 2
    )
    previous_operating = None
    if previous:
        previous_operating = round(
            previous["revenue"]
            - previous["cogs"]
            - previous["tax_surcharge"]
            - previous["period_expense"],
            2,
        )
    kpis = [
        _kpi_from("revenue", "营业收入", pnl["revenue"], "CNY", previous["revenue"] if previous else None),
        _kpi_from("cogs", "营业成本", pnl["cogs"], "CNY", previous["cogs"] if previous else None),
        _kpi_from("gross_profit", "毛利", pnl["gross_profit"], "CNY", previous["gross_profit"] if previous else None),
        _kpi_from("gross_margin", "毛利率", pnl["gross_margin"], "%", previous["gross_margin"] if previous else None, absolute_change=True),
        _kpi_from("operating_profit", "营业利润", operating_profit, "CNY", previous_operating),
        _kpi_from("net_profit", "净利润", pnl["net_profit"], "CNY", previous["net_profit"] if previous else None),
    ]
    items = [
        ("revenue", "营业收入"),
        ("cogs", "营业成本"),
        ("gross_profit", "毛利"),
        ("tax_surcharge", "税金及附加"),
        ("period_expense", "期间费用"),
        ("operating_profit", "营业利润"),
        ("nonop_income", "营业外收入"),
        ("nonop_expense", "营业外支出"),
        ("total_profit", "利润总额"),
        ("income_tax", "所得税费用"),
        ("net_profit", "净利润"),
    ]
    current_map = {**pnl, "operating_profit": operating_profit}
    previous_map = {**previous, "operating_profit": previous_operating} if previous else {}
    rows = [
        {
            "item": label,
            "current": round(current_map[key], 2),
            "comparison": round(previous_map[key], 2) if previous else None,
            "change": _diff(current_map[key], previous_map.get(key)) if previous else None,
        }
        for key, label in items
    ]
    columns = [
        {"key": "item", "label": "项目", "type": "string"},
        {"key": "current", "label": "本期金额", "type": "number"},
        {"key": "comparison", "label": "对比期金额", "type": "number"},
        {"key": "change", "label": "变动额", "type": "number"},
    ]
    chart = reports.chart_block(
        "bar",
        "label",
        ["营业收入", "营业成本", "期间费用", "净利润"],
        [
            (
                "金额",
                [
                    pnl["revenue"],
                    pnl["cogs"],
                    pnl["period_expense"],
                    pnl["net_profit"],
                ],
            )
        ],
    )
    trend_start = _add_months(period_code, -5)
    trend_rows = reports.income_series(trend_start, period_code)
    labels = [row["period"] for row in trend_rows]
    margins = [
        round((float(row["revenue"]) - float(row["cogs"])) / float(row["revenue"]), 4)
        if float(row["revenue"])
        else 0.0
        for row in trend_rows
    ]
    trend_chart = reports.chart_block(
        "line",
        "period",
        labels,
        [
            ("营业收入", [float(row["revenue"]) for row in trend_rows]),
            ("毛利率", margins),
        ],
    )
    return [
        reports.section_block("summary", "核心指标", "kpi", data={"items": kpis}),
        reports.section_block(
            "pnl_table", "利润表明细", "table", table=reports.table_block(columns, rows)
        ),
        reports.section_block("profit_chart", "利润形成", "chart", chart=chart),
        reports.section_block("trend_chart", "近 6 期收入与毛利率", "chart", chart=trend_chart),
    ]


def _profit_series(start_code: str, end_code: str) -> list[dict[str, Any]]:
    return query_all(
        """SELECT p.period_code AS period,
                  ROUND(SUM(CASE WHEN a.account_type = 'revenue'
                                 THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS revenue,
                  ROUND(SUM(CASE WHEN a.account_type = 'cogs'
                                 THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS cogs,
                  ROUND(SUM(CASE WHEN a.account_type = 'tax_surcharge'
                                 THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS tax_surcharge,
                  ROUND(SUM(CASE WHEN a.account_type = 'expense'
                                 THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS period_expense,
                  ROUND(SUM(CASE WHEN a.account_code = '6301'
                                 THEN b.credit_amount - b.debit_amount ELSE 0 END), 2) AS nonop_income,
                  ROUND(SUM(CASE WHEN a.account_code = '6711'
                                 THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS nonop_expense,
                  ROUND(SUM(CASE WHEN a.account_code = '6801'
                                 THEN b.debit_amount - b.credit_amount ELSE 0 END), 2) AS income_tax
           FROM fact_balance b
           JOIN dim_account a ON a.account_id = b.account_id
           JOIN dim_period p ON p.period_id = b.period_id
           WHERE a.account_type IN ('revenue', 'cogs', 'tax_surcharge', 'expense',
                                    'non_operating', 'income_tax')
             AND p.period_code BETWEEN :start_period AND :end_period
           GROUP BY p.period_code
           ORDER BY p.period_code""",
        {"start_period": start_code, "end_period": end_code},
    )


def _metric_value(code: str, period_code: str) -> tuple[float | None, str | None]:
    try:
        execution = metrics.execute_metric(code, period_code)
        return float(execution["value"]), None
    except Exception as exc:  # noqa: BLE001 - 指标不可用时降级为告警
        return None, f"指标 {code} 执行失败：{str(exc)[:120]}"


def _management_report(
    period_code: str, comparison_code: str | None, quality_run: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    pnl = reports.pnl_values(period_code)
    balance = reports.balance_values(period_code)
    warnings: list[str] = []
    budget_rows = reports.budget_rows(period_code)
    budget_total = sum(float(row["budget_amount"] or 0) for row in budget_rows)
    actual_total = sum(float(row["actual_amount"] or 0) for row in budget_rows)
    fallback_rate = round(actual_total / budget_total, 4) if budget_total else None

    budget_rate, warning = _metric_value("budget_achievement_rate", period_code)
    if warning:
        warnings.append(warning)
        budget_rate = fallback_rate
    active_customers, warning = _metric_value("active_customer_count", period_code)
    if warning:
        warnings.append(warning)
    cash_flow, warning = _metric_value("operating_cash_flow", period_code)
    if warning:
        warnings.append(warning)
    expense_ratio, warning = _metric_value("expense_ratio", period_code)
    if warning:
        warnings.append(warning)

    previous = _period_value("pnl", comparison_code)
    kpis = [
        _kpi_from("revenue", "营业收入", pnl["revenue"], "CNY", previous["revenue"] if previous else None),
        _kpi_from("period_expense", "期间费用", pnl["period_expense"], "CNY", previous["period_expense"] if previous else None),
        _kpi_from("net_profit", "净利润", pnl["net_profit"], "CNY", previous["net_profit"] if previous else None),
        _kpi_from("budget_achievement_rate", "预算达成率", budget_rate or 0.0, "%", None, absolute_change=True),
        _kpi_from("receivable_total", "应收账款", balance["receivable_total"], "CNY", None),
        _kpi_from("active_customer_count", "活跃客户数", active_customers or 0.0, "家", None),
    ]
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
    budget_columns = [
        {"key": "item", "label": "科目", "type": "string"},
        {"key": "budget", "label": "预算金额", "type": "number"},
        {"key": "actual", "label": "实际金额", "type": "number"},
        {"key": "variance", "label": "差异", "type": "number"},
        {"key": "achievement_rate", "label": "达成率", "type": "number"},
    ]
    operation_rows = [
        {"item": "应收账款", "value": balance["receivable_total"], "unit": "CNY"},
        {"item": "存货", "value": balance["inventory_total"], "unit": "CNY"},
        {"item": "经营现金净流入", "value": cash_flow, "unit": "CNY"},
        {"item": "活跃客户数", "value": active_customers, "unit": "家"},
        {"item": "费用率", "value": expense_ratio, "unit": "%"},
    ]
    operation_columns = [
        {"key": "item", "label": "指标", "type": "string"},
        {"key": "value", "label": "数值", "type": "number"},
        {"key": "unit", "label": "单位", "type": "string"},
    ]
    trend_start = _add_months(period_code, -11)
    trend_rows = _profit_series(trend_start, period_code)
    net_profits = [
        round(
            float(row["revenue"])
            - float(row["cogs"])
            - float(row["tax_surcharge"])
            - float(row["period_expense"])
            + float(row["nonop_income"])
            - float(row["nonop_expense"])
            - float(row["income_tax"]),
            2,
        )
        for row in trend_rows
    ]
    trend_chart = reports.chart_block(
        "line",
        "period",
        [row["period"] for row in trend_rows],
        [
            ("营业收入", [float(row["revenue"]) for row in trend_rows]),
            ("净利润", net_profits),
        ],
    )

    if quality_run:
        failed = [
            item["rule_name"]
            for item in quality_run.get("results", [])
            if item.get("status") in ("failed", "error")
        ]
        quality_text = (
            f"质量检查 {quality_run.get('run_id')}：{quality_run.get('status')}，"
            f"共 {quality_run.get('rules_total', 0)} 条规则，"
            f"通过 {quality_run.get('rules_passed', 0)} 条，"
            f"失败 {quality_run.get('rules_failed', 0)} 条，"
            f"跳过 {quality_run.get('rules_skipped', 0)} 条。"
        )
        if failed:
            quality_text += f" 失败规则：{'、'.join(failed)}。"
        if quality_run.get("error_message"):
            quality_text += f" 执行错误：{quality_run['error_message']}。"
    else:
        quality_text = "质量检查未完成，报表包基于已计算数据生成。"

    sections = [
        reports.section_block("summary", "经营 KPI", "kpi", data={"items": kpis}),
        reports.section_block(
            "budget_table",
            "预算执行",
            "table",
            table=reports.table_block(budget_columns, budget_table_rows),
        ),
        reports.section_block(
            "operation_table",
            "营运指标",
            "table",
            table=reports.table_block(operation_columns, operation_rows),
        ),
        reports.section_block("trend_chart", "近 12 期收入与净利润", "chart", chart=trend_chart),
        reports.section_block("quality_text", "数据质量摘要", "text", text=quality_text),
    ]
    return sections, warnings


def _mask_sections(
    sections: list[dict[str, Any]], role: str
) -> tuple[list[dict[str, Any]], list[str]]:
    masked_columns: set[str] = set()
    for section in sections:
        table = section.get("table")
        if not table:
            continue
        column_names = [
            str(column.get("key") or column.get("name") or "")
            for column in table.get("columns", [])
        ]
        masked_rows, masked = mask_rows(column_names, table.get("rows", []), role)
        table["rows"] = masked_rows
        masked_columns.update(masked)
    return sections, sorted(masked_columns)


def _package_appendix(codes: list[str], assumptions: list[str]) -> tuple[dict[str, Any], str | None]:
    try:
        items = metrics.appendix_metrics(codes)
    except Exception:  # noqa: BLE001
        items = []
    if items:
        return {
            "metrics": items,
            "assumptions": assumptions,
            "ontology_version": ONTOLOGY_VERSION,
        }, None
    fallback = reports._appendix(codes, assumptions)
    mapped = [
        {
            "code": item.get("code"),
            "name": item.get("name"),
            "definition": item.get("formula") or "",
            "formula_text": item.get("formula") or "",
            "formula_sql": item.get("sql") or "",
            "boundary": {},
            "owner": "",
            "unit": item.get("unit") or "",
            "version": None,
            "certified_by": None,
            "certified_at": None,
        }
        for item in fallback.get("metrics", [])
    ]
    return {
        "metrics": mapped,
        "assumptions": assumptions,
        "ontology_version": ONTOLOGY_VERSION,
    }, "指标注册表不可用，附录已回退到本体 SQL 属性目录"


def generate_package(
    period_code: str,
    template_codes: list[str],
    comparison: str,
    role: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    if role == "viewer":
        raise AppError(403, "FORBIDDEN", "viewer 角色不能生成报表包")
    if comparison not in _COMPARISONS:
        raise AppError(422, "VALIDATION_FAILED", "comparison 仅支持 yoy/mom/none")
    if not reports._period_exists(period_code):
        raise AppError(422, "VALIDATION_FAILED", f"期间不存在: {period_code}")
    codes = list(dict.fromkeys(template_codes or [item["code"] for item in PACKAGE_TEMPLATES]))
    invalid = [code for code in codes if code not in _TEMPLATE_MAP]
    if invalid:
        raise AppError(404, "REPORT_PACKAGE_TEMPLATE_NOT_FOUND", f"报表包模板不存在: {', '.join(invalid)}")
    if not codes:
        raise AppError(422, "VALIDATION_FAILED", "至少选择一个报表包模板")

    comparison_code = reports.comparison_code(period_code, comparison)
    warnings: list[str] = []
    quality_run: dict[str, Any] | None = None
    try:
        quality_run = quality.run_quality(
            trigger_type="report", period_code=period_code, role=role
        )
    except Exception:  # noqa: BLE001 - 质量检查失败降级生成
        warnings.append("质量检查执行失败，报表包基于已计算数据生成")
    if quality_run:
        if int(quality_run.get("blocker_failed") or 0) > 0:
            failed = [
                item["rule_name"]
                for item in quality_run.get("results", [])
                if item.get("severity") == "blocker"
                and item.get("status") in ("failed", "error")
            ]
            raise AppError(
                409,
                "QUALITY_BLOCKER_FAILED",
                "blocker 质量规则未通过，报表包停止生成",
                {"run_id": quality_run.get("run_id"), "failed": failed},
            )
        failed_names = [
            item["rule_name"]
            for item in quality_run.get("results", [])
            if item.get("status") in ("failed", "error")
        ]
        if failed_names:
            warnings.append(f"质量规则未通过：{', '.join(failed_names)}")
        if quality_run.get("status") == "failed":
            warnings.append("质量检查执行失败，报表包基于已计算数据生成")

    templates: list[dict[str, Any]] = []
    mask_set: set[str] = set()
    for code in codes:
        if code == "balance_sheet":
            sections = _balance_sheet(period_code, comparison_code)
        elif code == "profit_statement":
            sections = _profit_statement(period_code, comparison_code)
        else:
            sections, extra_warnings = _management_report(
                period_code, comparison_code, quality_run
            )
            warnings.extend(extra_warnings)
        sections, masked = _mask_sections(sections, role)
        mask_set.update(masked)
        templates.append(
            {
                "code": code,
                "name": _TEMPLATE_MAP[code]["name"],
                "sections": sections,
            }
        )

    assumptions = [f"报表包期间：{reports.period_label(period_code)}", *_DEFAULT_ASSUMPTIONS]
    appendix, appendix_warning = _package_appendix(_APPENDIX_CODES, assumptions)
    if appendix_warning:
        warnings.append(appendix_warning)
    reconciliation = reports.check_reconciliation(period_code)
    if not reconciliation["passed"]:
        failed = [item["name"] for item in reconciliation["checks"] if not item["passed"]]
        warnings.append(f"勾稽校验未通过：{', '.join(failed)}")
    elapsed = int((time.perf_counter() - started) * 1000)
    if quality_run:
        quality_summary = {
            "run_id": quality_run.get("run_id"),
            "status": quality_run.get("status"),
            "rules_total": quality_run.get("rules_total", 0),
            "rules_passed": quality_run.get("rules_passed", 0),
            "rules_failed": quality_run.get("rules_failed", 0),
            "rules_skipped": quality_run.get("rules_skipped", 0),
            "blocker_failed": quality_run.get("blocker_failed", 0),
            "results": quality_run.get("results", []),
        }
    else:
        quality_summary = {
            "run_id": None,
            "status": "failed",
            "rules_total": 0,
            "rules_passed": 0,
            "rules_failed": 0,
            "rules_skipped": 0,
            "blocker_failed": 0,
            "results": [],
        }
    return {
        "package_code": f"pkg_{period_code}_{uuid.uuid4().hex[:8]}",
        "title": f"{reports.period_label(period_code)}月度报表包",
        "period": {
            "code": period_code,
            "label": reports.period_label(period_code),
            "comparison": comparison_code,
        },
        "generated_at": _now(),
        "templates": templates,
        "quality": quality_summary,
        "reconciliation": reconciliation,
        "appendix": appendix,
        "warnings": warnings,
        "meta": {
            "role": role,
            "masked_columns": sorted(mask_set),
            "duration_ms": elapsed,
            "ontology_version": ONTOLOGY_VERSION,
            "engine": "rule",
        },
    }
