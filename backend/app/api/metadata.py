"""元数据 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import role_from_request
from app.schemas.common import ok
from app.services.metadata import get_table_detail, list_tables

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.get("/tables")
def metadata_tables() -> dict:
    return ok(list_tables())


@router.get("/tables/{table}")
def metadata_table_detail(table: str, request: Request) -> dict:
    role = role_from_request(request)
    return ok(get_table_detail(table, role))
