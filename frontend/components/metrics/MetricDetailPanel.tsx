"use client";

import { useEffect, useState } from "react";

import EmptyState from "@/components/EmptyState";
import LoadingState from "@/components/LoadingState";
import StatusBadge from "@/components/StatusBadge";
import Tabs, { tabButtonId, tabPanelId, type TabItem } from "@/components/Tabs";
import { formatDate } from "@/lib/format";
import type { MetricDetail, Role } from "@/lib/types";

import MetricAliasesPanel from "./MetricAliasesPanel";
import MetricDefinitionPanel from "./MetricDefinitionPanel";
import MetricTestCasesPanel from "./MetricTestCasesPanel";
import MetricVersionsPanel from "./MetricVersionsPanel";

export type MetricDetailTab = "definition" | "versions" | "tests" | "aliases";

export default function MetricDetailPanel({
  metric,
  role,
  loading,
  detailTab,
  onDetailTabChange,
  onChanged,
  onDirtyChange,
  onGoLibrary,
}: {
  metric: MetricDetail | null;
  role: Role;
  loading?: boolean;
  detailTab: MetricDetailTab;
  onDetailTabChange: (tab: MetricDetailTab) => void;
  onChanged: () => void | Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  onGoLibrary: () => void;
}) {
  const [editRequestToken, setEditRequestToken] = useState(0);
  const [pendingEdit, setPendingEdit] = useState(false);

  useEffect(() => {
    if (!pendingEdit || detailTab !== "definition") return;
    setEditRequestToken((value) => value + 1);
    setPendingEdit(false);
  }, [pendingEdit, detailTab]);

  if (loading && !metric) {
    return <LoadingState text="正在加载指标详情…" />;
  }
  if (!metric) {
    return (
      <EmptyState
        title="选择一个指标查看详情"
        description="从指标库选择一个指标，查看定义、边界、版本、测试用例与别名。"
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
    );
  }

  const canWrite = role === "admin" || role === "analyst";
  const summary = metric.test_summary;
  const tabItems: TabItem[] = [
    { value: "definition", label: "口径定义" },
    {
      value: "versions",
      label: "版本与认证",
      badge: metric.draft_version?.status === "pending" ? "待认证" : undefined,
    },
    { value: "tests", label: "测试用例", badge: `${summary?.passed ?? 0}/${summary?.total ?? 0}` },
    { value: "aliases", label: "别名", badge: metric.aliases.length },
  ];

  const requestEdit = () => {
    if (detailTab === "definition") {
      setEditRequestToken((value) => value + 1);
      return;
    }
    setPendingEdit(true);
    onDetailTabChange("definition");
  };

  return (
    <div className="space-y-4">
      <div className="card p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold tracking-tight text-slate-900">{metric.name}</h2>
              <StatusBadge status={metric.status} />
              {metric.current_version ? (
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                  当前认证 {metric.current_version}
                </span>
              ) : null}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-500">
              {metric.code} · {metric.business_domain || "未分域"} · {metric.unit || "无单位"} · 敏感级别{" "}
              {metric.sensitivity_level}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              负责人 {metric.owner || "待指定"} · 更新 {formatDate(metric.updated_at)} · 关联术语{" "}
              {metric.term_code || "未关联"}
              {metric.draft_version ? ` · 草稿版本 ${metric.draft_version.version}` : ""}
            </div>
            <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-slate-700">
              {metric.definition || "尚未维护定义"}
            </p>
          </div>
          {canWrite ? (
            <div className="no-print flex flex-wrap gap-2">
              <button
                type="button"
                onClick={requestEdit}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                编辑口径
              </button>
              <button
                type="button"
                onClick={() => onDetailTabChange("versions")}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                处理认证
              </button>
            </div>
          ) : (
            <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-500">
              viewer 只读
            </span>
          )}
        </div>
      </div>

      <Tabs
        items={tabItems}
        value={detailTab}
        onChange={(next) => onDetailTabChange(next as MetricDetailTab)}
        ariaLabel="指标详情视图"
        idPrefix="metric-detail"
        size="sm"
      />

      <div
        role="tabpanel"
        id={tabPanelId("metric-detail", detailTab)}
        aria-labelledby={tabButtonId("metric-detail", detailTab)}
        className="mt-4"
      >
        {detailTab === "definition" ? (
          <MetricDefinitionPanel
            key={metric.code}
            metric={metric}
            role={role}
            editRequestToken={editRequestToken}
            onChanged={onChanged}
            onDirtyChange={onDirtyChange}
          />
        ) : null}
        {detailTab === "versions" ? (
          <MetricVersionsPanel key={metric.code} metric={metric} role={role} onChanged={onChanged} />
        ) : null}
        {detailTab === "tests" ? (
          <MetricTestCasesPanel key={metric.code} metric={metric} role={role} onChanged={onChanged} />
        ) : null}
        {detailTab === "aliases" ? (
          <MetricAliasesPanel key={metric.code} metric={metric} role={role} onChanged={onChanged} />
        ) : null}
      </div>
    </div>
  );
}
