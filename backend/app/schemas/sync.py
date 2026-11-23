"""同步 API 的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class BatchSyncRequest(BaseModel):
    """批量同步请求；source_ids 省略时同步全部已注册源。"""

    source_ids: list[str] | None = None


class SyncRunTableOut(BaseModel):
    id: int
    run_id: str
    source_id: str
    namespace: str
    table_name: str
    staging_name: str | None = None
    status: str
    sync_mode: str
    rows_read: int
    rows_written: int
    rows_skipped: int
    bytes_written: int
    truncated: bool = False
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class SyncRunOut(BaseModel):
    run_id: str
    batch_id: str | None = None
    source_id: str
    source_name: str
    source_kind: str
    trigger_type: str
    trigger_role: str | None = None
    status: str
    attempts: int
    retry_count: int
    tables_total: int
    tables_succeeded: int
    tables_failed: int
    rows_read: int
    rows_written: int
    rows_skipped: int
    bytes_written: int
    truncated: bool = False
    warnings: list[str] = []
    error_code: str | None = None
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    created_at: str
    updated_at: str
    tables: list[SyncRunTableOut] | None = None


class SyncStartOut(BaseModel):
    run_id: str
    source_id: str
    status: str
    created_at: str


class SyncBatchRunOut(BaseModel):
    source_id: str
    run_id: str
    status: str


class SyncBatchSkippedOut(BaseModel):
    source_id: str
    reason: str
    run_id: str | None = None


class SyncBatchOut(BaseModel):
    batch_id: str
    runs: list[SyncBatchRunOut]
    skipped: list[SyncBatchSkippedOut]


class SyncSourceStatusOut(BaseModel):
    source_id: str
    name: str
    kind: str
    sync_status: str
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_success_at: str | None = None
    tables_total: int = 0
    rows_total: int = 0


class SyncSourcesOut(BaseModel):
    items: list[SyncSourceStatusOut]
    last_batch_at: str | None = None


class SyncRunListOut(BaseModel):
    items: list[SyncRunOut]
    total: int
    limit: int
    offset: int
