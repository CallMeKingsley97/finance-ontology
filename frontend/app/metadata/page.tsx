"use client";

import { RefreshCw, Table2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import SensitiveBadge from "@/components/SensitiveBadge";
import { errorMessage, metadataTable, metadataTables, useDemoRole } from "@/lib/api";
import type { TableDetail, TableSummary } from "@/lib/types";

export default function MetadataPage() {
  const [role] = useDemoRole();
  const [tables, setTables] = useState<TableSummary[]>([]);
  const [tablesLoading, setTablesLoading] = useState(true);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TableDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailRefresh, setDetailRefresh] = useState(0);

  const loadTables = useCallback(async () => {
    setTablesLoading(true);
    setTablesError(null);
    try {
      const data = await metadataTables();
      setTables(data.items);
      setSelected((current) => {
        if (current && data.items.some((item) => item.name === current)) return current;
        return data.items[0]?.name ?? null;
      });
    } catch (err) {
      setTablesError(errorMessage(err));
      setTables([]);
    } finally {
      setTablesLoading(false);
    }
  }, [role]);

  useEffect(() => {
    void loadTables();
  }, [loadTables]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    metadataTable(selected)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetailError(errorMessage(err));
          setDetail(null);
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, role, detailRefresh]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">元数据</h1>
        <p className="mt-1 text-sm text-slate-500">
          浏览业务表的列、主外键、索引与样本行；敏感列的样本值由后端按当前角色统一掩码。
        </p>
      </div>

      {tablesLoading ? <LoadingState text="正在读取表清单…" /> : null}
      {tablesError ? <ErrorAlert message={tablesError} onRetry={loadTables} /> : null}

      {!tablesLoading && !tablesError && tables.length === 0 ? (
        <EmptyState
          title="没有可展示的业务表"
          description="数据库可能尚未播种，请前往数据源页面重新播种。"
        />
      ) : null}

      {tables.length > 0 ? (
        <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
          <SectionCard title="业务表" description={`共 ${tables.length} 张`}>
            <div className="space-y-1">
              {tables.map((table) => {
                const active = table.name === selected;
                return (
                  <button
                    key={table.name}
                    type="button"
                    onClick={() => setSelected(table.name)}
                    className={[
                      "flex w-full items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors",
                      active
                        ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
                    ].join(" ")}
                  >
                    <span className="inline-flex min-w-0 items-center gap-2">
                      <Table2 size={13} aria-hidden className={active ? "text-white" : "text-slate-400"} />
                      <span className="truncate font-mono">{table.name}</span>
                    </span>
                    <span className={active ? "text-slate-200" : "text-slate-400"}>{table.row_count}</span>
                  </button>
                );
              })}
            </div>
          </SectionCard>

          <div className="min-w-0 space-y-5">
            {detailLoading ? <LoadingState text="正在读取表详情…" /> : null}
            {detailError ? (
              <ErrorAlert message={detailError} onRetry={() => setDetailRefresh((value) => value + 1)} />
            ) : null}
            {!detailLoading && !detailError && !detail ? (
              <EmptyState title="请选择左侧业务表" />
            ) : null}

            {detail ? (
              <>
                <SectionCard
                  title={detail.name}
                  description={`${detail.row_count} 行 · ${detail.columns.length} 列 · ${detail.foreign_keys.length} 个外键 · ${detail.indexes.length} 个索引`}
                  actions={
                    <button
                      type="button"
                      onClick={() => setDetailRefresh((value) => value + 1)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    >
                      <RefreshCw size={13} aria-hidden />
                      刷新
                    </button>
                  }
                >
                  <DataTable
                    columns={[
                      { key: "name", label: "列名" },
                      { key: "type", label: "类型" },
                      {
                        key: "pk",
                        label: "主键",
                        align: "center",
                        render: (_row, value) => (value ? "是" : "—"),
                      },
                      {
                        key: "notnull",
                        label: "非空",
                        align: "center",
                        render: (_row, value) => (value ? "是" : "—"),
                      },
                      { key: "default", label: "默认值" },
                      {
                        key: "is_sensitive",
                        label: "敏感",
                        align: "center",
                        render: (_row, value) =>
                          value ? <SensitiveBadge classification={String(_row.classification ?? "")} /> : "—",
                      },
                      {
                        key: "sample_masked",
                        label: "样本（掩码）",
                        render: (row, value) => {
                          if (!row.is_sensitive) return "—";
                          if (role !== "admin") {
                            return <span className="text-slate-400">见下方样本行（按角色掩码）</span>;
                          }
                          return String(value ?? "—");
                        },
                      },
                      { key: "description", label: "说明" },
                    ]}
                    rows={detail.columns.map((column) => ({ ...column }))}
                    sensitiveColumns={detail.masked_columns ?? []}
                    maxHeight={420}
                  />
                  {(detail.masked_columns ?? []).length ? (
                    <p className="mt-3 text-[11px] text-slate-500">
                      当前角色 {role} 下已掩码列：{(detail.masked_columns ?? []).join("、")}
                    </p>
                  ) : null}
                </SectionCard>

                <div className="grid gap-5 xl:grid-cols-2">
                  <SectionCard title="外键" description="来自 PRAGMA foreign_key_list">
                    <DataTable
                      columns={[
                        { key: "from_column", label: "本表列" },
                        { key: "to_table", label: "引用表" },
                        { key: "to_column", label: "引用列" },
                      ]}
                      rows={detail.foreign_keys.map((fk) => ({ ...fk }))}
                      emptyText="无外键"
                    />
                  </SectionCard>

                  <SectionCard title="索引" description="来自 PRAGMA index_list">
                    <DataTable
                      columns={[
                        { key: "name", label: "索引名" },
                        {
                          key: "unique",
                          label: "唯一",
                          align: "center",
                          render: (_row, value) => (value ? "是" : "否"),
                        },
                        {
                          key: "columns",
                          label: "列",
                          render: (_row, value) => (Array.isArray(value) ? value.join(", ") : "—"),
                        },
                      ]}
                      rows={detail.indexes.map((index) => ({ ...index }))}
                      emptyText="无索引"
                    />
                  </SectionCard>
                </div>

                <SectionCard title="样本行" description="最多 5 行，敏感列已由后端掩码">
                  <DataTable
                    columns={detail.columns.map((column) => ({
                      key: column.name,
                      label: column.name,
                      align: typeof column.type === "string" && /int|real|numeric|decimal/i.test(column.type)
                        ? "right"
                        : "left",
                    }))}
                    rows={detail.sample_rows}
                    sensitiveColumns={detail.masked_columns ?? []}
                    emptyText="暂无样本数据"
                    maxHeight={360}
                  />
                </SectionCard>
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
