"""数据源 API：多源 CRUD、上传、元数据与旧端点兼容。

路由声明顺序要求：/seed、/test、/upload 必须位于 /{source_id} 之前。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import ValidationError

from app.api import require_admin, role_from_request
from app.db.connection import seed_database
from app.schemas.common import AppError, ok
from app.schemas.source import SeedRequest, SourceCreateRequest, SourceTestRequest
from app.services.source_metadata import get_source_table_detail, list_source_metadata
from app.services.sources import (
    create_database_source,
    create_file_source,
    delete_source,
    get_source,
    list_sources,
    replace_file_source,
    test_legacy_sqlite,
    test_source as test_registered_source,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("")
def list_sources_endpoint() -> dict:
    """内置源 + 注册源列表；内置源固定第一。"""
    return ok(list_sources())


@router.post("/seed")
def seed_source(request: Request, payload: SeedRequest) -> dict:
    """保留旧行为：重新播种内置 SQLite 业务库。"""
    require_admin(request)
    result = seed_database(reset=payload.reset)
    if not result.get("seeded"):
        raise AppError(409, "SOURCE_ALREADY_SEEDED", "数据源已播种，如需重建请设置 reset=true")
    return ok(result)


@router.post("/test")
def test_legacy_source(request: Request, payload: SourceTestRequest) -> dict:
    """保留旧行为：测试内置库或上传目录内的 SQLite 文件。"""
    require_admin(request)
    return ok(test_legacy_sqlite(payload.path))


@router.post("/upload", status_code=201)
def upload_source(
    request: Request,
    name: str = Form(default=""),
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """上传 CSV/Excel/SQLite 创建文件源。"""
    require_admin(request)
    return ok(create_file_source(name, kind, file))


@router.post(
    "",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": SourceCreateRequest.model_json_schema(),
                }
            },
        }
    },
)
async def create_source(request: Request) -> dict:
    """新增 PostgreSQL/MySQL 数据源（仅登记非敏感配置与加密凭据）。"""
    require_admin(request)
    try:
        raw = await request.json()
    except ValueError as exc:
        raise AppError(422, "VALIDATION_FAILED", "请求体不是合法 JSON") from exc
    try:
        payload = SourceCreateRequest.model_validate(raw)
    except ValidationError as exc:
        # 手动校验：只保留类型/位置/提示，避免全局处理器回显 input 中的明文密码。
        safe_errors = [
            {
                "type": error.get("type", ""),
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", ""),
            }
            for error in exc.errors()
        ]
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "请求参数校验失败",
            {"errors": safe_errors},
        ) from exc
    return ok(create_database_source(payload))


@router.get("/{source_id}")
def get_source_detail(source_id: str) -> dict:
    """源详情；永不返回明文凭据、密文或服务器存储路径。"""
    return ok(get_source(source_id))


@router.delete("/{source_id}")
def delete_source_endpoint(source_id: str, request: Request) -> dict:
    """删除注册源及其上传文件；内置源返回 409。"""
    require_admin(request)
    return ok(delete_source(source_id))


@router.post("/{source_id}/test")
def test_source_endpoint(source_id: str, request: Request) -> dict:
    """测试指定源连接并更新状态。"""
    require_admin(request)
    return ok(test_registered_source(source_id))


@router.post("/{source_id}/file")
def replace_source_file(source_id: str, request: Request, file: UploadFile = File(...)) -> dict:
    """替换文件源存储文件，保留 id/name/kind。"""
    require_admin(request)
    return ok(replace_file_source(source_id, file))


@router.get("/{source_id}/metadata")
def source_metadata_endpoint(
    source_id: str,
    request: Request,
    refresh: bool = Query(default=False),
) -> dict:
    """读取源元数据表清单；refresh=true 强制刷新。"""
    role_from_request(request)
    return ok(list_source_metadata(source_id, refresh=refresh))


@router.get("/{source_id}/metadata/table")
def source_table_detail_endpoint(
    source_id: str,
    request: Request,
    table: str = Query(min_length=1),
    schema: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """读取单表列/主外键/索引/样本，样本按请求角色脱敏。"""
    role = role_from_request(request)
    return ok(get_source_table_detail(source_id, table, schema, role, limit))
