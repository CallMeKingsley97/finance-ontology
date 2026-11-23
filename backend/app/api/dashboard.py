"""仪表盘 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import role_from_request
from app.schemas.common import ok
from app.services.dashboard import get_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary(request: Request) -> dict:
    role = role_from_request(request)
    return ok(get_summary(role))
