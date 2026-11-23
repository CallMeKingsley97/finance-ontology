"""指标注册表 API：目录、版本、认证、用例与别名。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api import require_admin, role_from_request
from app.schemas.common import AppError, ok
from app.schemas.metrics import (
    AliasCreateRequest,
    AliasUpdateRequest,
    MetricCreateRequest,
    MetricUpdateRequest,
    TestRunRequest,
    VersionCertifyRequest,
    VersionSubmitRequest,
)
from app.services import metrics

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _require_editor(request: Request) -> str:
    role = role_from_request(request)
    if role == "viewer":
        raise AppError(403, "FORBIDDEN", "viewer 角色不能修改指标")
    return role


@router.get("/summary")
def metric_summary() -> dict:
    return ok(metrics.summary())


@router.get("")
def metric_list(
    q: str | None = None,
    status: str | None = None,
    business_domain: str | None = None,
    owner: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    return ok(
        metrics.list_metrics(
            {
                "q": q,
                "status": status,
                "business_domain": business_domain,
                "owner": owner,
                "page": page,
                "page_size": page_size,
            }
        )
    )


@router.post("", status_code=201)
def metric_create(request: Request, payload: MetricCreateRequest) -> JSONResponse:
    role = _require_editor(request)
    data = metrics.create_metric(payload.model_dump(), role)
    return JSONResponse(status_code=201, content=ok(data))


@router.get("/{code}")
def metric_detail(code: str) -> dict:
    return ok(metrics.get_metric(code))


@router.patch("/{code}")
def metric_update(code: str, request: Request, payload: MetricUpdateRequest) -> dict:
    role = _require_editor(request)
    return ok(metrics.update_metric(code, payload.model_dump(exclude_unset=True), role))


@router.get("/{code}/versions")
def metric_versions(code: str) -> dict:
    return ok(metrics.list_versions(code))


@router.post("/{code}/versions/{version}/submit")
def metric_version_submit(
    code: str,
    version: str,
    request: Request,
    payload: VersionSubmitRequest | None = None,
) -> dict:
    role = _require_editor(request)
    note = payload.change_note if payload else None
    return ok(metrics.submit_version(code, version, role, note))


@router.post("/{code}/versions/{version}/certify")
def metric_version_certify(
    code: str,
    version: str,
    request: Request,
    payload: VersionCertifyRequest | None = None,
) -> dict:
    role = require_admin(request)
    note = payload.change_note if payload else None
    return ok(metrics.certify_version(code, version, role, note))


@router.post("/{code}/test-cases/run")
def metric_test_run(code: str, request: Request, payload: TestRunRequest | None = None) -> dict:
    role = _require_editor(request)
    version = payload.version if payload else None
    return ok(metrics.run_tests(code, role, version))


@router.post("/{code}/aliases", status_code=201)
def metric_alias_create(code: str, request: Request, payload: AliasCreateRequest) -> JSONResponse:
    role = _require_editor(request)
    data = metrics.create_alias(code, payload.model_dump(), role)
    return JSONResponse(status_code=201, content=ok(data))


@router.patch("/{code}/aliases/{alias_id}")
def metric_alias_update(
    code: str, alias_id: int, request: Request, payload: AliasUpdateRequest
) -> dict:
    role = _require_editor(request)
    return ok(metrics.update_alias(code, alias_id, payload.model_dump(exclude_unset=True), role))


@router.delete("/{code}/aliases/{alias_id}")
def metric_alias_delete(code: str, alias_id: int, request: Request) -> dict:
    role = _require_editor(request)
    return ok(metrics.delete_alias(code, alias_id, role))
