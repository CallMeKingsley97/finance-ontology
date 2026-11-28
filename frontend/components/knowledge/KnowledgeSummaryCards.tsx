"use client";

import KpiCard from "@/components/KpiCard";
import LoadingState from "@/components/LoadingState";
import type { KnowledgeSummary } from "@/lib/types";

export default function KnowledgeSummaryCards({
  summary,
}: {
  summary: KnowledgeSummary | null;
}) {
  if (!summary) {
    return <LoadingState text="正在加载知识概况…" />;
  }
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiCard label="知识条目" formatted={`${summary.entries} 条`} />
      <KpiCard label="知识来源" formatted={`${summary.sources} 个`} />
      <KpiCard label="知识包版本" formatted={summary.pack_version || "—"} />
      <KpiCard label="加载状态" formatted={summary.available ? "已加载" : "未加载"} />
    </div>
  );
}
