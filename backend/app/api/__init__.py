"""API 层公共依赖。"""

from __future__ import annotations

from fastapi import Request

from app.schemas.common import AppError
from app.services.security import resolve_role


def role_from_request(request: Request) -> str:
    return resolve_role(request.headers.get("X-Demo-Role"))


def require_admin(request: Request) -> str:
    role = role_from_request(request)
    if role != "admin":
        raise AppError(403, "FORBIDDEN", "该操作仅 admin 角色可执行")
    return role


def require_report_role(request: Request) -> str:
    role = role_from_request(request)
    if role == "viewer":
        raise AppError(403, "FORBIDDEN", "viewer 角色不能生成报告")
    return role
