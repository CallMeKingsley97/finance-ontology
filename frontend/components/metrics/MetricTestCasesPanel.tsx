"use client";

import { useState } from "react";

import DataTable from "@/components/DataTable";
import ErrorAlert from "@/components/ErrorAlert";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { runMetricTests } from "@/lib/api";
import { formatNumber, formatPct, formatRelativeTime } from "@/lib/format";
import type { MetricDetail, Role } from "@/lib/types";

import { actionErrorText } from "./metricActionError";

export default function MetricTestCasesPanel({
  metric,
  role,
  onChanged,
}: {
  metric: MetricDetail;
  role: Role;
  onChanged: () => void | Promise<void>;
}) {
  const canWrite = role === "admin" || role === "analyst";
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; details?: unknown } | null>(null);

  const summary = metric.test_summary;

  const handleRun = async () => {
    setRunning(true);
    setNotice(null);
    setError(null);
    try {
      const result = await runMetricTests(metric.code);
      const failed = result.failed + result.errors;
      setNotice(
        failed > 0
          ? `用例执行完成：通过 ${result.passed}/${result.total}，失败 ${result.failed}，异常 ${result.errors}。`
          : `用例执行完成：${result.passed}/${result.total} 全部通过。`,
      );
      await onChanged();
    } catch (err) {
      setError(actionErrorText(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-4">
      <SectionCard
        title="测试用例"
        description="认证前必须全部通过；失败用例会阻止提交认证。"
        actions={
          canWrite ? (
            <button
              type="button"
              onClick={() => void handleRun()}
              disabled={running}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              {running ? "运行中…" : "运行用例"}
            </button>
          ) : undefined
        }
      >
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">共</div>
            <div className="mt-1 font-medium text-slate-800">{summary?.total ?? 0}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">通过</div>
            <div className="mt-1 font-medium text-emerald-700">{summary?.passed ?? 0}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">失败</div>
            <div className={`mt-1 font-medium ${(summary?.failed ?? 0) > 0 ? "text-rose-600" : "text-slate-800"}`}>
              {summary?.failed ?? 0}
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">异常</div>
            <div className={`mt-1 font-medium ${(summary?.error ?? 0) > 0 ? "text-rose-600" : "text-slate-800"}`}>
              {summary?.error ?? 0}
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">未执行</div>
            <div className="mt-1 font-medium text-slate-800">{summary?.not_run ?? 0}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">通过率</div>
            <div className="mt-1 font-medium text-slate-800">{formatPct(summary?.pass_rate ?? null)}</div>
          </div>
        </div>
      </SectionCard>

      {notice ? (
        <div
          aria-live="polite"
          className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
        >
          {notice}
        </div>
      ) : null}
      {error ? <ErrorAlert message={error.message} details={error.details ?? undefined} /> : null}

      <SectionCard title="用例清单" description="期望值、容差与最近一次执行结果。">
        <DataTable
          columns={[
            { key: "name", label: "名称" },
            { key: "period_code", label: "期间" },
            {
              key: "expected_value",
              label: "期望值",
              align: "right",
              render: (_row, value) => formatNumber(value, 4),
            },
            {
              key: "tolerance",
              label: "容差",
              render: (row) => `${formatNumber(row.tolerance_abs, 2)} / ${formatPct(row.tolerance_rel, 2)}`,
            },
            {
              key: "last_actual",
              label: "实际值",
              align: "right",
              render: (_row, value) => formatNumber(value, 4),
            },
            {
              key: "last_diff",
              label: "差异",
              align: "right",
              render: (_row, value) => formatNumber(value, 4),
            },
            {
              key: "last_status",
              label: "结果",
              align: "center",
              render: (_row, value) => <StatusBadge status={String(value ?? "not_run")} />,
            },
            {
              key: "last_run_at",
              label: "最近执行",
              render: (_row, value) => <span title={String(value ?? "")}>{formatRelativeTime(value)}</span>,
            },
            {
              key: "last_message",
              label: "失败详情",
              render: (_row, value) => {
                const text = value ? String(value) : "";
                return text ? (
                  <span className="block max-w-[220px] truncate" title={text}>
                    {text}
                  </span>
                ) : (
                  "—"
                );
              },
            },
          ]}
          rows={metric.test_cases.map((testCase) => ({ ...testCase }))}
          emptyText="暂无测试用例"
          maxHeight={320}
        />
      </SectionCard>
    </div>
  );
}
