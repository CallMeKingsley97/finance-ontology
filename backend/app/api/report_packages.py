"""报表包 API：模板、生成与 Excel 导出。"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api import require_report_role
from app.schemas.common import AppError, ok
from app.schemas.report_package import ReportPackageRequest
from app.services.excel_export import build_package_workbook
from app.services.report_packages import generate_package, list_package_templates

router = APIRouter(prefix="/api/report-packages", tags=["report-packages"])

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@router.get("/templates")
def report_package_templates() -> dict:
    return ok(list_package_templates())


@router.post("/generate")
def report_package_generate(request: Request, payload: ReportPackageRequest) -> dict:
    role = require_report_role(request)
    return ok(
        generate_package(
            period_code=payload.period,
            template_codes=payload.templates or [],
            comparison=payload.comparison,
            role=role,
        )
    )


@router.post("/export")
def report_package_export(request: Request, payload: ReportPackageRequest) -> Response:
    role = require_report_role(request)
    package = generate_package(
        period_code=payload.period,
        template_codes=payload.templates or [],
        comparison=payload.comparison,
        role=role,
    )
    try:
        content = build_package_workbook(package, role)
    except Exception as exc:  # noqa: BLE001 - 导出失败统一为业务错误
        raise AppError(500, "EXPORT_FAILED", "Excel 导出失败", {"error": str(exc)}) from exc
    period_code = str((package.get("period") or {}).get("code") or "report")
    filename = f"report-package-{period_code}.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
