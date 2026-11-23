"""质量规则 API：规则 CRUD、运行、历史与告警。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import role_from_request
from app.schemas.common import AppError, ok
from app.schemas.quality import (
    QualityRuleCreateRequest,
    QualityRuleRunRequest,
    QualityRuleUpdateRequest,
    QualityRunRequest,
)
from app.services import quality

router = APIRouter(prefix="/api/quality", tags=["quality"])


def _require_editor(request: Request) -> str:
    role = role_from_request(request)
    if role == "viewer":
        raise AppError(403, "FORBIDDEN", "viewer 角色不能修改或执行质量规则")
    return role


@router.get("/summary")
def quality_summary() -> dict:
    return ok(quality.summary())


@router.get("/rules")
def quality_rules(
    enabled: str | None = None,
    rule_type: str | None = None,
    target_db: str | None = None,
    q: str | None = None,
) -> dict:
    return ok(
        quality.list_rules(
            {
                "enabled": enabled,
                "rule_type": rule_type,
                "target_db": target_db,
                "q": q,
            }
        )
    )


@router.post("/rules", status_code=201)
def quality_rule_create(request: Request, payload: QualityRuleCreateRequest) -> dict:
    role = _require_editor(request)
    return ok(quality.create_rule(payload.model_dump(), role))


@router.patch("/rules/{code}")
def quality_rule_update(
    code: str, request: Request, payload: QualityRuleUpdateRequest
) -> dict:
    role = _require_editor(request)
    return ok(quality.update_rule(code, payload.model_dump(exclude_unset=True), role))


@router.post("/rules/{code}/run")
def quality_rule_run(
    code: str, request: Request, payload: QualityRuleRunRequest | None = None
) -> dict:
    role = _require_editor(request)
    rule = quality.get_rule(code)
    if not rule["enabled"]:
        raise AppError(404, "QUALITY_RULE_NOT_FOUND", f"质量规则不存在或未启用: {code}")
    period = payload.period if payload else None
    return ok(
        quality.run_quality(
            trigger_type="manual",
            period_code=period,
            role=role,
            rule_codes=[code],
        )
    )


@router.post("/run")
def quality_run(request: Request, payload: QualityRunRequest | None = None) -> dict:
    role = _require_editor(request)
    period = payload.period if payload else None
    rule_codes = payload.rule_codes if payload else None
    return ok(
        quality.run_quality(
            trigger_type="manual",
            period_code=period,
            role=role,
            rule_codes=rule_codes,
        )
    )


@router.get("/runs")
def quality_runs(
    status: str | None = None,
    trigger_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    return ok(
        quality.list_runs(
            {
                "status": status,
                "trigger_type": trigger_type,
                "limit": limit,
                "offset": offset,
            }
        )
    )


@router.get("/runs/{run_id}")
def quality_run_detail(run_id: str) -> dict:
    return ok(quality.get_run(run_id))


@router.get("/alerts")
def quality_alerts(days: int = 7, limit: int = 20) -> dict:
    return ok(quality.list_alerts(days, limit))
