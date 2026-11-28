"""外部 auto-ontology 绑定 API：状态读取、保存、清除与连接测试。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import role_from_request
from app.schemas.auto_ontology import AutoOntologyConfigRequest, AutoOntologyTestRequest
from app.schemas.common import AppError, ok
from app.services.auto_ontology import (
    clear_binding,
    get_binding_state,
    save_binding,
    test_binding,
)

router = APIRouter(prefix="/api/ontology", tags=["ontology-service"])


def _require_admin(request: Request) -> str:
    role = role_from_request(request)
    if role != "admin":
        raise AppError(403, "AUTO_ONTOLOGY_FORBIDDEN", "该操作仅 admin 角色可执行")
    return role


@router.get("/service")
def ontology_service_state() -> dict:
    """任意角色可读绑定状态；不触发上游请求。"""
    return ok(get_binding_state())


@router.put("/service")
def ontology_service_save(request: Request, payload: AutoOntologyConfigRequest) -> dict:
    _require_admin(request)
    return ok(save_binding(payload))


@router.delete("/service")
def ontology_service_clear(request: Request) -> dict:
    _require_admin(request)
    return ok(clear_binding())


@router.post("/service/test")
def ontology_service_test(
    request: Request,
    payload: AutoOntologyTestRequest | None = None,
) -> dict:
    _require_admin(request)
    return ok(test_binding(payload or AutoOntologyTestRequest()))
