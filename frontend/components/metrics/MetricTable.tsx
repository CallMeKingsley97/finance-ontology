"use client";

import DataTable from "@/components/DataTable";
import StatusBadge from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/format";
import type { MetricDefinition } from "@/lib/types";

export default function MetricTable({
  items,
  selectedCode,
  total,
  page,
  pageSize,
  onSelect,
  onPageChange,
  loading,
}: {
  items: MetricDefinition[];
  selectedCode: string | null;
  total: number;
  page: number;
  pageSize: number;
  onSelect: (code: string) => void;
  onPageChange: (page: number) => void;
  loading?: boolean;
}) {
  const pageCount = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const rows = items.map((item) => ({
    ...item,
    test_display: `${item.test_passed ?? 0}/${item.test_total ?? 0}`,
    test_failed: (item.test_total ?? 0) - (item.test_passed ?? 0),
  }));

  return (
    <div className="space-y-3">
      <DataTable
        columns={[
          {
            key: "name",
            label: "指标",
            render: (row) => {
              const active = String(row.code) === selectedCode;
              return (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(String(row.code));
                  }}
                  aria-current={active ? "true" : undefined}
                  className={[
                    "text-left",
                    active ? "font-semibold text-slate-900" : "text-slate-700 hover:text-slate-900",
                  ].join(" ")}
                >
                  <span className="block text-sm">{String(row.name ?? "")}</span>
                  <span className="block font-mono text-[10px] text-slate-400">{String(row.code ?? "")}</span>
                </button>
              );
            },
          },
          { key: "business_domain", label: "业务域" },
          {
            key: "status",
            label: "状态",
            align: "center",
            render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
          },
          { key: "current_version", label: "当前版本", align: "center" },
          {
            key: "owner",
            label: "负责人",
            render: (_row, value) => String(value || "待指定"),
          },
          {
            key: "test_display",
            label: "用例",
            align: "center",
            render: (row, value) => (
              <span className={Number(row.test_failed) > 0 ? "font-medium text-rose-600" : "text-slate-600"}>
                {String(value ?? "0/0")}
              </span>
            ),
          },
          {
            key: "updated_at",
            label: "更新时间",
            render: (_row, value) => (
              <span title={String(value ?? "")}>{formatRelativeTime(value)}</span>
            ),
          },
        ]}
        rows={rows}
        emptyText={loading ? "正在加载指标…" : "暂无指标，先新增或调整筛选条件。"}
        onRowClick={(row) => onSelect(String(row.code))}
        rowClassName={(row) =>
          String(row.code) === selectedCode ? "shadow-[inset_3px_0_0_#0f172a]" : ""
        }
      />
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>
          共 {total} 个指标 · 第 {page}/{pageCount} 页
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={page <= 1 || loading}
            onClick={() => onPageChange(page - 1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            上一页
          </button>
          <button
            type="button"
            disabled={page >= pageCount || loading}
            onClick={() => onPageChange(page + 1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}
