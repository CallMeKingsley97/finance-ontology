"use client";

import KpiCard from "@/components/KpiCard";
import LoadingState from "@/components/LoadingState";
import { formatPct } from "@/lib/format";
import type { MetricSummary } from "@/lib/types";

export default function MetricSummaryCards({
  summary,
  loading,
}: {
  summary: MetricSummary | null;
  loading?: boolean;
}) {
  if (loading && !summary) {
    return <LoadingState text="正在加载指标概况…" />;
  }
  const pending = (summary?.draft ?? 0) + (summary?.pending ?? 0);
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiCard label="指标总数" formatted={`${summary?.total ?? 0} 个`} />
      <KpiCard label="已认证" formatted={`${summary?.certified ?? 0} 个`} />
      <KpiCard label="待认证草稿" formatted={`${pending} 个`} />
      <KpiCard
        label="用例通过率"
        formatted={summary?.test_pass_rate === null || summary?.test_pass_rate === undefined ? "—" : formatPct(summary.test_pass_rate)}
      />
    </div>
  );
}
