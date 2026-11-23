"""通用错误与响应信封。"""

from __future__ import annotations

from typing import Any

ROLES = ("admin", "analyst", "viewer")


class AppError(Exception):
    """业务异常，统一由 main.py 转换为错误信封。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def ok(data: Any) -> dict[str, Any]:
    """成功信封。"""
    return {"code": "OK", "message": "", "data": data}
