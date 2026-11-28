"""知识库 API：摘要、条目浏览、来源清单与 admin 重载。

读接口全员可读；``POST /reload`` 仅 admin。库不可用时读接口返回 HTTP 200
的降级结构（``available=false``），错误响应不包含异常详情或堆栈。
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api import require_admin
from app.schemas.common import AppError, ok
from app.schemas.knowledge import KnowledgeEntryQuery
from app.services.knowledge import loader, service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/summary")
def knowledge_summary() -> dict:
    return ok(service.summary())


@router.get("/entries")
def knowledge_entries(params: Annotated[KnowledgeEntryQuery, Query()]) -> dict:
    return ok(
        service.list_entries(
            q=params.q,
            domain=params.domain,
            kind=params.kind,
            source_id=params.source_id,
            page=params.page,
            page_size=params.page_size,
        )
    )


@router.get("/entries/{entry_id}")
def knowledge_entry_detail(entry_id: str) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise AppError(404, "KNOWLEDGE_ENTRY_NOT_FOUND", f"知识条目不存在: {entry_id}")
    return ok(entry)


@router.get("/sources")
def knowledge_sources() -> dict:
    return ok(service.list_sources())


@router.post("/reload")
def knowledge_reload(request: Request) -> dict:
    require_admin(request)
    try:
        result = loader.load_knowledge_packs(force=True)
    except loader.PackValidationError:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise AppError(503, "KNOWLEDGE_UNAVAILABLE", "知识库不可用") from exc
    summary = service.summary()
    return ok(
        {
            "reloaded": True,
            "pack_version": result.get("pack_version", ""),
            "entries": result.get("entries", 0),
            "sources": result.get("sources", 0),
            "domains": result.get("domains", 0),
            "removed": result.get("removed", 0),
            "duration_ms": result.get("duration_ms", 0),
            "loaded_at": summary.get("loaded_at"),
        }
    )
