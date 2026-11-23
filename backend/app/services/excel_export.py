"""报表包 Excel 导出：5 个 sheet，只读已脱敏的报表包文档。"""

from __future__ import annotations

from io import BytesIO
from typing import Any

EXCEL_MAX_ROWS_PER_SHEET = 2000
EXCEL_MAX_COLUMNS = 50
_SHEET_NAMES = {
    "balance_sheet": "资产负债表",
    "profit_statement": "利润表",
    "management_report": "管理报表",
}


def _safe_cell(value: Any) -> Any:
    """把值转换为可安全写入的单元格值，阻止公式注入。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text[:32767]


class _SheetWriter:
    def __init__(self, worksheet: Any) -> None:
        self.ws = worksheet
        self.row = 1
        self.truncated = False

    def write(self, values: list[Any]) -> None:
        if len(values) > EXCEL_MAX_COLUMNS:
            values = values[:EXCEL_MAX_COLUMNS]
            self.truncated = True
        if self.row > EXCEL_MAX_ROWS_PER_SHEET:
            self.truncated = True
            return
        for column, value in enumerate(values, start=1):
            self.ws.cell(row=self.row, column=column, value=_safe_cell(value))
        self.row += 1

    def finish(self) -> None:
        if not self.truncated:
            return
        note_row = EXCEL_MAX_ROWS_PER_SHEET + 1
        self.ws.cell(row=note_row, column=1, value="已截断，完整数据请在系统查看")


def _write_template_sheet(
    ws: Any, template: dict[str, Any] | None, code: str
) -> None:
    writer = _SheetWriter(ws)
    if template is None:
        writer.write(["本次未选择该模板"])
        writer.finish()
        return
    writer.write([template.get("name") or _SHEET_NAMES[code]])
    writer.write([])
    for section in template.get("sections", []):
        writer.write([section.get("title") or section.get("key") or ""])
        section_type = section.get("type")
        if section_type == "kpi":
            items = (section.get("data") or {}).get("items", [])
            writer.write(["项目", "数值", "单位", "展示值", "变化"])
            for item in items:
                writer.write(
                    [
                        item.get("label"),
                        item.get("value"),
                        item.get("unit"),
                        item.get("formatted"),
                        item.get("change_pct"),
                    ]
                )
        elif section_type == "table":
            table = section.get("table") or {}
            columns = table.get("columns") or []
            writer.write([column.get("label") or column.get("key") for column in columns])
            keys = [column.get("key") or column.get("name") for column in columns]
            for row in table.get("rows") or []:
                writer.write([row.get(key) for key in keys])
        elif section_type == "chart":
            writer.write(["图表见系统预览"])
            labels = (section.get("chart") or {}).get("labels") or []
            if labels:
                writer.write(["横轴", *labels])
        elif section_type == "text":
            text = section.get("text") or ""
            for line in str(text).splitlines() or [""]:
                writer.write([line])
        else:
            writer.write(["该章节请在系统预览中查看"])
        writer.write([])
    writer.finish()


def build_package_workbook(package: dict[str, Any], role: str) -> bytes:
    """把报表包文档写成 xlsx 字节；不重新查库、不写公式与外链。"""
    from openpyxl import Workbook

    workbook = Workbook()
    cover = workbook.active
    cover.title = "封面"
    balance_sheet = workbook.create_sheet("资产负债表")
    profit_statement = workbook.create_sheet("利润表")
    management_report = workbook.create_sheet("管理报表")
    reconciliation_sheet = workbook.create_sheet("勾稽校验")

    templates = {item.get("code"): item for item in package.get("templates", [])}
    quality = package.get("quality") or {}
    period = package.get("period") or {}
    meta = package.get("meta") or {}
    appendix = package.get("appendix") or {}

    cover_writer = _SheetWriter(cover)
    cover_writer.write([package.get("title") or "报表包"])
    cover_writer.write(["报表包编码", package.get("package_code")])
    cover_writer.write(["期间", period.get("code")])
    cover_writer.write(["期间标签", period.get("label")])
    cover_writer.write(["对比期", period.get("comparison") or "无"])
    cover_writer.write(["生成时间", package.get("generated_at")])
    cover_writer.write(["生成角色", role])
    cover_writer.write([])
    cover_writer.write(["质量状态", quality.get("status")])
    cover_writer.write(["质量运行", quality.get("run_id") or "无"])
    cover_writer.write(["规则总数", quality.get("rules_total")])
    cover_writer.write(["规则通过", quality.get("rules_passed")])
    cover_writer.write(["规则失败", quality.get("rules_failed")])
    cover_writer.write(["规则跳过", quality.get("rules_skipped")])
    cover_writer.write([])
    cover_writer.write(["警告"])
    for warning in package.get("warnings", []) or ["无"]:
        cover_writer.write([warning])
    cover_writer.write([])
    cover_writer.write(["已掩码列", ", ".join(meta.get("masked_columns", [])) or "无"])
    cover_writer.write(["口径说明"])
    for assumption in appendix.get("assumptions", []):
        cover_writer.write([assumption])
    cover_writer.write(["附录指标数", len(appendix.get("metrics", []))])
    cover_writer.finish()

    _write_template_sheet(balance_sheet, templates.get("balance_sheet"), "balance_sheet")
    _write_template_sheet(profit_statement, templates.get("profit_statement"), "profit_statement")
    _write_template_sheet(management_report, templates.get("management_report"), "management_report")

    recon_writer = _SheetWriter(reconciliation_sheet)
    reconciliation = package.get("reconciliation") or {}
    recon_writer.write(["勾稽校验"])
    recon_writer.write(["校验项", "左值", "右值", "差异", "结果"])
    for check in reconciliation.get("checks", []):
        recon_writer.write(
            [
                check.get("name"),
                check.get("left"),
                check.get("right"),
                check.get("diff"),
                "通过" if check.get("passed") else "失败",
            ]
        )
    recon_writer.write([])
    recon_writer.write(["总体结果", "通过" if reconciliation.get("passed") else "失败"])
    recon_writer.finish()

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
