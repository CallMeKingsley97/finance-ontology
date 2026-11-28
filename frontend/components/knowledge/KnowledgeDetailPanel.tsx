"use client";

import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import type { KnowledgeEntryDetail } from "@/lib/types";

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

const CONFIDENCE_LABELS: Record<string, string> = {
  standard: "准则",
  opensource: "开源",
  practice: "实践",
};

const SOURCE_TYPE_LABELS: Record<string, string> = {
  standard: "准则",
  opensource: "开源",
  practice: "实践",
};

export default function KnowledgeDetailPanel({
  detail,
  loading,
  error,
}: {
  detail: KnowledgeEntryDetail | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading && !detail) {
    return <LoadingState text="正在加载知识详情…" />;
  }
  if (error && !detail) {
    return <ErrorAlert message={error} />;
  }
  if (!detail) {
    return (
      <EmptyState
        title="选择一条知识查看详情"
        description="从左侧知识列表选择条目，查看陈述、依据、来源、关联对象与原始数据。"
      />
    );
  }

  const source = detail.source;
  const links = detail.links ?? [];

  return (
    <div className="space-y-4">
      {loading ? <LoadingState text="正在刷新知识详情…" /> : null}
      {error ? <ErrorAlert message={error} /> : null}

      <SectionCard title={detail.title} description={detail.entry_id}>
        <dl className="grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
          <div>
            <dt className="inline text-slate-500">类型：</dt>
            <dd className="inline text-slate-700">{KIND_LABELS[detail.kind] ?? detail.kind}</dd>
          </div>
          <div>
            <dt className="inline text-slate-500">领域：</dt>
            <dd className="inline text-slate-700">{DOMAIN_LABELS[detail.domain] ?? detail.domain}</dd>
          </div>
          <div>
            <dt className="inline text-slate-500">可信度：</dt>
            <dd className="inline text-slate-700">
              {CONFIDENCE_LABELS[detail.confidence] ?? detail.confidence}
            </dd>
          </div>
          <div>
            <dt className="inline text-slate-500">版本：</dt>
            <dd className="inline text-slate-700">{detail.version || "—"}</dd>
          </div>
          <div>
            <dt className="inline text-slate-500">生效期：</dt>
            <dd className="inline text-slate-700">{detail.effective_from || "—"}</dd>
          </div>
        </dl>

        <div className="mt-4 space-y-3">
          <div>
            <div className="text-xs font-medium text-slate-600">知识陈述</div>
            <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-700">
              {detail.statement || "—"}
            </p>
          </div>
          <div>
            <div className="text-xs font-medium text-slate-600">依据</div>
            <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-700">
              {detail.basis || "—"}
            </p>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="知识来源">
        {source ? (
          <div className="space-y-2 text-xs text-slate-600">
            <div>
              <span className="text-slate-500">名称：</span>
              <span className="text-slate-700">{source.name || "—"}</span>
            </div>
            <div>
              <span className="text-slate-500">类型：</span>
              <span className="text-slate-700">
                {SOURCE_TYPE_LABELS[source.type] ?? source.type ?? "—"}
              </span>
            </div>
            <div>
              <span className="text-slate-500">许可：</span>
              <span className="text-slate-700">{source.license || "—"}</span>
            </div>
            <div>
              <span className="text-slate-500">版本：</span>
              <span className="text-slate-700">{source.version || "—"}</span>
            </div>
            {source.url ? (
              <div>
                <span className="text-slate-500">链接：</span>
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-slate-700 underline underline-offset-2 hover:text-slate-900"
                >
                  {source.url}
                </a>
              </div>
            ) : null}
            {source.notice ? (
              <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-600">
                {source.notice}
              </p>
            ) : null}
          </div>
        ) : (
          <div className="text-xs text-slate-500">该条目未关联来源信息。</div>
        )}
      </SectionCard>

      <SectionCard title="关联对象" description="知识条目指向的指标、概念或勾稽规则。">
        {links.length === 0 ? (
          <div className="text-xs text-slate-500">未建立关联。</div>
        ) : (
          <ul className="space-y-1 text-xs text-slate-600">
            {links.map((link) => (
              <li key={`${link.target_kind}-${link.target_code}`} className="flex gap-2">
                <span className="text-slate-500">{link.target_kind}</span>
                <span className="font-mono text-slate-700">{link.target_code}</span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="原始数据（payload）" description="知识包内该条目的结构化载荷。">
        <pre className="max-h-80 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-700">
          {JSON.stringify(detail.payload ?? {}, null, 2)}
        </pre>
      </SectionCard>
    </div>
  );
}
