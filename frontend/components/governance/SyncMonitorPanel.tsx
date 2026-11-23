"use client";

import { BellRing, RefreshCw, X } from "lucide-react";

import DataTable from "@/components/DataTable";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { formatDate, formatNumber, formatRelativeTime } from "@/lib/format";
import type { Role, SyncRun, SyncSourceStatus } from "@/lib/types";

import RunHistoryTable from "./RunHistoryTable";

export interface ActionFeedback {
  message: string;
  details?: unknown;
}

export default function SyncMonitorPanel({
  role,
  items,
  lastBatchAt,
  loading,
  error,
  onRefresh,
  selectedSource,
  onSelectSource,
  onSyncSource,
  syncingId,
  actionError,
  actionNotice,
  runs,
  runsError,
  runFilter,
  onRunFilterChange,
  expandedRunId,
  expandedRun,
  expandedLoading,
  expandedError,
  onToggleRun,
  onGoCatalog,
}: {
  role: Role;
  items: SyncSourceStatus[];
  lastBatchAt: string | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  selectedSource: string | null;
  onSelectSource: (sourceId: string | null) => void;
  onSyncSource: (sourceId: string) => void;
  syncingId: string | null;
  actionError: ActionFeedback | null;
  actionNotice: string | null;
  runs: SyncRun[];
  runsError: string | null;
  runFilter: string;
  onRunFilterChange: (value: string) => void;
  expandedRunId: string | null;
  expandedRun: SyncRun | null;
  expandedLoading: boolean;
  expandedError: string | null;
  onToggleRun: (runId: string) => void;
  onGoCatalog: () => void;
}) {
  const selectedName = items.find((item) => item.source_id === selectedSource)?.name ?? selectedSource;
  const isAdmin = role === "admin";

  return (
    <div className="space-y-4">
      {actionNotice ? (
        <div
          role="status"
          aria-live="polite"
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-700"
        >
          {actionNotice}
        </div>
      ) : null}
      {actionError ? (
        <div role="alert">
          <ErrorAlert message={actionError.message} details={actionError.details} />
        </div>
      ) : null}

      <SectionCard
        title="源同步状态"
        description={
          lastBatchAt
            ? `最近批量同步：${formatDate(lastBatchAt)}；单源互斥由数据库唯一索引保证。`
            : "尚未执行批量同步；单源同步按需触发，运行中可查看历史。"
        }
        actions={
          <>
            {selectedSource ? (
              <button
                type="button"
                onClick={() => onSelectSource(null)}
                className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                title="清除数据源筛选"
              >
                <X size={12} aria-hidden />
                仅看 {selectedName}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} aria-hidden />
              刷新
            </button>
          </>
        }
      >
        {loading ? <LoadingState text="正在读取同步状态…" /> : null}
        {error ? (
          <div role="alert">
            <ErrorAlert message={error} onRetry={onRefresh} />
          </div>
        ) : null}
        {!loading && !error ? (
          <DataTable
            maxHeight={320}
            emptyText="尚无已注册外部数据源；请先在数据源页接入。"
            columns={[
              {
                key: "name",
                label: "源",
                render: (row) => (
                  <button
                    type="button"
                    onClick={() => onSelectSource(String(row.source_id ?? ""))}
                    className="cursor-pointer text-left text-xs font-medium text-slate-800 hover:text-slate-950 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                    title="按该源筛选运行历史"
                  >
                    <span className="block">{String(row.name ?? "")}</span>
                    <span className="block font-mono text-[10px] text-slate-400">{String(row.source_id ?? "")}</span>
                  </button>
                ),
              },
              {
                key: "kind",
                label: "类型",
                render: (_row, value) => (
                  <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-600">
                    {String(value ?? "")}
                  </span>
                ),
              },
              {
                key: "sync_status",
                label: "状态",
                render: (row, value) => (
                  <StatusBadge
                    status={String(value ?? "")}
                    label={value === "never" ? "未同步" : undefined}
                  />
                ),
              },
              {
                key: "last_run_at",
                label: "最近运行",
                render: (_row, value) => (
                  <span title={value ? String(value) : undefined}>{formatRelativeTime(value)}</span>
                ),
              },
              {
                key: "last_success_at",
                label: "最近成功",
                render: (_row, value) => (
                  <span title={value ? String(value) : undefined}>{formatRelativeTime(value)}</span>
                ),
              },
              {
                key: "tables_total",
                label: "表数/行数",
                align: "right",
                render: (row) => (
                  <span className="tabular-nums">
                    {formatNumber(row.tables_total, 0)}/{formatNumber(row.rows_total, 0)}
                  </span>
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) => {
                  const sourceId = String(row.source_id ?? "");
                  const running = row.sync_status === "running";
                  return (
                    <button
                      type="button"
                      onClick={() => onSyncSource(sourceId)}
                      disabled={!isAdmin || syncingId === sourceId || running}
                      title={
                        !isAdmin
                          ? "仅管理员可触发同步"
                          : running
                            ? "该源已有运行中的同步任务"
                            : "触发单源同步"
                      }
                      className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <RefreshCw
                        size={11}
                        className={syncingId === sourceId || running ? "animate-spin" : ""}
                        aria-hidden
                      />
                      {syncingId === sourceId ? "提交中" : "同步"}
                    </button>
                  );
                },
              },
            ]}
            rows={items.map((item) => ({ ...item }))}
          />
        ) : null}
      </SectionCard>

      {runsError ? (
        <div role="alert">
          <ErrorAlert message={runsError} onRetry={onRefresh} />
        </div>
      ) : null}

      <RunHistoryTable
        runs={runs}
        filter={runFilter}
        onFilterChange={onRunFilterChange}
        expandedRunId={expandedRunId}
        expandedRun={expandedRun}
        expandedLoading={expandedLoading}
        expandedError={expandedError}
        onToggle={onToggleRun}
      />

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <BellRing size={13} aria-hidden />
          失败运行可按状态筛选后展开查看表级错误；目录中可核对表与字段说明。
        </span>
        <button
          type="button"
          onClick={onGoCatalog}
          className="cursor-pointer rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          前往元数据目录
        </button>
      </div>
    </div>
  );
}
