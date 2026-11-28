"use client";

import { Search } from "lucide-react";

import SectionCard from "@/components/SectionCard";
import type { KnowledgeSource } from "@/lib/types";

const DOMAIN_OPTIONS: { value: string; label: string }[] = [
  { value: "accounting_reconciliation", label: "会计核算与勾稽" },
  { value: "business_finance_integration", label: "业财融合" },
];

const KIND_OPTIONS: { value: string; label: string }[] = [
  { value: "term", label: "术语" },
  { value: "process", label: "流程" },
  { value: "metric_basis", label: "指标依据" },
  { value: "reconciliation_rule", label: "勾稽规则" },
  { value: "terminology", label: "行业说法" },
  { value: "correction", label: "科目修正" },
];

export default function KnowledgeFilters({
  q,
  domain,
  kind,
  sourceId,
  onQChange,
  onDomainChange,
  onKindChange,
  onSourceChange,
  sources,
  onSearch,
}: {
  q: string;
  domain: string;
  kind: string;
  sourceId: string;
  onQChange: (value: string) => void;
  onDomainChange: (value: string) => void;
  onKindChange: (value: string) => void;
  onSourceChange: (value: string) => void;
  sources: KnowledgeSource[];
  onSearch: () => void;
}) {
  return (
    <SectionCard
      title="筛选知识"
      description="关键词匹配标题、陈述与依据；领域、类型与来源为精确过滤。"
    >
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
        <label className="text-xs text-slate-600">
          关键词
          <input
            value={q}
            onChange={(event) => onQChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSearch();
            }}
            placeholder="标题、陈述或依据"
            className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
          />
        </label>
        <label className="text-xs text-slate-600">
          领域
          <select
            value={domain}
            onChange={(event) => onDomainChange(event.target.value)}
            className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
          >
            <option value="">全部</option>
            {DOMAIN_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          类型
          <select
            value={kind}
            onChange={(event) => onKindChange(event.target.value)}
            className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
          >
            <option value="">全部</option>
            {KIND_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          来源
          <select
            value={sourceId}
            onChange={(event) => onSourceChange(event.target.value)}
            className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
          >
            <option value="">全部</option>
            {sources.map((source) => (
              <option key={source.source_id} value={source.source_id}>
                {source.name}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <button
            type="button"
            onClick={onSearch}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
          >
            <Search size={13} aria-hidden />
            查询
          </button>
        </div>
      </div>
    </SectionCard>
  );
}
