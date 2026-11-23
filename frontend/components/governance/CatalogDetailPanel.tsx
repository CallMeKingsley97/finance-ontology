"use client";

import { ChevronDown, ChevronRight, Pencil, RefreshCw, Save, ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import type { DataTableColumn } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { formatDate, formatNumber, formatRelativeTime } from "@/lib/format";
import type {
  CatalogChange,
  CatalogColumn,
  CatalogColumnUpdateBody,
  CatalogPreview,
  CatalogTableDetail,
  CatalogTableUpdateBody,
  Role,
} from "@/lib/types";

import type { ActionFeedback } from "./SyncMonitorPanel";

const INPUT_CLASS =
  "w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-800 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200";

const SENSITIVITY_OPTIONS = [
  { value: "public", label: "公开" },
  { value: "internal", label: "内部" },
  { value: "confidential", label: "机密" },
  { value: "restricted", label: "受限" },
] as const;

const STATUS_LABELS: Record<string, string> = {
  pending: "待补充",
  active: "正常",
  missing: "缺失",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function isEditor(role: Role): boolean {
  return role === "admin" || role === "analyst";
}

interface TableFormState {
  display_name: string;
  description: string;
  owner: string;
  business_domain: string;
  sensitivity_level: string;
  watermark_column: string;
}

interface ColumnFormState {
  display_name: string;
  description: string;
  semantic_type: string;
  sensitivity_level: string;
}

function formFromTable(table: CatalogTableDetail): TableFormState {
  return {
    display_name: table.display_name ?? "",
    description: table.description ?? "",
    owner: table.owner ?? "",
    business_domain: table.business_domain ?? "",
    sensitivity_level: table.sensitivity_level ?? "internal",
    watermark_column: table.watermark_column ?? "",
  };
}

function formFromColumn(column: CatalogColumn): ColumnFormState {
  return {
    display_name: column.display_name ?? "",
    description: column.description ?? "",
    semantic_type: column.semantic_type ?? "",
    sensitivity_level: column.sensitivity_level ?? "internal",
  };
}

export default function CatalogDetailPanel({
  table,
  role,
  loading,
  error,
  changes,
  changesOpen,
  changesLoading,
  onToggleChanges,
  preview,
  previewLoading,
  onLoadPreview,
  onSaveTable,
  onSaveColumn,
  saving,
  formError,
  notice,
  onBackToList,
}: {
  table: CatalogTableDetail | null;
  role: Role;
  loading: boolean;
  error: string | null;
  changes: CatalogChange[];
  changesOpen: boolean;
  changesLoading: boolean;
  onToggleChanges: () => void;
  preview: CatalogPreview | null;
  previewLoading: boolean;
  onLoadPreview: () => void;
  onSaveTable: (body: CatalogTableUpdateBody) => Promise<boolean>;
  onSaveColumn: (columnId: number, body: CatalogColumnUpdateBody) => Promise<boolean>;
  saving: boolean;
  formError: ActionFeedback | null;
  notice: string | null;
  onBackToList: () => void;
}) {
  const editor = isEditor(role);
  const [tableEditing, setTableEditing] = useState(false);
  const [tableForm, setTableForm] = useState<TableFormState | null>(null);
  const [editingColumnId, setEditingColumnId] = useState<number | null>(null);
  const [columnForm, setColumnForm] = useState<ColumnFormState | null>(null);

  useEffect(() => {
    setTableEditing(false);
    setEditingColumnId(null);
    setColumnForm(null);
    setTableForm(table ? formFromTable(table) : null);
  }, [table?.id, table?.updated_at]);

  if (loading) return <LoadingState text="正在读取目录详情…" />;
  if (error) return <ErrorAlert message={error} />;
  if (!table) {
    return (
      <EmptyState
        title="未选择表"
        description="在左侧表清单中选择一张表，查看字段、变更历史与脱敏样本。"
      />
    );
  }

  const startTableEdit = () => {
    setTableForm(formFromTable(table));
    setTableEditing(true);
  };

  const handleTableSave = async () => {
    if (!tableForm) return;
    const body: CatalogTableUpdateBody = {
      display_name: tableForm.display_name,
      description: tableForm.description,
      owner: tableForm.owner,
      business_domain: tableForm.business_domain,
      sensitivity_level: tableForm.sensitivity_level as CatalogTableUpdateBody["sensitivity_level"],
    };
    if (role === "admin") {
      body.watermark_column = tableForm.watermark_column.trim() ? tableForm.watermark_column.trim() : null;
    }
    const success = await onSaveTable(body);
    if (success) setTableEditing(false);
  };

  const startColumnEdit = (column: CatalogColumn) => {
    setEditingColumnId(column.id);
    setColumnForm(formFromColumn(column));
  };

  const handleColumnSave = async (columnId: number) => {
    if (!columnForm) return;
    const success = await onSaveColumn(columnId, {
      display_name: columnForm.display_name,
      description: columnForm.description,
      semantic_type: columnForm.semantic_type,
      sensitivity_level: columnForm.sensitivity_level as CatalogColumnUpdateBody["sensitivity_level"],
    });
    if (success) {
      setEditingColumnId(null);
      setColumnForm(null);
    }
  };

  const editingColumn = table.columns.find((column) => column.id === editingColumnId) ?? null;

  const columnColumns: DataTableColumn[] = [
    { key: "column_name", label: "列名" },
    {
      key: "display_name",
      label: "中文名",
      render: (_row: Record<string, unknown>, value: unknown) =>
        value ? String(value) : <span className="text-slate-400">—</span>,
    },
    { key: "data_type", label: "类型" },
    {
      key: "keys",
      label: "键",
      render: (row: Record<string, unknown>) => {
        const parts: string[] = [];
        if (row.is_primary_key) parts.push("PK");
        if (row.is_foreign_key) parts.push("FK");
        return parts.length ? parts.join(" / ") : "—";
      },
    },
    {
      key: "is_nullable",
      label: "可空",
      render: (row: Record<string, unknown>) => (row.is_nullable ? "是" : "否"),
    },
    {
      key: "sensitivity_level",
      label: "敏感级别",
      render: (_row: Record<string, unknown>, value: unknown) => (
        <StatusBadge status={String(value ?? "internal")} label={
          SENSITIVITY_OPTIONS.find((item) => item.value === value)?.label ?? String(value ?? "")
        } />
      ),
    },
    {
      key: "status",
      label: "状态",
      render: (_row: Record<string, unknown>, value: unknown) => (
        <StatusBadge status={String(value ?? "")} label={statusLabel(String(value ?? ""))} />
      ),
    },
    ...(editor
      ? [
          {
            key: "actions",
            label: "操作",
            render: (row: Record<string, unknown>) => {
              const column = table.columns.find((item) => item.id === row.id);
              if (!column) return null;
              return (
                <button
                  type="button"
                  onClick={() => startColumnEdit(column)}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                >
                  <Pencil size={11} aria-hidden />
                  编辑
                </button>
              );
            },
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-4">
      <div className="lg:hidden">
        <button
          type="button"
          onClick={onBackToList}
          className="cursor-pointer text-xs font-medium text-slate-600 underline-offset-2 hover:underline"
        >
          ← 返回表清单
        </button>
      </div>

      {notice ? (
        <div role="status" aria-live="polite" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
          {notice}
        </div>
      ) : null}
      {formError ? (
        <div role="alert">
          <ErrorAlert message={formError.message} details={formError.details} />
        </div>
      ) : null}

      <SectionCard
        title={table.display_name || table.table_name}
        description={`${table.source_name || table.source_id} · ${table.namespace || "default"} · ${table.table_name}`}
        actions={
          editor ? (
            tableEditing ? (
              <>
                <button
                  type="button"
                  onClick={() => void handleTableSave()}
                  disabled={saving}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-50"
                >
                  <Save size={13} aria-hidden />
                  {saving ? "保存中…" : "保存表信息"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setTableEditing(false);
                    setTableForm(formFromTable(table));
                  }}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                >
                  <X size={13} aria-hidden />
                  取消
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={startTableEdit}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
              >
                <Pencil size={13} aria-hidden />
                编辑表信息
              </button>
            )
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-500">
              <ShieldAlert size={12} aria-hidden />
              viewer 只读
            </span>
          )
        }
      >
        {!tableEditing ? (
          <dl className="grid gap-3 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-slate-500">状态</dt>
              <dd className="mt-1">
                <StatusBadge status={table.status} label={statusLabel(table.status)} />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">负责人</dt>
              <dd className={`mt-1 font-medium ${table.claimed ? "text-slate-900" : "text-amber-700"}`}>
                {table.owner || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">业务域</dt>
              <dd className="mt-1 text-slate-900">{table.business_domain || "—"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">敏感级别</dt>
              <dd className="mt-1">
                <StatusBadge
                  status={table.sensitivity_level}
                  label={
                    SENSITIVITY_OPTIONS.find((item) => item.value === table.sensitivity_level)?.label ??
                    table.sensitivity_level
                  }
                />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">行数 / 字段数</dt>
              <dd className="mt-1 tabular-nums text-slate-900">
                {formatNumber(table.row_count, 0)} / {table.columns.length}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">上次同步</dt>
              <dd className="mt-1 text-slate-900" title={table.last_sync_at ?? undefined}>
                {formatRelativeTime(table.last_sync_at)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">水位列 / 模式</dt>
              <dd className="mt-1 text-slate-900" title={role === "analyst" ? "水位列影响增量策略，仅管理员可改" : undefined}>
                {table.watermark_column || "—"} / {table.sync_mode}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">本体映射</dt>
              <dd className="mt-1 text-slate-900">
                {table.ontology_term_id ? `term_id=${table.ontology_term_id}` : "—"}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-slate-500">说明</dt>
              <dd className="mt-1 leading-relaxed text-slate-900">{table.description || "—"}</dd>
            </div>
            <div className="sm:col-span-2 text-[11px] text-slate-400">
              最近人工变更：{table.updated_by ? `${table.updated_by} · ` : ""}
              {formatDate(table.updated_at)}
            </div>
          </dl>
        ) : tableForm ? (
          <div className="grid gap-3 text-xs sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-slate-500">中文名</span>
              <input
                className={INPUT_CLASS}
                value={tableForm.display_name}
                maxLength={80}
                onChange={(event) => setTableForm({ ...tableForm, display_name: event.target.value })}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-500">负责人</span>
              <input
                className={INPUT_CLASS}
                value={tableForm.owner}
                maxLength={80}
                placeholder="待认领"
                onChange={(event) => setTableForm({ ...tableForm, owner: event.target.value })}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-500">业务域</span>
              <input
                className={INPUT_CLASS}
                value={tableForm.business_domain}
                maxLength={80}
                onChange={(event) => setTableForm({ ...tableForm, business_domain: event.target.value })}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-slate-500">敏感级别</span>
              <select
                className={INPUT_CLASS}
                value={tableForm.sensitivity_level}
                onChange={(event) => setTableForm({ ...tableForm, sensitivity_level: event.target.value })}
              >
                {SENSITIVITY_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {role === "admin" ? (
              <label className="block sm:col-span-2">
                <span className="mb-1 block text-slate-500">水位列（留空表示不使用增量）</span>
                <input
                  className={INPUT_CLASS}
                  value={tableForm.watermark_column}
                  maxLength={128}
                  placeholder="例如 update_time"
                  onChange={(event) => setTableForm({ ...tableForm, watermark_column: event.target.value })}
                />
              </label>
            ) : (
              <div className="sm:col-span-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
                水位列影响增量策略，仅管理员可改。
              </div>
            )}
            <label className="block sm:col-span-2">
              <span className="mb-1 block text-slate-500">说明</span>
              <textarea
                className={`${INPUT_CLASS} min-h-20`}
                value={tableForm.description}
                maxLength={500}
                onChange={(event) => setTableForm({ ...tableForm, description: event.target.value })}
              />
            </label>
          </div>
        ) : null}
      </SectionCard>

      <SectionCard
        title="字段"
        description={`共 ${table.columns.length} 个字段；类型与键信息来自源结构，只读。`}
      >
        {table.columns.length === 0 ? (
          <EmptyState title="暂无字段" description="该表尚未同步或源未返回字段信息。" />
        ) : (
          <DataTable
            maxHeight={420}
            columns={columnColumns}
            rows={table.columns.map((column) => ({ ...column }))}
          />
        )}

        {editingColumn && columnForm ? (
          <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
            <div className="mb-3 flex items-center justify-between gap-2 text-xs font-medium text-slate-700">
              <span className="font-mono">{editingColumn.column_name}</span>
              <button
                type="button"
                onClick={() => {
                  setEditingColumnId(null);
                  setColumnForm(null);
                }}
                className="cursor-pointer rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
              >
                取消编辑
              </button>
            </div>
            <div className="grid gap-3 text-xs sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-slate-500">中文名</span>
                <input
                  className={INPUT_CLASS}
                  value={columnForm.display_name}
                  maxLength={80}
                  onChange={(event) => setColumnForm({ ...columnForm, display_name: event.target.value })}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-slate-500">语义类型</span>
                <input
                  className={INPUT_CLASS}
                  value={columnForm.semantic_type}
                  maxLength={40}
                  placeholder="例如 amount"
                  onChange={(event) => setColumnForm({ ...columnForm, semantic_type: event.target.value })}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-slate-500">敏感级别</span>
                <select
                  className={INPUT_CLASS}
                  value={columnForm.sensitivity_level}
                  onChange={(event) => setColumnForm({ ...columnForm, sensitivity_level: event.target.value })}
                >
                  {SENSITIVITY_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block sm:col-span-2">
                <span className="mb-1 block text-slate-500">说明</span>
                <input
                  className={INPUT_CLASS}
                  value={columnForm.description}
                  maxLength={500}
                  onChange={(event) => setColumnForm({ ...columnForm, description: event.target.value })}
                />
              </label>
            </div>
            <div className="mt-3">
              <button
                type="button"
                onClick={() => void handleColumnSave(editingColumn.id)}
                disabled={saving}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-50"
              >
                <Save size={13} aria-hidden />
                {saving ? "保存中…" : "保存字段"}
              </button>
            </div>
          </div>
        ) : null}
      </SectionCard>

      <SectionCard
        title="变更历史"
        description="每次人工变更记录字段、旧值、新值与角色；默认折叠。"
        actions={
          <button
            type="button"
            onClick={onToggleChanges}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            {changesOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
            {changesOpen ? "收起" : "展开历史"}
          </button>
        }
      >
        {changesOpen ? (
          changesLoading ? (
            <LoadingState text="正在读取变更历史…" />
          ) : changes.length === 0 ? (
            <EmptyState title="暂无变更记录" description="人工编辑表或字段后，这里会保留每次旧值与新值。" />
          ) : (
            <DataTable
              maxHeight={280}
              emptyText="暂无变更记录"
              columns={[
                { key: "field_name", label: "字段" },
                {
                  key: "old_value",
                  label: "旧值",
                  render: (_row, value) => (value ? String(value) : "—"),
                },
                {
                  key: "new_value",
                  label: "新值",
                  render: (_row, value) => (value ? String(value) : "—"),
                },
                {
                  key: "entity_type",
                  label: "对象",
                  render: (row) =>
                    row.entity_type === "column" ? `字段 #${String(row.column_id ?? "")}` : "表",
                },
                { key: "changed_by_role", label: "角色" },
                {
                  key: "created_at",
                  label: "时间",
                  render: (_row, value) => <span title={String(value ?? "")}>{formatDate(value)}</span>,
                },
              ]}
              rows={changes.map((change) => ({ ...change }))}
            />
          )
        ) : (
          <p className="text-xs text-slate-500">展开后按最近优先显示变更记录。</p>
        )}
      </SectionCard>

      <SectionCard
        title="样本预览"
        description="按当前角色脱敏；仅读取已同步的 staging 表。"
        actions={
          <button
            type="button"
            onClick={onLoadPreview}
            disabled={previewLoading || !table.staging_available}
            title={!table.staging_available ? "该表尚未同步到 staging" : "加载脱敏样本"}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw size={13} className={previewLoading ? "animate-spin" : ""} aria-hidden />
            {preview ? "刷新样本" : "加载样本"}
          </button>
        }
      >
        {!table.staging_available ? (
          <p className="text-xs text-slate-500">
            该表尚未同步到 staging（staging 状态：{table.staging_status ?? "—"}），无法预览。
          </p>
        ) : preview ? (
          <div className="space-y-2">
            {preview.masked_columns.length > 0 ? (
              <p className="text-[11px] text-amber-700">
                已脱敏列：{preview.masked_columns.join(", ")}
              </p>
            ) : null}
            <DataTable
              maxHeight={260}
              sensitiveColumns={preview.masked_columns}
              columns={preview.columns.map((name) => ({ key: name, label: name }))}
              rows={preview.rows}
              emptyText="该表暂无样本数据"
            />
          </div>
        ) : (
          <p className="text-xs text-slate-500">点击“加载样本”按需请求，默认不拉取数据。</p>
        )}
      </SectionCard>
    </div>
  );
}
