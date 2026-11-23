"""健康检查。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app import __version__
from app.core.config import settings
from app.db.connection import database_info
from app.schemas.common import ok

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    info = database_info()
    status = "ok" if info["db_exists"] and info["table_count"] > 0 else "degraded"
    return ok(
        {
            "status": status,
            "version": __version__,
            "db_path": info["db_path"],
            "db_exists": info["db_exists"],
            "table_count": info["table_count"],
            "latest_period": info["latest_period"],
            "llm_enabled": settings.llm_enabled,
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
