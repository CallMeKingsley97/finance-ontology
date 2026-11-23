"""财务报告 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import require_report_role
from app.schemas.common import ok
from app.schemas.report import GenerateReportRequest
from app.services.reports import generate_report, list_templates

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/templates")
def report_templates() -> dict:
    return ok(list_templates())


@router.post("/generate")
def report_generate(request: Request, payload: GenerateReportRequest) -> dict:
    role = require_report_role(request)
    return ok(
        generate_report(
            template_code=payload.template_code,
            period_code=payload.period,
            comparison=payload.comparison,
            role=role,
        )
    )
