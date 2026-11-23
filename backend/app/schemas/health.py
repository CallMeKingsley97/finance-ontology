"""健康检查响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class HealthData(BaseModel):
    status: str
    version: str
    db_path: str
    db_exists: bool
    table_count: int
    latest_period: str | None
    llm_enabled: bool
    time: str
