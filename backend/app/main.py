"""FastAPI 应用入口：路由注册、统一信封与异常处理。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api import (
    catalog,
    dashboard,
    health,
    metadata,
    metrics,
    ontology,
    quality,
    query,
    report_packages,
    reports,
    security,
    sources,
    sync,
)
from app.core.config import settings
from app.db.connection import database_info
from app.schemas.common import AppError

logger = logging.getLogger("finance_ontology")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    info = database_info()
    logger.info(
        "Finance Ontology MVP backend starting: db_path=%s db_exists=%s tables=%s latest_period=%s llm_enabled=%s",
        info["db_path"],
        info["db_exists"],
        info["table_count"],
        info["latest_period"],
        settings.llm_enabled,
    )

    from app.services.ingest import catalog as catalog_service
    from app.services.ingest import runs as ingest_runs
    from app.services.ingest import store as staging_store

    try:
        staging_store.init_staging()
    except Exception:  # noqa: BLE001 - staging 初始化失败不阻断应用启动
        logger.exception("staging initialization failed; sync/catalog endpoints will return 503")

    try:
        recovery = ingest_runs.recover_interrupted_runs()
        logger.info(
            "staging recovery: interrupted=%s orphans_dropped=%s",
            recovery["interrupted"],
            recovery["orphans_dropped"],
        )
    except Exception:  # noqa: BLE001
        logger.exception("staging recovery failed")

    try:
        pruned = ingest_runs.prune_runs(settings.staging_run_retention_days)
        if pruned:
            logger.info(
                "staging pruned runs=%s retention_days=%s",
                pruned,
                settings.staging_run_retention_days,
            )
    except Exception:  # noqa: BLE001
        logger.exception("staging prune failed")

    try:
        bootstrap = catalog_service.ensure_bootstrap()
        logger.info("catalog bootstrap: %s", bootstrap)
    except Exception:  # noqa: BLE001
        logger.exception("catalog bootstrap failed")

    try:
        from app.db.connection import ensure_audit_columns

        ensure_audit_columns()
    except Exception:  # noqa: BLE001 - 审计列迁移失败不阻断启动
        logger.exception("query audit column migration failed")

    try:
        from app.services import metric_seed

        seeded = metric_seed.ensure_metric_catalog()
        logger.info("metric catalog seed: %s", seeded)
    except Exception:  # noqa: BLE001 - 指标目录失败不阻断启动
        logger.exception("metric catalog seed failed")

    try:
        from app.services import quality_seed

        seeded = quality_seed.ensure_quality_rules()
        logger.info("quality rule seed: %s", seeded)
    except Exception:  # noqa: BLE001 - 质量规则失败不阻断启动
        logger.exception("quality rule seed failed")

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="企业财务 Ontology Agent MVP API",
        description="数据源、元数据、本体、问数、报告与安全演示 API。角色头 X-Demo-Role 仅用于演示，不是生产安全边界。",
        version=__version__,
        lifespan=lifespan,
    )

    for router in (
        health.router,
        dashboard.router,
        sources.router,
        metadata.router,
        ontology.router,
        query.router,
        reports.router,
        metrics.router,
        quality.router,
        report_packages.router,
        security.router,
        sync.router,
        catalog.router,
    ):
        app.include_router(router)

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": jsonable_encoder(exc.details),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "VALIDATION_FAILED",
                "message": "请求参数校验失败",
                "details": {"errors": jsonable_encoder(exc.errors())},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_map = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 403: "FORBIDDEN"}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code_map.get(exc.status_code, "HTTP_ERROR"),
                "message": str(exc.detail),
                "details": {},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
                "details": {},
            },
        )

    return app


app = create_app()
