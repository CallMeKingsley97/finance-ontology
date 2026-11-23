"""安全与审计 API。"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import ok
from app.services.security import get_policies, get_roles, list_audit

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/roles")
def security_roles() -> dict:
    return ok(get_roles())


@router.get("/policies")
def security_policies() -> dict:
    return ok(get_policies())


@router.get("/audit")
def security_audit(role: str | None = None, limit: int = 50) -> dict:
    return ok(list_audit(role, limit))
