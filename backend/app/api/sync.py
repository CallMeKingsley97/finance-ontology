"""同步 API：单源/批量触发、运行查询与源同步状态。

鉴权：
- 单源同步仅 admin；
- 批量同步：X-Service-Token 正确 → cron；否则 admin → manual；否则 403；
  配置了令牌但令牌错误 → 401 SYNC_TOKEN_INVALID；
- 服务令牌为空时令牌通道关闭，批量同步只能由 admin 触发。
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from app.api import role_from_request
from app.core.config import settings
from app.db.registry import list_source_rows
from app.schemas.common import AppError, ok
from app.schemas.sync import BatchSyncRequest
from app.services.ingest import orchestrator, runs, store
from app.services.sources import BUILTIN_SOURCE_ID

logger = logging.getLogger("finance_ontology.api.sync")

router = APIRouter(tags=["sync"])


def _require_sync_admin(request: Request) -> str:
    role = role_from_request(request)
    if role != "admin":
        raise AppError(403, "FORBIDDEN", "同步触发仅 admin 可执行")
    return role


def _token_matches(expected: str, supplied: str) -> bool:
    try:
        return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def _batch_trigger(request: Request) -> tuple[str, str]:
    """返回 (trigger_type, trigger_role)。"""
    expected = (settings.sync_service_token or "").strip()
    supplied = (request.headers.get("X-Service-Token") or "").strip()
    if expected and supplied:
        if not _token_matches(expected, supplied):
            raise AppError(401, "SYNC_TOKEN_INVALID", "服务令牌无效")
        return "cron", "service"
    role = role_from_request(request)
    if role != "admin":
        raise AppError(403, "FORBIDDEN", "批量同步仅 admin 或服务令牌可执行")
    return "manual", role


def _resolve_sources(requested: list[str] | None) -> list[dict[str, Any]]:
    rows = list_source_rows()
    if requested is None:
        return rows
    by_id = {str(row["id"]): row for row in rows}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in requested:
        source_id = str(raw or "").strip()
        if not source_id:
            raise AppError(422, "VALIDATION_FAILED", "source_ids 包含空值")
        if source_id == BUILTIN_SOURCE_ID:
            raise AppError(409, "BUILTIN_SOURCE", "内置数据源不支持同步")
        if source_id in seen:
            continue
        row = by_id.get(source_id)
        if row is None:
            raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")
        seen.add(source_id)
        selected.append(row)
    if not selected:
        raise AppError(409, "SYNC_NO_SOURCES", "没有可同步的已注册数据源")
    max_sources = max(1, int(settings.staging_max_batch_sources or 50))
    if len(selected) > max_sources:
        raise AppError(
            422,
            "SYNC_BATCH_TOO_LARGE",
            f"单次批量同步最多 {max_sources} 个数据源",
        )
    return selected


@router.post("/api/sources/{source_id}/sync", status_code=202)
def single_sync_endpoint(source_id: str, request: Request) -> dict:
    """触发单源同步；立即返回 202 与 run_id。"""
    role = _require_sync_admin(request)
    run = orchestrator.start_single_sync(source_id, role)
    return ok(
        {
            "run_id": run["run_id"],
            "source_id": run["source_id"],
            "status": run["status"],
            "created_at": run["created_at"],
        }
    )


@router.post("/api/sync/run", status_code=202)
def batch_sync_endpoint(
    request: Request,
    payload: BatchSyncRequest | None = None,
) -> dict:
    """批量同步所有源或指定子集；单个 daemon 线程顺序执行。"""
    trigger_type, trigger_role = _batch_trigger(request)
    store.ensure_staging_available()
    sources = _resolve_sources(payload.source_ids if payload else None)
    batch = runs.start_batch(sources, trigger_type=trigger_type, trigger_role=trigger_role)
    orchestrator.start_batch_worker(batch)
    return ok(batch)


@router.get("/api/sync/runs")
def sync_runs_endpoint(
    request: Request,
    source_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """运行历史；筛选与分页全部参数化。"""
    role_from_request(request)
    store.ensure_staging_available()
    data = runs.list_runs(source_id=source_id, status=status, limit=limit, offset=offset)
    return ok({"items": data["items"], "total": data["total"]})


@router.get("/api/sync/runs/{run_id}")
def sync_run_endpoint(run_id: str, request: Request) -> dict:
    """运行详情（含表级结果与脱敏错误）。"""
    role_from_request(request)
    store.ensure_staging_available()
    return ok(runs.get_run(run_id))


def _derive_sync_status(last_status: str | None) -> str:
    if last_status in ("queued", "running"):
        return "running"
    if last_status in ("failed", "dead_letter"):
        return "failed"
    if last_status == "partial":
        return "partial"
    if last_status in ("succeeded", "interrupted"):
        return last_status
    return "never"


@router.get("/api/sync/sources")
def sync_sources_endpoint(request: Request) -> dict:
    """注册源同步聚合；只读 registry 与 staging，不建立源连接。"""
    role_from_request(request)
    store.ensure_staging_available()
    rows = list_source_rows()
    with store.staging_connection() as conn:
        staging_rows = conn.execute(
            """SELECT source_id, COUNT(*) AS tables_total,
                      COALESCE(SUM(row_count), 0) AS rows_total,
                      MAX(last_success_at) AS last_success_at
               FROM staging_table GROUP BY source_id"""
        ).fetchall()
        latest_rows = conn.execute(
            """SELECT source_id, run_id, status, created_at FROM (
                   SELECT source_id, run_id, status, created_at,
                          ROW_NUMBER() OVER (
                              PARTITION BY source_id
                              ORDER BY created_at DESC, run_id DESC
                          ) AS rn
                   FROM ingest_run
               ) WHERE rn = 1"""
        ).fetchall()
        batch_row = conn.execute(
            "SELECT MAX(created_at) AS value FROM ingest_run WHERE batch_id IS NOT NULL"
        ).fetchone()
    staging = {str(row["source_id"]): row for row in staging_rows}
    latest = {str(row["source_id"]): row for row in latest_rows}
    items: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row["id"])
        agg = staging.get(source_id)
        last = latest.get(source_id)
        last_status = str(last["status"]) if last else None
        items.append(
            {
                "source_id": source_id,
                "name": str(row.get("name") or ""),
                "kind": str(row.get("kind") or ""),
                "sync_status": _derive_sync_status(last_status),
                "last_run_id": str(last["run_id"]) if last else None,
                "last_run_status": last_status,
                "last_run_at": str(last["created_at"]) if last else None,
                "last_success_at": (
                    str(agg["last_success_at"]) if agg and agg["last_success_at"] else None
                ),
                "tables_total": int(agg["tables_total"]) if agg else 0,
                "rows_total": int(agg["rows_total"]) if agg else 0,
            }
        )
    return ok(
        {
            "items": items,
            "last_batch_at": batch_row["value"] if batch_row else None,
        }
    )
