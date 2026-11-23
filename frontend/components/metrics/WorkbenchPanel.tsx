"use client";

import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import { errorMessage, metricsList } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import type { MetricDefinition, Role } from "@/lib/types";

const QUEUE_PAGE_SIZE = 100;

export default function WorkbenchPanel({
  role,
  active,
  refreshToken,
  onOpenMetric,
  onGoLibrary,
}: {
  role: Role;
  active: boolean;
  refreshToken: number;
  onOpenMetric: (code: string, detailTab: "definition" | "versions") => void;
  onGoLibrary: () => void;
}) {
  const canWrite = role === "admin" || role === "analyst";

  const [draftItems, setDraftItems] = useState<MetricDefinition[] | null>(null);
  const [pendingItems, setPendingItems] = useState<MetricDefinition[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [draftList, pendingList] = await Promise.all([
        metricsList({ status: "draft", page_size: QUEUE_PAGE_SIZE }),
        metricsList({ status: "pending", page_size: QUEUE_PAGE_SIZE }),
      ]);
      setDraftItems(draftList.items);
      setPendingItems(pendingList.items);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, refreshToken, load]);

  const actionLabel = (kind: "draft" | "pending") => {
    if (!canWrite) return "查看";
    return kind === "draft" ? "去完善" : "去认证";
  };

  const renderQueue = (items: MetricDefinition[], kind: "draft" | "pending") => {
    const rows = items.map((item) => ({
      ...item,
      test_display: `${item.test_passed ?? 0}/${item.test_total ?? 0}`,
      test_failed: (item.test_total ?? 0) - (item.test_passed ?? 0),
    }));
    return (
      <SectionCard
        title={`${kind === "draft" ? "待提交（草稿）" : "待认证"}${items.length}`}
        description={
          kind === "draft"
            ? "草稿版本需要补齐口径、用例与别名后提交认证。"
            : "待认证版本由 admin 在详情中运行用例并完成认证。"
        }
      >
        <DataTable
          columns={[
            {
              key: "name",
              label: "指标",
              render: (row) => (
                <div>
                  <div className="text-sm text-slate-800">{String(row.name ?? "")}</div>
                  <div className="font-mono text-[10px] text-slate-400">{String(row.code ?? "")}</div>
                </div>
              ),
            },
            {
              key: "owner",
              label: "负责人",
              render: (_row, value) => String(value || "待指定"),
            },
            { key: "current_version", label: "当前版本", align: "center" },
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
              render: (_row, value) => <span title={String(value ?? "")}>{formatRelativeTime(value)}</span>,
            },
            {
              key: "actions",
              label: "操作",
              align: "center",
              render: (row) => (
                <button
                  type="button"
                  onClick={() =>
                    onOpenMetric(String(row.code), kind === "draft" ? "definition" : "versions")
                  }
                  className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  {actionLabel(kind)}
                </button>
              ),
            },
          ]}
          rows={rows}
          emptyText={kind === "draft" ? "暂无待提交草稿" : "暂无待认证指标"}
        />
      </SectionCard>
    );
  };

  if (error && !draftItems && !pendingItems) {
    return <ErrorAlert message={error} onRetry={() => void load()} />;
  }
  if (loading && (!draftItems || !pendingItems)) {
    return <LoadingState text="正在加载认证工作台…" />;
  }
  if (!draftItems || !pendingItems) {
    return <LoadingState text="正在加载认证工作台…" />;
  }

  const empty = draftItems.length === 0 && pendingItems.length === 0;

  return (
    <div className="space-y-4">
      {error ? <ErrorAlert message={error} onRetry={() => void load()} /> : null}
      {empty ? (
        <EmptyState
          title="当前没有待处理的认证任务"
          description="草稿与待认证队列均为空；可前往指标库查看全部指标。"
          action={
            <button
              type="button"
              onClick={onGoLibrary}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
            >
              去指标库
            </button>
          }
        />
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {renderQueue(draftItems, "draft")}
          {renderQueue(pendingItems, "pending")}
        </div>
      )}
    </div>
  );
}
