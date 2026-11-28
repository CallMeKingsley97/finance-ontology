"use client";

import EmptyState from "@/components/EmptyState";
import SectionCard from "@/components/SectionCard";
import type { KnowledgeSource } from "@/lib/types";

const SOURCE_TYPE_LABELS: Record<string, string> = {
  standard: "准则",
  opensource: "开源",
  practice: "实践",
};

export default function KnowledgeSourcePanel({
  sources,
}: {
  sources: KnowledgeSource[];
}) {
  return (
    <SectionCard
      title="知识来源与许可"
      description="知识包引用的准则、开源项目与实践来源；许可与版权声明均已保留。"
    >
      {sources.length === 0 ? (
        <EmptyState title="暂无知识来源" description="知识包未加载或未声明来源。" />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {sources.map((source) => (
            <div
              key={source.source_id}
              className="rounded-lg border border-slate-200 bg-slate-50/70 p-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-slate-800">{source.name}</div>
                  <div className="mt-0.5 font-mono text-[10px] text-slate-400">
                    {source.source_id}
                  </div>
                </div>
                <span className="inline-flex items-center rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-600">
                  {SOURCE_TYPE_LABELS[source.type] ?? source.type}
                </span>
              </div>

              <dl className="mt-2 grid gap-1 text-xs text-slate-600 sm:grid-cols-2">
                <div>
                  <dt className="inline text-slate-500">许可：</dt>
                  <dd className="inline text-slate-700">{source.license || "未标注"}</dd>
                </div>
                <div>
                  <dt className="inline text-slate-500">版本：</dt>
                  <dd className="inline text-slate-700">{source.version || "—"}</dd>
                </div>
                <div>
                  <dt className="inline text-slate-500">条目：</dt>
                  <dd className="inline text-slate-700">{source.entry_count} 条</dd>
                </div>
              </dl>

              {source.url ? (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block break-all text-[11px] text-slate-600 underline underline-offset-2 hover:text-slate-900"
                >
                  {source.url}
                </a>
              ) : null}

              {source.notice ? (
                <p className="mt-2 text-[11px] leading-relaxed text-slate-500">{source.notice}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}
