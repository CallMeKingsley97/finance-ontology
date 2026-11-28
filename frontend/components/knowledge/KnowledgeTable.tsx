"use client";

import DataTable from "@/components/DataTable";
import type { KnowledgeEntry } from "@/lib/types";

const KIND_LABELS: Record<string, string> = {
  term: "术语",
  process: "流程",
  metric_basis: "指标依据",
  reconciliation_rule: "勾稽规则",
  terminology: "行业说法",
  correction: "科目修正",
};

const DOMAIN_LABELS: Record<string, string> = {
  accounting_reconciliation: "会计核算与勾稽",
  business_finance_integration: "业财融合",
};

const CONFIDENCE_STYLES: Record<string, string> = {
  standard: "border-emerald-200 bg-emerald-50 text-emerald-700",
  opensource: "border-sky-200 bg-sky-50 text-sky-700",
  practice: "border-amber-200 bg-amber-50 text-amber-700",
};

const CONFIDENCE_LABELS: Record<string, string> = {
  standard: "准则",
  opensource: "开源",
  practice: "实践",
};

function ConfidenceBadge({ value }: { value: string }) {
  const key = String(value ?? "").toLowerCase();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        CONFIDENCE_STYLES[key] ?? "border-slate-200 bg-slate-50 text-slate-600"
      }`}
    >
      {CONFIDENCE_LABELS[key] ?? value ?? "—"}
    </span>
  );
}

export default function KnowledgeTable({
  items,
  total,
  page,
  pageSize,
  onSelect,
  onPageChange,
}: {
  items: KnowledgeEntry[];
  total: number;
  page: number;
  pageSize: number;
  onSelect: (entryId: string) => void;
  onPageChange: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const rows = items.map((item) => ({
    ...item,
    kind_display: KIND_LABELS[item.kind] ?? item.kind,
    domain_display: DOMAIN_LABELS[item.domain] ?? item.domain,
  }));

  return (
    <div className="space-y-3">
      <DataTable
        columns={[
          {
            key: "title",
            label: "标题",
            render: (row) => (
              <div className="min-w-0">
                <span className="block text-sm text-slate-800">{String(row.title ?? "")}</span>
                <span className="block truncate font-mono text-[10px] text-slate-400">
                  {String(row.entry_id ?? "")}
                </span>
              </div>
            ),
          },
          { key: "kind_display", label: "类型", render: (_row, value) => String(value ?? "—") },
          { key: "domain_display", label: "领域", render: (_row, value) => String(value ?? "—") },
          {
            key: "confidence",
            label: "可信度",
            align: "center",
            render: (_row, value) => <ConfidenceBadge value={String(value ?? "")} />,
          },
          {
            key: "source_name",
            label: "来源",
            render: (_row, value) => String(value || "—"),
          },
        ]}
        rows={rows}
        emptyText="暂无知识条目，调整筛选条件或等待知识包加载。"
        onRowClick={(row) => onSelect(String(row.entry_id))}
      />
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>
          共 {total} 条知识 · 第 {page}/{pageCount} 页
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            上一页
          </button>
          <button
            type="button"
            disabled={page >= pageCount}
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
