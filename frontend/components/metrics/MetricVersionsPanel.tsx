"use client";

import { useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import DataTable from "@/components/DataTable";
import ErrorAlert from "@/components/ErrorAlert";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { certifyMetricVersion, submitMetricVersion } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { MetricDetail, Role } from "@/lib/types";

import { actionErrorText } from "./metricActionError";

export default function MetricVersionsPanel({
  metric,
  role,
  onChanged,
}: {
  metric: MetricDetail;
  role: Role;
  onChanged: () => void | Promise<void>;
}) {
  const canWrite = role === "admin" || role === "analyst";
  const canCertify = role === "admin";
  const draft = metric.draft_version;
  const canSubmit = Boolean(canWrite && draft && draft.status === "draft");
  const canCertifyVersion = Boolean(
    canCertify && draft && (draft.status === "draft" || draft.status === "pending"),
  );

  const [action, setAction] = useState<"submit" | "certify" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; details?: unknown } | null>(null);
  const [certifyOpen, setCertifyOpen] = useState(false);

  const clearFeedback = () => {
    setNotice(null);
    setError(null);
  };

  const handleSubmit = async () => {
    if (!draft) return;
    setAction("submit");
    clearFeedback();
    try {
      await submitMetricVersion(metric.code, draft.version);
      setNotice(`${draft.version} 已提交认证，等待 admin 处理。`);
      await onChanged();
    } catch (err) {
      setError(actionErrorText(err));
    } finally {
      setAction(null);
    }
  };

  const handleCertify = async () => {
    if (!draft) return;
    setAction("certify");
    clearFeedback();
    try {
      await certifyMetricVersion(metric.code, draft.version);
      setNotice(`${draft.version} 认证成功，旧认证版本已废弃。`);
      setCertifyOpen(false);
      await onChanged();
    } catch (err) {
      setCertifyOpen(false);
      setError(actionErrorText(err));
    } finally {
      setAction(null);
    }
  };

  const summary = metric.test_summary;

  return (
    <div className="space-y-4">
      <SectionCard
        title="认证状态"
        description="同一指标仅允许一个 certified 版本；新认证会自动废弃旧版本。"
        actions={
          <div className="flex flex-wrap gap-2">
            {canWrite ? (
              <button
                type="button"
                onClick={() => void handleSubmit()}
                disabled={action !== null || !canSubmit}
                title={canSubmit ? "提交草稿版本等待认证" : "当前没有可提交的草稿版本"}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {action === "submit" ? "提交中…" : "提交认证"}
              </button>
            ) : null}
            {canCertify ? (
              <button
                type="button"
                onClick={() => setCertifyOpen(true)}
                disabled={action !== null || !canCertifyVersion}
                title={canCertifyVersion ? "认证前会先运行全部测试用例" : "当前没有草稿或待认证版本"}
                className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {action === "certify" ? "认证中…" : "认证"}
              </button>
            ) : null}
          </div>
        }
      >
        <div className="grid gap-2 text-xs sm:grid-cols-3">
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">当前认证版本</div>
            <div className="mt-1 font-medium text-slate-800">{metric.current_version || "暂无"}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">草稿 / 待认证</div>
            <div className="mt-1 flex items-center gap-1.5 text-slate-800">
              {draft ? (
                <>
                  <span className="font-medium">{draft.version}</span>
                  <StatusBadge status={draft.status} />
                </>
              ) : (
                <span className="text-slate-500">暂无</span>
              )}
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="text-slate-500">测试用例</div>
            <div className="mt-1 font-medium text-slate-800">
              {summary?.passed ?? 0}/{summary?.total ?? 0} 通过
            </div>
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

      <SectionCard title="版本历史" description="按创建时间倒序展示指标版本与认证信息。">
        <DataTable
          columns={[
            { key: "version", label: "版本" },
            {
              key: "status",
              label: "状态",
              align: "center",
              render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
            },
            { key: "change_note", label: "变更说明" },
            { key: "effective_period", label: "生效期" },
            { key: "certified_by", label: "认证人" },
            {
              key: "certified_at",
              label: "认证时间",
              render: (_row, value) => formatDate(value),
            },
            { key: "created_by", label: "创建人" },
          ]}
          rows={metric.versions.map((version) => ({ ...version }))}
          emptyText="暂无版本"
        />
      </SectionCard>

      <ConfirmDialog
        open={certifyOpen}
        title="认证版本"
        description="认证前会先运行全部测试用例，失败将阻止认证。确认认证当前草稿/待认证版本？"
        confirmText="认证"
        loading={action === "certify"}
        onConfirm={() => void handleCertify()}
        onCancel={() => {
          if (action !== "certify") setCertifyOpen(false);
        }}
      />
    </div>
  );
}
