"""元数据目录 API：汇总、列表、详情、人工编辑、变更历史与脱敏样本。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api import role_from_request
from app.schemas.catalog import CatalogColumnUpdateRequest, CatalogTableUpdateRequest
from app.schemas.common import AppError, ok
from app.services.ingest import catalog

router = APIRouter(tags=["catalog"])


def _require_catalog_editor(request: Request) -> str:
    """目录编辑权限：admin/analyst；viewer 403。"""
    role = role_from_request(request)
    if role not in ("admin", "analyst"):
        raise AppError(403, "FORBIDDEN", "目录编辑仅 admin/analyst 可执行")
    return role


@router.get("/api/catalog/summary")
def catalog_summary_endpoint(request: Request) -> dict:
    """目录 KPI 汇总；启动后自动登记内置 10 表。"""
    role_from_request(request)
    catalog.ensure_bootstrap()
    return ok(catalog.summary())


@router.get("/api/catalog/tables")
def catalog_tables_endpoint(
    request: Request,
    source_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    business_domain: str | None = Query(default=None),
    sensitivity_level: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
) -> dict:
    """目录表清单：筛选与分页；page_size 上限 100 由服务层收敛。"""
    role_from_request(request)
    catalog.ensure_bootstrap()
    return ok(
        catalog.list_tables(
            {
                "source_id": source_id,
                "status": status,
                "owner": owner,
                "business_domain": business_domain,
                "sensitivity_level": sensitivity_level,
                "q": q,
                "page": page,
                "page_size": page_size,
            }
        )
    )


@router.get("/api/catalog/tables/{table_id}")
def catalog_table_endpoint(table_id: int, request: Request) -> dict:
    """目录表详情（含字段、staging 可用性）。"""
    role_from_request(request)
    return ok(catalog.get_table(table_id))


@router.patch("/api/catalog/tables/{table_id}")
def catalog_table_update_endpoint(
    table_id: int,
    request: Request,
    payload: CatalogTableUpdateRequest,
) -> dict:
    """人工编辑表级字段；变更写 catalog_change_log。"""
    role = _require_catalog_editor(request)
    return ok(catalog.update_table(table_id, payload.model_dump(exclude_unset=True), role))


@router.patch("/api/catalog/columns/{column_id}")
def catalog_column_update_endpoint(
    column_id: int,
    request: Request,
    payload: CatalogColumnUpdateRequest,
) -> dict:
    """人工编辑字段级信息；变更写 catalog_change_log。"""
    role = _require_catalog_editor(request)
    return ok(catalog.update_column(column_id, payload.model_dump(exclude_unset=True), role))


@router.get("/api/catalog/tables/{table_id}/changes")
def catalog_changes_endpoint(
    table_id: int,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """目录表变更历史（最近 N 条）。"""
    role_from_request(request)
    return ok(catalog.list_changes(table_id, limit))


@router.get("/api/catalog/tables/{table_id}/preview")
def catalog_preview_endpoint(
    table_id: int,
    request: Request,
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """staging 样本预览，按角色脱敏敏感列。"""
    role = role_from_request(request)
    return ok(catalog.preview_rows(table_id, role, limit))
