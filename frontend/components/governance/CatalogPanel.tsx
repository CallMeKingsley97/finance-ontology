"use client";

import { ChevronLeft, ChevronRight, Filter, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import type { DataTableColumn } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import {
  ApiError,
  catalogChanges,
  catalogPreview,
  catalogTable,
  catalogTables,
  errorMessage,
  sources,
  updateCatalogColumn,
  updateCatalogTable,
} from "@/lib/api";
import { formatNumber, formatRelativeTime } from "@/lib/format";
import type {
  CatalogChange,
  CatalogColumnUpdateBody,
  CatalogPreview,
  CatalogTableDetail,
  CatalogTableListResponse,
  CatalogTableUpdateBody,
  Role,
  SourceItem,
} from "@/lib/types";

import CatalogDetailPanel from "./CatalogDetailPanel";
import type { ActionFeedback } from "./SyncMonitorPanel";

const INPUT_CLASS =
  "w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-800 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200";

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "pending", label: "待补充" },
  { value: "active", label: "正常" },
  { value: "missing", label: "缺失" },
] as const;

const SENSITIVITY_LABELS: Record<string, string> = {
  public: "公开",
  internal: "内部",
  confidential: "机密",
  restricted: "受限",
};

interface FilterState {
  source_id: string;
  status: string;
  owner: string;
  business_domain: string;
  q: string;
  unclaimed: boolean;
}

const EMPTY_FILTERS: FilterState = {
  source_id: "",
  status: "",
  owner: "",
  business_domain: "",
  q: "",
  unclaimed: false,
};

function toFeedback(error: unknown): ActionFeedback {
  if (error instanceof ApiError) {
    return { message: error.message, details: error.details };
  }
  return { message: errorMessage(error) };
}

export default function CatalogPanel({
  role,
  selectedId,
  onSelect,
  onChanged,
}: {
  role: Role;
  selectedId: number | null;
  onSelect: (tableId: number | null) => void;
  onChanged: () => void;
}) {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [sourceOptions, setSourceOptions] = useState<SourceItem[]>([]);

  const [data, setData] = useState<CatalogTableListResponse | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [detail, setDetail] = useState<CatalogTableDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [changes, setChanges] = useState<CatalogChange[]>([]);
  const [changesOpen, setChangesOpen] = useState(false);
  const [changesLoading, setChangesLoading] = useState(false);

  const [preview, setPreview] = useState<CatalogPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<ActionFeedback | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loadOptions = async () => {
      try {
        const result = await sources();
        if (!cancelled) setSourceOptions(result.items);
      } catch {
        if (!cancelled) setSourceOptions([]);
      }
    };
    void loadOptions();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setApplied(filters);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [filters]);

  const loadTables = useCallback(
    async (quiet = false) => {
      if (!quiet) setListLoading(true);
      setListError(null);
      try {
        const result = await catalogTables({
          source_id: applied.source_id || undefined,
          status: applied.status || undefined,
          owner: applied.unclaimed ? "待认领" : applied.owner.trim() || undefined,
          business_domain: applied.business_domain.trim() || undefined,
          q: applied.q.trim() || undefined,
          page,
          page_size: 20,
        });
        setData(result);
      } catch (error) {
        if (!quiet) {
          setListError(errorMessage(error));
          setData(null);
        }
      } finally {
        if (!quiet) setListLoading(false);
      }
    },
    [applied, page],
  );

  useEffect(() => {
    void loadTables();
  }, [loadTables]);

  const loadDetail = useCallback(async (tableId: number) => {
    setDetailLoading(true);
    setDetailError(null);
    setChanges([]);
    setChangesOpen(false);
    setPreview(null);
    try {
      const result = await catalogTable(tableId);
      setDetail(result);
    } catch (error) {
      setDetail(null);
      setDetailError(errorMessage(error));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      setDetailError(null);
      setChanges([]);
      setChangesOpen(false);
      setPreview(null);
      setNotice(null);
      setFormError(null);
      return;
    }
    void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const loadChanges = useCallback(async (tableId: number) => {
    setChangesLoading(true);
    try {
      const result = await catalogChanges(tableId, 20);
      setChanges(result.items);
    } catch (error) {
      setFormError(toFeedback(error));
    } finally {
      setChangesLoading(false);
    }
  }, []);

  const handleToggleChanges = () => {
    if (!detail) return;
    const next = !changesOpen;
    setChangesOpen(next);
    if (next) void loadChanges(detail.id);
  };

  const handleLoadPreview = async () => {
    if (!detail) return;
    setPreviewLoading(true);
    setFormError(null);
    try {
      const result = await catalogPreview(detail.id, 5);
      setPreview(result);
    } catch (error) {
      setFormError(toFeedback(error));
    } finally {
      setPreviewLoading(false);
    }
  };

  const refreshAfterSave = async (tableId: number, message: string) => {
    const [refreshed] = await Promise.all([catalogTable(tableId), loadTables(true)]);
    setDetail(refreshed);
    if (changesOpen) await loadChanges(tableId);
    setNotice(message);
    setFormError(null);
    onChanged();
  };

  const handleSaveTable = async (body: CatalogTableUpdateBody): Promise<boolean> => {
    if (!detail) return false;
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      const updated = await updateCatalogTable(detail.id, body);
      setDetail(updated);
      await loadTables(true);
      if (changesOpen) await loadChanges(detail.id);
      setNotice("表信息已保存");
      onChanged();
      return true;
    } catch (error) {
      setFormError(toFeedback(error));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleSaveColumn = async (
    columnId: number,
    body: CatalogColumnUpdateBody,
  ): Promise<boolean> => {
    if (!detail) return false;
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      await updateCatalogColumn(columnId, body);
      await refreshAfterSave(detail.id, "字段信息已保存");
      return true;
    } catch (error) {
      setFormError(toFeedback(error));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const resetFilters = () => {
    setFilters(EMPTY_FILTERS);
    setPage(1);
  };

  const total = data?.total ?? 0;
  const pageSize = data?.page_size ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const activeFilterCount = [
    applied.source_id,
    applied.status,
    applied.owner.trim(),
    applied.business_domain.trim(),
    applied.q.trim(),
  ].filter(Boolean).length + (applied.unclaimed ? 1 : 0);

  const tableColumns: DataTableColumn[] = [
    {
      key: "display_name",
      label: "表",
      render: (row, value) => {
        const id = Number(row.id);
        return (
          <button
            type="button"
            onClick={() => onSelect(id)}
            className="cursor-pointer text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            <span className="block text-xs font-medium text-slate-800">
              {value ? String(value) : String(row.table_name ?? "")}
            </span>
            <span className="block font-mono text-[10px] text-slate-400">{String(row.table_name ?? "")}</span>
          </button>
        );
      },
    },
    {
      key: "source_name",
      label: "源",
      render: (row: Record<string, unknown>) => (
        <span className="text-[11px] text-slate-600">
          {String(row.source_name ?? row.source_id ?? "")}
          <span className="ml-1 text-[10px] uppercase text-slate-400">{String(row.source_kind ?? "")}</span>
        </span>
      ),
    },
    {
      key: "owner",
      label: "负责人",
      render: (_row: Record<string, unknown>, value: unknown) =>
        value && value !== "待认领" ? (
          <span className="text-slate-700">{String(value)}</span>
        ) : (
          <span className="text-amber-700">待认领</span>
        ),
    },
    {
      key: "status",
      label: "状态",
      render: (_row: Record<string, unknown>, value: unknown) => (
        <StatusBadge
          status={String(value ?? "")}
          label={value === "pending" ? "待补充" : value === "missing" ? "缺失" : value === "active" ? "正常" : undefined}
        />
      ),
    },
    {
      key: "row_count",
      label: "行数",
      align: "right",
      render: (_row: Record<string, unknown>, value: unknown) => (
        <span className="tabular-nums">{formatNumber(value, 0)}</span>
      ),
    },
    {
      key: "last_sync_at",
      label: "上次同步",
      render: (_row: Record<string, unknown>, value: unknown) => (
        <span title={value ? String(value) : undefined}>{formatRelativeTime(value)}</span>
      ),
    },
    {
      key: "sensitivity_level",
      label: "敏感",
      render: (_row: Record<string, unknown>, value: unknown) =>
        value && value !== "internal" ? (
          <StatusBadge status={String(value)} label={SENSITIVITY_LABELS[String(value)] ?? String(value)} />
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
  ];

  return (
    <div className="space-y-4">
      <SectionCard
        title="筛选"
        description="筛选条件变化后自动查询；搜索匹配中文名与物理表名。"
        actions={
          <button
            type="button"
            onClick={resetFilters}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            <X size={12} aria-hidden />
            重置
          </button>
        }
      >
        <div className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <label className="block">
            <span className="mb-1 block text-slate-500">数据源</span>
            <select
              className={INPUT_CLASS}
              value={filters.source_id}
              onChange={(event) => setFilters({ ...filters, source_id: event.target.value })}
            >
              <option value="">全部数据源</option>
              {sourceOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-500">状态</span>
            <select
              className={INPUT_CLASS}
              value={filters.status}
              onChange={(event) => setFilters({ ...filters, status: event.target.value })}
            >
              {STATUS_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-500">负责人</span>
            <input
              className={INPUT_CLASS}
              value={filters.unclaimed ? "待认领" : filters.owner}
              disabled={filters.unclaimed}
              placeholder="全部负责人"
              onChange={(event) => setFilters({ ...filters, owner: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-500">业务域</span>
            <input
              className={INPUT_CLASS}
              value={filters.business_domain}
              placeholder="全部业务域"
              onChange={(event) => setFilters({ ...filters, business_domain: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-slate-500">搜索</span>
            <input
              className={INPUT_CLASS}
              value={filters.q}
              placeholder="表名 / 中文名"
              onChange={(event) => setFilters({ ...filters, q: event.target.value })}
            />
          </label>
          <label className="flex items-center gap-2 self-end pb-1 text-slate-600">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-slate-300 text-slate-900 focus:ring-slate-400"
              checked={filters.unclaimed}
              onChange={(event) => setFilters({ ...filters, unclaimed: event.target.checked })}
            />
            仅看待认领
          </label>
        </div>
        {activeFilterCount > 0 ? (
          <p className="mt-2 inline-flex items-center gap-1 text-[11px] text-slate-500">
            <Filter size={11} aria-hidden />
            已应用 {activeFilterCount} 个筛选条件
          </p>
        ) : null}
      </SectionCard>

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <SectionCard
            title="表清单"
            description={`共 ${formatNumber(total, 0)} 张表；点击表名选中，右侧查看详情。`}
          >
            {listLoading ? <LoadingState text="正在读取元数据目录…" /> : null}
            {listError ? (
              <div role="alert">
                <ErrorAlert message={listError} onRetry={() => void loadTables()} />
              </div>
            ) : null}
            {!listLoading && !listError ? (
              data && data.items.length > 0 ? (
                <DataTable
                  maxHeight={560}
                  columns={tableColumns}
                  rows={data.items.map((item) => ({ ...item }))}
                />
              ) : (
                <EmptyState
                  title="没有匹配的表"
                  description="调整筛选条件或重置后重试；新接入数据源同步后会自动登记。"
                />
              )
            ) : null}
            {totalPages > 1 ? (
              <div className="mt-3 flex items-center justify-between text-xs text-slate-600">
                <button
                  type="button"
                  onClick={() => setPage((value) => Math.max(1, value - 1))}
                  disabled={page <= 1}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <ChevronLeft size={12} aria-hidden />
                  上一页
                </button>
                <span className="tabular-nums">
                  第 {page} / {totalPages} 页
                </span>
                <button
                  type="button"
                  onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                  disabled={page >= totalPages}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  下一页
                  <ChevronRight size={12} aria-hidden />
                </button>
              </div>
            ) : null}
          </SectionCard>
        </div>

        <div className="lg:col-span-7">
          <CatalogDetailPanel
            table={detail}
            role={role}
            loading={detailLoading}
            error={detailError}
            changes={changes}
            changesOpen={changesOpen}
            changesLoading={changesLoading}
            onToggleChanges={handleToggleChanges}
            preview={preview}
            previewLoading={previewLoading}
            onLoadPreview={() => void handleLoadPreview()}
            onSaveTable={handleSaveTable}
            onSaveColumn={handleSaveColumn}
            saving={saving}
            formError={formError}
            notice={notice}
            onBackToList={() => onSelect(null)}
          />
        </div>
      </div>
    </div>
  );
}
