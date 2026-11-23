"use client";

import { RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SensitiveBadge from "@/components/SensitiveBadge";
import { errorMessage, sourceMetadata, sourceTableDetail } from "@/lib/api";
import { formatCell, formatNumber } from "@/lib/format";
import type {
  SourceItem,
  SourceMetadataResponse,
  SourceMetadataTable,
  SourceTableDetail,
} from "@/lib/types";

function tableKey(table: SourceMetadataTable): string {
  return `${table.schema}.${table.name}`;
}

export default function SourceMetadataPanel({
  open,
  source,
  onClose,
}: {
  open: boolean;
  source: SourceItem | null;
  onClose: () => void;
}) {
  const [metadata, setMetadata] = useState<SourceMetadataResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<SourceTableDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      if (!source) return;
      setLoading(true);
      setError(null);
      try {
        const data = await sourceMetadata(source.id, refresh);
        setMetadata(data);
        setSelectedKey((current) => {
          if (current && data.tables.some((table) => tableKey(table) === current)) {
            return current;
          }
          return data.tables.length > 0 ? tableKey(data.tables[0]) : null;
        });
      } catch (err) {
        setError(errorMessage(err));
        setMetadata(null);
      } finally {
        setLoading(false);
      }
    },
    [source],
  );

  useEffect(() => {
    if (open && source) {
      setSelectedKey(null);
      setDetail(null);
      setDetailError(null);
      void load(false);
    }
  }, [open, source, load]);

  const selected = useMemo(() => {
    if (!metadata || metadata.tables.length === 0) return null;
    return (
      metadata.tables.find((table) => tableKey(table) === selectedKey) ??
      metadata.tables[0]
    );
  }, [metadata, selectedKey]);

  useEffect(() => {
    if (!open || !source || !selected) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    sourceTableDetail(source.id, selected.name, selected.schema, 5)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetail(null);
          setDetailError(errorMessage(err));
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, source, selected]);

  if (!open || !source) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="presentation">
      <div
        className="card flex h-[86vh] w-full max-w-5xl flex-col p-4 sm:p-5"
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-metadata-title"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 id="source-metadata-title" className="text-base font-semibold text-slate-900">
              元数据 · {source.name}
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              类型 {source.kind} · 表数 {source.table_count}
              {metadata?.cached ? " · 使用缓存快照" : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void load(true)}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} aria-hidden />
              刷新
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              aria-label="关闭"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {error ? <div className="mt-4"><ErrorAlert message={error} onRetry={() => void load(false)} /></div> : null}
        {loading && !metadata ? <div className="mt-4"><LoadingState text="正在读取元数据…" /></div> : null}

        {metadata ? (
          <div className="mt-4 grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(220px,30%)_1fr]">
            <div className="min-h-0 overflow-auto rounded-lg border border-slate-200">
              {metadata.tables.length === 0 ? (
                <div className="p-4 text-xs text-slate-500">该源暂无可展示的表。</div>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {metadata.tables.map((table) => {
                    const active = selected?.schema === table.schema && selected?.name === table.name;
                    return (
                      <li key={tableKey(table)}>
                        <button
                          type="button"
                          onClick={() => setSelectedKey(tableKey(table))}
                          className={`w-full px-3 py-2 text-left text-xs hover:bg-slate-50 ${
                            active ? "bg-slate-100" : ""
                          }`}
                        >
                          <span className="block font-medium text-slate-800">
                            {table.schema}.{table.name}
                          </span>
                          <span className="mt-0.5 block text-slate-500">
                            {table.row_count === null ? "行数未知" : `${formatNumber(table.row_count, 0)} 行`}
                            {table.row_count_estimated ? "（估算）" : ""} · {table.column_count} 列
                          </span>
                          {table.sensitive_columns.length > 0 ? (
                            <span className="mt-1 flex flex-wrap items-center gap-1">
                              <SensitiveBadge />
                              <span className="text-[11px] text-amber-700">
                                {table.sensitive_columns.join(", ")}
                              </span>
                            </span>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="min-h-0 overflow-auto pr-1">
              {metadata.truncated || metadata.warnings.length > 0 ? (
                <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
                  {metadata.warnings.length > 0
                    ? metadata.warnings.join("；")
                    : "元数据已截断，仅展示部分内容。"}
                </div>
              ) : null}
              {detailLoading ? <LoadingState text="正在读取表详情…" /> : null}
              {detailError ? <ErrorAlert message={detailError} onRetry={() => setSelectedKey(selectedKey)} /> : null}
              {!detailLoading && !detailError && !detail ? (
                <EmptyState title="选择左侧表查看详情" description="包含列类型、主外键、索引与脱敏样本。" />
              ) : null}
              {detail ? (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">
                      {detail.schema}.{detail.name}
                    </h3>
                    <p className="mt-1 text-xs text-slate-500">
                      {detail.comment || detail.description || "无描述"} · 行数{" "}
                      {detail.row_count === null ? "未知" : formatNumber(detail.row_count, 0)}
                    </p>
                    {detail.primary_key && detail.primary_key.length > 0 ? (
                      <p className="mt-1 text-xs text-slate-500">
                        主键：<span className="font-mono">{detail.primary_key.join(", ")}</span>
                      </p>
                    ) : null}
                  </div>

                  <div>
                    <h4 className="mb-2 text-xs font-semibold text-slate-700">列</h4>
                    <DataTable
                      columns={[
                        { key: "name", label: "列名" },
                        { key: "type", label: "类型" },
                        { key: "pk", label: "主键" },
                        { key: "notnull", label: "非空" },
                        {
                          key: "is_sensitive",
                          label: "敏感",
                          render: (_row, value) =>
                            value ? <SensitiveBadge /> : <span className="text-slate-400">—</span>,
                        },
                        { key: "description", label: "说明" },
                      ]}
                      rows={detail.columns.map((column) => ({ ...column }))}
                      maxHeight={220}
                    />
                  </div>

                  <div>
                    <h4 className="mb-2 text-xs font-semibold text-slate-700">外键</h4>
                    <DataTable
                      columns={[
                        { key: "from_column", label: "本表列" },
                        { key: "to_schema", label: "目标 schema" },
                        { key: "to_table", label: "目标表" },
                        { key: "to_column", label: "目标列" },
                      ]}
                      rows={detail.foreign_keys.map((item) => ({ ...item }))}
                      emptyText="无外键"
                      maxHeight={180}
                    />
                  </div>

                  <div>
                    <h4 className="mb-2 text-xs font-semibold text-slate-700">索引</h4>
                    <DataTable
                      columns={[
                        { key: "name", label: "索引名" },
                        { key: "unique", label: "唯一" },
                        {
                          key: "columns",
                          label: "列",
                          render: (_row, value) =>
                            formatCell(Array.isArray(value) ? value.join(", ") : value),
                        },
                      ]}
                      rows={detail.indexes.map((item) => ({ ...item }))}
                      emptyText="无索引"
                      maxHeight={180}
                    />
                  </div>

                  <div>
                    <h4 className="mb-2 text-xs font-semibold text-slate-700">
                      样本（{detail.sample_rows.length} 行，敏感列已按角色脱敏）
                    </h4>
                    <DataTable
                      columns={detail.columns.map((column) => ({
                        key: column.name,
                        label: column.name,
                      }))}
                      rows={detail.sample_rows}
                      sensitiveColumns={detail.masked_columns ?? []}
                      emptyText="无样本数据"
                      maxHeight={260}
                    />
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
