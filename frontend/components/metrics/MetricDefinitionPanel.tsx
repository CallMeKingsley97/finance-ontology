"use client";

import { useEffect, useRef, useState } from "react";

import ErrorAlert from "@/components/ErrorAlert";
import SectionCard from "@/components/SectionCard";
import SqlBlock from "@/components/SqlBlock";
import { updateMetric } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { MetricDetail, Role } from "@/lib/types";

import { actionErrorText } from "./metricActionError";

const BOUNDARY_LABELS: Record<string, string> = {
  tax_basis: "含税口径",
  period_basis: "期间口径",
  entity_scope: "主体范围",
  currency: "币种",
  internal_trade: "内部交易",
  note: "其他说明",
};

interface EditForm {
  definition: string;
  formula_text: string;
  formula_sql: string;
  owner: string;
  business_domain: string;
  unit: string;
  sensitivity_level: string;
  boundaryNote: string;
}

function toEditForm(metric: MetricDetail): EditForm {
  return {
    definition: metric.definition,
    formula_text: metric.formula_text,
    formula_sql: metric.formula_sql,
    owner: metric.owner,
    business_domain: metric.business_domain,
    unit: metric.unit,
    sensitivity_level: metric.sensitivity_level,
    boundaryNote: metric.boundary?.note ?? "",
  };
}

function isDirty(form: EditForm, metric: MetricDetail): boolean {
  const base = toEditForm(metric);
  return (Object.keys(base) as (keyof EditForm)[]).some((key) => form[key] !== base[key]);
}

export default function MetricDefinitionPanel({
  metric,
  role,
  editRequestToken,
  onChanged,
  onDirtyChange,
}: {
  metric: MetricDetail;
  role: Role;
  editRequestToken: number;
  onChanged: () => void | Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const canWrite = role === "admin" || role === "analyst";
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<EditForm>(() => toEditForm(metric));
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; details?: unknown } | null>(null);
  const lastEditToken = useRef(editRequestToken);

  useEffect(() => {
    if (editing) return;
    setForm(toEditForm(metric));
  }, [metric, editing]);

  useEffect(() => {
    if (editRequestToken === lastEditToken.current) return;
    lastEditToken.current = editRequestToken;
    if (!canWrite || editing) return;
    setEditing(true);
    setForm(toEditForm(metric));
    setNotice(null);
    setError(null);
  }, [editRequestToken, canWrite, editing, metric]);

  useEffect(() => {
    onDirtyChange(editing && isDirty(form, metric));
  }, [editing, form, metric, onDirtyChange]);

  useEffect(() => {
    return () => onDirtyChange(false);
  }, [onDirtyChange]);

  const startEdit = () => {
    setEditing(true);
    setForm(toEditForm(metric));
    setNotice(null);
    setError(null);
  };

  const cancelEdit = () => {
    setEditing(false);
    setForm(toEditForm(metric));
    setError(null);
  };

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    setNotice(null);
    setError(null);
    try {
      const updated = await updateMetric(metric.code, {
        definition: form.definition,
        formula_text: form.formula_text,
        formula_sql: form.formula_sql,
        owner: form.owner,
        business_domain: form.business_domain,
        unit: form.unit,
        sensitivity_level: form.sensitivity_level as
          | "public"
          | "internal"
          | "confidential"
          | "restricted",
        boundary: { ...(metric.boundary ?? {}), note: form.boundaryNote },
      });
      setEditing(false);
      setNotice(`已保存草稿版本 ${updated.draft_version?.version ?? ""}，认证后生效。`);
      await onChanged();
    } catch (err) {
      setError(actionErrorText(err));
    } finally {
      setSaving(false);
    }
  };

  const boundaryEntries = Object.entries(metric.boundary ?? {});

  return (
    <div className="space-y-4">
      <SectionCard
        title="口径与责任"
        description="指标定义、责任信息与公式口径；保存后生成或更新草稿版本。"
        actions={
          canWrite ? (
            editing ? (
              <button
                type="button"
                onClick={cancelEdit}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                收起编辑
              </button>
            ) : (
              <button
                type="button"
                onClick={startEdit}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                编辑指标
              </button>
            )
          ) : undefined
        }
      >
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3">
            <div className="text-xs font-medium text-slate-700">定义</div>
            <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-700">
              {metric.definition || "尚未维护定义"}
            </p>
            <dl className="mt-3 grid gap-1 text-xs text-slate-600">
              <div>
                <dt className="inline text-slate-500">负责人：</dt>
                <dd className="inline text-slate-700">{metric.owner || "待指定"}</dd>
              </div>
              <div>
                <dt className="inline text-slate-500">业务域：</dt>
                <dd className="inline text-slate-700">{metric.business_domain || "未分域"}</dd>
              </div>
              <div>
                <dt className="inline text-slate-500">单位：</dt>
                <dd className="inline text-slate-700">{metric.unit || "无单位"}</dd>
              </div>
              <div>
                <dt className="inline text-slate-500">敏感级别：</dt>
                <dd className="inline text-slate-700">{metric.sensitivity_level}</dd>
              </div>
              <div>
                <dt className="inline text-slate-500">关联本体术语：</dt>
                <dd className="inline text-slate-700">{metric.term_code || "未关联"}</dd>
              </div>
              <div>
                <dt className="inline text-slate-500">准则依据：</dt>
                <dd className="inline text-slate-700">{metric.basis || "未挂载依据"}</dd>
              </div>
              <div>
                <dt className="inline text-slate-500">来源：</dt>
                <dd className="inline text-slate-700">
                  {metric.source_name ? (
                    metric.source_url ? (
                      <a
                        href={metric.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-2 hover:text-slate-900"
                      >
                        {metric.source_name}
                      </a>
                    ) : (
                      metric.source_name
                    )
                  ) : (
                    "未挂载来源"
                  )}
                </dd>
              </div>
              <div>
                <dt className="inline text-slate-500">更新时间：</dt>
                <dd className="inline text-slate-700">{formatDate(metric.updated_at)}</dd>
              </div>
            </dl>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3">
            <div className="text-xs font-medium text-slate-700">公式</div>
            <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-700">
              {metric.formula_text || "尚未维护公式文本"}
            </p>
            <div className="mt-2">
              <SqlBlock sql={metric.formula_sql} title="指标公式 SQL（只读）" />
            </div>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="口径边界" description="含税口径、期间口径、主体范围与内部交易等约定。">
        {boundaryEntries.length === 0 ? (
          <div className="text-xs text-slate-500">未维护边界</div>
        ) : (
          <dl className="grid gap-2 text-xs sm:grid-cols-2">
            {boundaryEntries.map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <dt className="shrink-0 text-slate-500">{BOUNDARY_LABELS[key] ?? key}</dt>
                <dd className="text-slate-700">{value || "—"}</dd>
              </div>
            ))}
          </dl>
        )}
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

      {editing && canWrite ? (
        <SectionCard
          title="编辑指标"
          description="编辑中，保存后生成或更新草稿版本；认证后当前版本才切换。code 不可修改。"
          actions={
            <div className="flex gap-2">
              <button
                type="button"
                onClick={cancelEdit}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={saving}
                className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {saving ? "保存中…" : "保存草稿"}
              </button>
            </div>
          }
        >
          <div className="grid gap-3">
            <label className="text-xs text-slate-600">
              定义
              <textarea
                value={form.definition}
                onChange={(event) => setForm({ ...form, definition: event.target.value })}
                rows={3}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm text-slate-800 outline-none focus:border-slate-500"
              />
            </label>
            <label className="text-xs text-slate-600">
              公式文本
              <input
                value={form.formula_text}
                onChange={(event) => setForm({ ...form, formula_text: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              />
            </label>
            <label className="text-xs text-slate-600">
              公式 SQL（只读守卫会校验单条 SELECT 与单标量）
              <textarea
                value={form.formula_sql}
                onChange={(event) => setForm({ ...form, formula_sql: event.target.value })}
                rows={4}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 font-mono text-[11px] text-slate-800 outline-none focus:border-slate-500"
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-slate-600">
                负责人
                <input
                  value={form.owner}
                  onChange={(event) => setForm({ ...form, owner: event.target.value })}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                />
              </label>
              <label className="text-xs text-slate-600">
                业务域
                <input
                  value={form.business_domain}
                  onChange={(event) => setForm({ ...form, business_domain: event.target.value })}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                />
              </label>
              <label className="text-xs text-slate-600">
                单位
                <input
                  value={form.unit}
                  onChange={(event) => setForm({ ...form, unit: event.target.value })}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                />
              </label>
              <label className="text-xs text-slate-600">
                敏感级别
                <select
                  value={form.sensitivity_level}
                  onChange={(event) => setForm({ ...form, sensitivity_level: event.target.value })}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                >
                  <option value="public">public</option>
                  <option value="internal">internal</option>
                  <option value="confidential">confidential</option>
                  <option value="restricted">restricted</option>
                </select>
              </label>
            </div>
            <label className="text-xs text-slate-600">
              边界说明
              <input
                value={form.boundaryNote}
                onChange={(event) => setForm({ ...form, boundaryNote: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              />
            </label>
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
