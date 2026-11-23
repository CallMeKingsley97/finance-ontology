"use client";

import { useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import DataTable from "@/components/DataTable";
import ErrorAlert from "@/components/ErrorAlert";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { ApiError, createMetricAlias, deleteMetricAlias, updateMetricAlias } from "@/lib/api";
import type { MetricAliasType, MetricDetail, Role } from "@/lib/types";

import { actionErrorText } from "./metricActionError";

const ALIAS_TYPE_LABELS: Record<string, string> = {
  synonym: "同义词",
  business: "业务别名",
  exclusion: "排除项",
};

export default function MetricAliasesPanel({
  metric,
  role,
  onChanged,
}: {
  metric: MetricDetail;
  role: Role;
  onChanged: () => void | Promise<void>;
}) {
  const canWrite = role === "admin" || role === "analyst";

  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [alias, setAlias] = useState("");
  const [aliasType, setAliasType] = useState<MetricAliasType>("business");
  const [aliasPriority, setAliasPriority] = useState(0);
  const [aliasStatus, setAliasStatus] = useState<"active" | "disabled">("active");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; details?: unknown } | null>(null);

  const openCreate = () => {
    setEditingId(null);
    setAlias("");
    setAliasType("business");
    setAliasPriority(0);
    setAliasStatus("active");
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (row: Record<string, unknown>) => {
    setEditingId(Number(row.id));
    setAlias(String(row.alias ?? ""));
    setAliasType(String(row.alias_type ?? "business") as MetricAliasType);
    setAliasPriority(Number(row.priority ?? 0));
    setAliasStatus(row.status === "disabled" ? "disabled" : "active");
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditingId(null);
    setFormError(null);
  };

  const handleSave = async () => {
    if (!alias.trim()) {
      setFormError("别名不能为空");
      return;
    }
    setSaving(true);
    setFormError(null);
    setNotice(null);
    setError(null);
    try {
      if (editingId !== null) {
        await updateMetricAlias(metric.code, editingId, {
          alias: alias.trim(),
          alias_type: aliasType,
          priority: aliasPriority,
          status: aliasStatus,
        });
        setNotice(`别名「${alias.trim()}」已更新。`);
      } else {
        await createMetricAlias(metric.code, {
          alias: alias.trim(),
          alias_type: aliasType,
          priority: aliasPriority,
        });
        setNotice(`别名「${alias.trim()}」已保存。`);
      }
      closeForm();
      await onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.code === "METRIC_ALIAS_CONFLICT") {
        setFormError("该指标已存在同名别名");
      } else {
        setFormError(actionErrorText(err).message);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (pendingDelete === null) return;
    setDeleteLoading(true);
    setNotice(null);
    setError(null);
    try {
      await deleteMetricAlias(metric.code, pendingDelete);
      setNotice("别名已删除。");
      setPendingDelete(null);
      await onChanged();
    } catch (err) {
      setError(actionErrorText(err));
    } finally {
      setDeleteLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <SectionCard
        title="业务别名"
        description="同一别名命中多个认证指标时，问数会返回候选而不是猜测。"
        actions={
          canWrite ? (
            <button
              type="button"
              onClick={() => (formOpen ? closeForm() : openCreate())}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              {formOpen ? "取消" : "新增别名"}
            </button>
          ) : undefined
        }
      >
        {formError ? (
          <div role="alert" className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {formError}
          </div>
        ) : null}
        {formOpen && canWrite ? (
          <div className="mb-4 grid gap-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3 sm:grid-cols-2 lg:grid-cols-5">
            <label className="text-xs text-slate-600">
              别名
              <input
                value={alias}
                onChange={(event) => setAlias(event.target.value)}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              />
            </label>
            <label className="text-xs text-slate-600">
              类型
              <select
                value={aliasType}
                onChange={(event) => setAliasType(event.target.value as MetricAliasType)}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              >
                <option value="synonym">同义词</option>
                <option value="business">业务别名</option>
                <option value="exclusion">排除项</option>
              </select>
            </label>
            <label className="text-xs text-slate-600">
              优先级
              <input
                type="number"
                min={0}
                max={1000}
                value={aliasPriority}
                onChange={(event) => setAliasPriority(Number(event.target.value))}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              />
            </label>
            {editingId !== null ? (
              <label className="text-xs text-slate-600">
                状态
                <select
                  value={aliasStatus}
                  onChange={(event) => setAliasStatus(event.target.value as "active" | "disabled")}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                >
                  <option value="active">启用</option>
                  <option value="disabled">停用</option>
                </select>
              </label>
            ) : null}
            <div className="flex items-end gap-2">
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={saving}
                className="w-full rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {saving ? "保存中…" : editingId !== null ? "保存修改" : "保存别名"}
              </button>
            </div>
          </div>
        ) : null}
        <DataTable
          columns={[
            { key: "alias", label: "别名" },
            {
              key: "alias_type",
              label: "类型",
              render: (_row, value) => ALIAS_TYPE_LABELS[String(value ?? "")] ?? String(value ?? ""),
            },
            { key: "priority", label: "优先级", align: "right" },
            {
              key: "status",
              label: "状态",
              align: "center",
              render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
            },
            { key: "created_by", label: "创建人" },
            {
              key: "actions",
              label: "操作",
              align: "center",
              render: (row) =>
                canWrite ? (
                  <div className="flex items-center justify-center gap-2">
                    <button
                      type="button"
                      onClick={() => openEdit(row)}
                      className="text-xs font-medium text-slate-600 hover:text-slate-900"
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(Number(row.id))}
                      className="text-xs font-medium text-rose-600 hover:text-rose-700"
                    >
                      删除
                    </button>
                  </div>
                ) : (
                  "—"
                ),
            },
          ]}
          rows={metric.aliases.map((item) => ({ ...item }))}
          emptyText="暂无别名"
        />
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

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除别名"
        description="删除后该问法将不再命中此指标，确认继续？"
        confirmText="删除"
        danger
        loading={deleteLoading}
        onConfirm={() => void handleDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
