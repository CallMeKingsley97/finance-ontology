"use client";

import { ChevronDown, ChevronRight } from "lucide-react";

import DataTable from "@/components/DataTable";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { formatDate, formatDuration, formatNumber } from "@/lib/format";
import type { SyncRun, SyncRunTable } from "@/lib/types";

const FILTERS = [
  { value: "", label: "全部" },
  { value: "failed", label: "失败" },
  { value: "running", label: "运行中" },
] as const;

export default function RunHistoryTable({
  runs,
  filter,
  onFilterChange,
  expandedRunId,
  expandedRun,
  expandedLoading,
  expandedError,
  onToggle,
}: {
  runs: SyncRun[];
  filter: string;
  onFilterChange: (value: string) => void;
  expandedRunId: string | null;
  expandedRun: SyncRun | null;
  expandedLoading: boolean;
  expandedError: string | null;
  onToggle: (runId: string) => void;
}) {
  const tableRows = expandedRun?.tables ?? [];

  return (
    <SectionCard
      title="运行历史"
      description="点击运行 ID 展开表级结果；失败错误已由后端脱敏。"
      actions={
        <div className="flex items-center gap-1" role="group" aria-label="运行状态筛选">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => onFilterChange(item.value)}
              aria-pressed={filter === item.value}
              className={[
                "cursor-pointer rounded-md border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
                filter === item.value
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50",
              ].join(" ")}
            >
              {item.label}
            </button>
          ))}
        </div>
      }
    >
      <DataTable
        maxHeight={360}
        emptyText="暂无运行记录"
        columns={[
          {
            key: "run_id",
            label: "运行 ID",
            render: (_row, value) => {
              const runId = String(value ?? "");
              const open = expandedRunId === runId;
              return (
                <button
                  type="button"
                  onClick={() => onToggle(runId)}
                  aria-expanded={open}
                  className="inline-flex cursor-pointer items-center gap-1 rounded font-mono text-[11px] text-slate-700 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                >
                  {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
                  {runId}
                </button>
              );
            },
          },
          { key: "source_name", label: "源" },
          { key: "trigger_type", label: "触发" },
          {
            key: "status",
            label: "状态",
            render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
          },
          {
            key: "tables",
            label: "表（成功/失败）",
            align: "right",
            render: (row) => (
              <span className="tabular-nums">
                {formatNumber(Number(row.tables_succeeded ?? 0), 0)}/
                {formatNumber(Number(row.tables_failed ?? 0), 0)}
              </span>
            ),
          },
          {
            key: "rows",
            label: "读取/写入",
            align: "right",
            render: (row) => (
              <span className="tabular-nums">
                {formatNumber(Number(row.rows_read ?? 0), 0)}/{formatNumber(Number(row.rows_written ?? 0), 0)}
              </span>
            ),
          },
          {
            key: "duration",
            label: "耗时",
            align: "right",
            render: (row) => {
              const started = row.started_at ? new Date(String(row.started_at)).getTime() : null;
              const finished = row.finished_at ? new Date(String(row.finished_at)).getTime() : null;
              const duration =
                started !== null && finished !== null && Number.isFinite(started) && Number.isFinite(finished)
                  ? finished - started
                  : null;
              return <span className="tabular-nums">{duration === null ? "—" : formatDuration(duration)}</span>;
            },
          },
          {
            key: "created_at",
            label: "开始",
            render: (_row, value) => <span title={String(value ?? "")}>{formatDate(value)}</span>,
          },
          {
            key: "retry",
            label: "重试",
            align: "right",
            render: (row) => <span className="tabular-nums">{formatNumber(Number(row.retry_count ?? 0), 0)}</span>,
          },
        ]}
        rows={runs.map((run) => ({ ...run }))}
      />

      {expandedRunId ? (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/60 p-3">
          {expandedLoading ? <LoadingState text="正在读取运行详情…" /> : null}
          {expandedError ? <ErrorAlert message={expandedError} /> : null}
          {!expandedLoading && !expandedError && expandedRun ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                <StatusBadge status={expandedRun.status} />
                <span className="font-mono">{expandedRun.run_id}</span>
                <span>重试 {expandedRun.retry_count} 次</span>
                {expandedRun.error_code ? (
                  <span className="break-all text-rose-700">
                    {expandedRun.error_code} · {expandedRun.error_message ?? ""}
                  </span>
                ) : null}
                {expandedRun.warnings.length > 0 ? (
                  <span className="text-amber-700">warning：{expandedRun.warnings.join(", ")}</span>
                ) : null}
              </div>
              <DataTable
                maxHeight={260}
                emptyText="本次运行没有表级结果"
                columns={[
                  { key: "table_name", label: "表" },
                  { key: "sync_mode", label: "模式" },
                  {
                    key: "status",
                    label: "状态",
                    render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
                  },
                  {
                    key: "rows_read",
                    label: "读取",
                    align: "right",
                    render: (_row, value) => <span className="tabular-nums">{formatNumber(value, 0)}</span>,
                  },
                  {
                    key: "rows_written",
                    label: "写入",
                    align: "right",
                    render: (_row, value) => <span className="tabular-nums">{formatNumber(value, 0)}</span>,
                  },
                  {
                    key: "duration_ms",
                    label: "耗时",
                    align: "right",
                    render: (_row, value) => <span className="tabular-nums">{formatDuration(value)}</span>,
                  },
                  {
                    key: "error_message",
                    label: "错误（已脱敏）",
                    render: (_row, value) =>
                      value ? <span className="break-all text-rose-700">{String(value)}</span> : "—",
                  },
                ]}
                rows={tableRows.map((table: SyncRunTable) => ({ ...table }))}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </SectionCard>
  );
}
