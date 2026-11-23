"use client";

import { Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import KpiCard from "@/components/KpiCard";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import SqlBlock from "@/components/SqlBlock";
import StatusBadge from "@/components/StatusBadge";
import {
  ApiError,
  errorMessage,
  qualityAlerts,
  qualityRules,
  qualityRun,
  qualityRuns,
  qualitySummary,
  runQuality,
  runQualityRule,
  updateQualityRule,
} from "@/lib/api";
import { formatNumber, formatPct, formatRelativeTime } from "@/lib/format";
import type {
  QualityAlertListResponse,
  QualityResult,
  QualityRule,
  QualityRuleBody,
  QualityRun,
  QualitySummary,
  Role,
} from "@/lib/types";

const RULE_TYPE_LABELS: Record<string, string> = {
  reconciliation: "勾稽",
  completeness: "完整性",
  uniqueness: "唯一性",
  range: "范围",
  custom_sql: "自定义 SQL",
};

const TARGET_LABELS: Record<string, string> = {
  business: "业务库",
  staging: "暂存库",
};

const SCHEDULE_LABELS: Record<string, string> = {
  manual: "手动",
  sync: "同步后",
  report: "报告前",
  sync_report: "同步+报告",
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "手动",
  sync: "同步",
  report: "报告",
  schedule: "调度",
};

const SEVERITY_STYLES: Record<string, string> = {
  info: "border-slate-200 bg-slate-50 text-slate-600",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  blocker: "border-rose-200 bg-rose-50 text-rose-700",
};

const SEVERITY_LABELS: Record<string, string> = {
  info: "提示",
  warning: "警告",
  blocker: "阻断",
};

function SeverityBadge({ severity }: { severity: string }) {
  const key = String(severity ?? "").toLowerCase();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        SEVERITY_STYLES[key] ?? SEVERITY_STYLES.info
      }`}
    >
      {SEVERITY_LABELS[key] ?? severity}
    </span>
  );
}

function feedbackOf(error: unknown): { message: string; details?: unknown } {
  if (error instanceof ApiError) return { message: error.message, details: error.details };
  return { message: errorMessage(error) };
}

interface RuleForm {
  name: string;
  severity: "info" | "warning" | "blocker";
  schedule: "manual" | "sync" | "report" | "sync_report";
  enabled: boolean;
  tolerance: number;
  owner: string;
  description: string;
}

function formOf(rule: QualityRule): RuleForm {
  return {
    name: rule.name,
    severity: (rule.severity as RuleForm["severity"]) || "warning",
    schedule: (rule.schedule as RuleForm["schedule"]) || "manual",
    enabled: Boolean(rule.enabled),
    tolerance: Number(rule.tolerance ?? 0),
    owner: rule.owner,
    description: rule.description,
  };
}

function runDuration(run: QualityRun): string {
  if (!run.finished_at) return "—";
  const started = new Date(run.started_at).getTime();
  const finished = new Date(run.finished_at).getTime();
  if (!Number.isFinite(started) || !Number.isFinite(finished)) return "—";
  return `${Math.max(0, Math.round(finished - started))} ms`;
}

export default function QualityPanel({ role, onChanged }: { role: Role; onChanged: () => void }) {
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [rules, setRules] = useState<QualityRule[]>([]);
  const [runs, setRuns] = useState<QualityRun[]>([]);
  const [alerts, setAlerts] = useState<QualityResult[]>([]);
  const [latestRun, setLatestRun] = useState<QualityRun | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; details?: unknown } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [form, setForm] = useState<RuleForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [runningRule, setRunningRule] = useState<string | null>(null);
  const [runningAll, setRunningAll] = useState(false);
  const [actionError, setActionError] = useState<{ message: string; details?: unknown } | null>(null);

  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<QualityRun | null>(null);
  const [expandedLoading, setExpandedLoading] = useState(false);
  const [expandedError, setExpandedError] = useState<string | null>(null);

  const canWrite = role === "admin" || role === "analyst";

  const loadAll = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [summaryResult, rulesResult, runsResult, alertsResult] = await Promise.all([
        qualitySummary(),
        qualityRules(),
        qualityRuns({ limit: 10 }),
        qualityAlerts({ days: 7, limit: 20 }),
      ]);
      setSummary(summaryResult);
      setRules(rulesResult.items);
      setRuns(runsResult.items);
      setAlerts((alertsResult as QualityAlertListResponse).items);
      setError(null);
      if (runsResult.items.length) {
        try {
          const detail = await qualityRun(runsResult.items[0].run_id);
          setLatestRun(detail);
        } catch {
          setLatestRun(null);
        }
      } else {
        setLatestRun(null);
      }
    } catch (err) {
      setError(feedbackOf(err));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (selectedCode && rules.some((rule) => rule.code === selectedCode)) return;
    if (!rules.length) {
      setSelectedCode(null);
      setForm(null);
      return;
    }
    setSelectedCode(rules[0].code);
  }, [rules, selectedCode]);

  const selectedRule = rules.find((rule) => rule.code === selectedCode) ?? null;

  useEffect(() => {
    setForm(selectedRule ? formOf(selectedRule) : null);
  }, [selectedRule?.code, selectedRule?.updated_at]);

  useEffect(() => {
    if (!expandedRunId) {
      setExpandedRun(null);
      setExpandedError(null);
      return;
    }
    let cancelled = false;
    setExpandedLoading(true);
    qualityRun(expandedRunId)
      .then((result) => {
        if (!cancelled) {
          setExpandedRun(result);
          setExpandedError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setExpandedRun(null);
          setExpandedError(errorMessage(err));
        }
      })
      .finally(() => {
        if (!cancelled) setExpandedLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [expandedRunId]);

  const latestResults = new Map<string, QualityResult>();
  (latestRun?.results ?? []).forEach((result) => latestResults.set(result.rule_code, result));

  const refreshAfterAction = async () => {
    await loadAll(true);
    try {
      onChanged();
    } catch {
      // 父级刷新失败不影响本面板展示
    }
  };

  const handleRunAll = async () => {
    setRunningAll(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await runQuality({});
      setNotice(
        `质量检查完成：${result.status}，通过 ${result.rules_passed} / 失败 ${result.rules_failed} / 跳过 ${result.rules_skipped}，blocker ${result.blocker_failed}。`,
      );
      await refreshAfterAction();
    } catch (err) {
      setActionError(feedbackOf(err));
    } finally {
      setRunningAll(false);
    }
  };

  const handleRunRule = async (code: string) => {
    setRunningRule(code);
    setActionError(null);
    setNotice(null);
    try {
      const result = await runQualityRule(code, {});
      setNotice(`规则 ${code} 执行完成：${result.status}。`);
      await refreshAfterAction();
    } catch (err) {
      setActionError(feedbackOf(err));
    } finally {
      setRunningRule(null);
    }
  };

  const handleSave = async () => {
    if (!selectedRule || !form) return;
    if (!form.name.trim()) {
      setActionError({ message: "规则名称不能为空" });
      return;
    }
    setSaving(true);
    setActionError(null);
    setNotice(null);
    try {
      const body: QualityRuleBody = {
        name: form.name.trim(),
        severity: form.severity,
        schedule: form.schedule,
        enabled: form.enabled,
        tolerance: form.tolerance,
        owner: form.owner,
        description: form.description,
      };
      const updated = await updateQualityRule(selectedRule.code, body);
      setNotice(`规则 ${updated.code} 已保存。`);
      await refreshAfterAction();
    } catch (err) {
      setActionError(feedbackOf(err));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setForm(selectedRule ? formOf(selectedRule) : null);
    setActionError(null);
  };

  const openRun = (runId: string) => {
    setExpandedRunId((current) => (current === runId ? null : runId));
  };

  const rulesEmpty = !loading && rules.length === 0;

  return (
    <div className="space-y-5">
      <SectionCard
        title="质量概况"
        description="通过率按最近 7 天的 passed/failed/error 结果计算，skipped 不计入分母。"
        actions={
          canWrite ? (
            <button
              type="button"
              onClick={() => void handleRunAll()}
              disabled={runningAll}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play size={13} aria-hidden />
              {runningAll ? "执行中…" : "立即执行质量检查"}
            </button>
          ) : undefined
        }
      >
        {loading && !summary ? (
          <LoadingState text="正在加载质量概况…" />
        ) : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiCard
              label="通过率（7 天）"
              formatted={summary?.pass_rate_7d === null || summary?.pass_rate_7d === undefined ? "—" : formatPct(summary.pass_rate_7d)}
            />
            <KpiCard label="启用规则" formatted={`${summary?.rules_enabled ?? 0} 条`} />
            <KpiCard label="失败规则（7 天）" formatted={`${summary?.failed_results_7d ?? 0} 条`} />
            <KpiCard
              label="Blocker 失败（7 天）"
              formatted={`${summary?.blocker_failed_7d ?? 0} 条`}
            />
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">
          <span>
            最近运行：
            {summary?.last_run_at ? (
              <>
                {formatRelativeTime(summary.last_run_at)} · <StatusBadge status={summary.last_run_status ?? ""} />
              </>
            ) : (
              "尚无运行"
            )}
          </span>
          {summary && summary.rules_total !== summary.rules_enabled ? (
            <span>共 {summary.rules_total} 条规则，其中 {summary.rules_total - summary.rules_enabled} 条已停用</span>
          ) : null}
          <button
            type="button"
            onClick={() => void loadAll()}
            className="inline-flex cursor-pointer items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 font-medium text-slate-600 hover:bg-slate-50"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} aria-hidden />
            刷新
          </button>
        </div>
      </SectionCard>

      {!canWrite ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          当前角色为 {role}：可查看规则、运行记录与失败明细，但不能编辑规则或执行质量检查。
        </div>
      ) : null}

      {error ? (
        <ErrorAlert
          message={error.message}
          details={error.details ?? undefined}
          onRetry={() => void loadAll()}
        />
      ) : null}
      {actionError ? <ErrorAlert message={actionError.message} details={actionError.details ?? undefined} /> : null}
      {notice ? (
        <div aria-live="polite" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
          {notice}
        </div>
      ) : null}

      <SectionCard title="规则列表" description="选择规则查看详情；严重级别 blocker 失败会阻断报表包生成。">
        {rulesEmpty ? (
          <EmptyState
            title="无质量规则"
            description="质量规则尚未初始化，请重启服务或检查 staging 库。"
          />
        ) : (
          <DataTable
            columns={[
              {
                key: "name",
                label: "规则",
                render: (row) => {
                  const active = String(row.code) === selectedCode;
                  return (
                    <button
                      type="button"
                      onClick={() => setSelectedCode(String(row.code))}
                      className={[
                        "text-left",
                        active ? "font-semibold text-slate-900" : "text-slate-700 hover:text-slate-900",
                      ].join(" ")}
                    >
                      <span className="block text-sm">{String(row.name ?? "")}</span>
                      <span className="block font-mono text-[10px] text-slate-400">{String(row.code ?? "")}</span>
                    </button>
                  );
                },
              },
              {
                key: "rule_type",
                label: "类型",
                render: (_row, value) => RULE_TYPE_LABELS[String(value ?? "")] ?? String(value ?? ""),
              },
              {
                key: "target_db",
                label: "目标",
                render: (_row, value) => TARGET_LABELS[String(value ?? "")] ?? String(value ?? ""),
              },
              {
                key: "severity",
                label: "严重级别",
                align: "center",
                render: (_row, value) => <SeverityBadge severity={String(value ?? "")} />,
              },
              {
                key: "schedule",
                label: "调度",
                render: (_row, value) => SCHEDULE_LABELS[String(value ?? "")] ?? String(value ?? ""),
              },
              {
                key: "enabled",
                label: "启用",
                align: "center",
                render: (_row, value) => (value ? "是" : "否"),
              },
              {
                key: "last_result",
                label: "最近结果",
                align: "center",
                render: (row) => {
                  const result = latestResults.get(String(row.code));
                  if (!result) return <span className="text-slate-400">—</span>;
                  return <StatusBadge status={result.status} />;
                },
              },
              {
                key: "actions",
                label: "操作",
                align: "center",
                render: (row) =>
                  canWrite ? (
                    <button
                      type="button"
                      onClick={() => void handleRunRule(String(row.code))}
                      disabled={runningRule !== null || runningAll}
                      className="text-xs font-medium text-slate-600 hover:text-slate-900 disabled:opacity-50"
                    >
                      {runningRule === String(row.code) ? "执行中…" : "单独执行"}
                    </button>
                  ) : (
                    "—"
                  ),
              },
            ]}
            rows={rules.map((rule) => ({ ...rule }))}
            emptyText="暂无质量规则"
            maxHeight={420}
          />
        )}
      </SectionCard>

      {selectedRule && form ? (
        <SectionCard
          title={`规则详情：${selectedRule.name}`}
          description={`${RULE_TYPE_LABELS[selectedRule.rule_type] ?? selectedRule.rule_type} · ${
            TARGET_LABELS[selectedRule.target_db] ?? selectedRule.target_db
          } · ${selectedRule.code}`}
          actions={
            canWrite ? (
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleReset}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void handleRunRule(selectedRule.code)}
                  disabled={runningRule !== null || runningAll}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {runningRule === selectedRule.code ? "执行中…" : "单独执行"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving}
                  className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  {saving ? "保存中…" : "保存"}
                </button>
              </div>
            ) : undefined
          }
        >
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-xs text-slate-600">
              名称
              <input
                value={form.name}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500 disabled:bg-slate-50"
              />
            </label>
            <label className="text-xs text-slate-600">
              负责人
              <input
                value={form.owner}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, owner: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500 disabled:bg-slate-50"
              />
            </label>
            <label className="text-xs text-slate-600">
              严重级别
              <select
                value={form.severity}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, severity: event.target.value as RuleForm["severity"] })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500 disabled:bg-slate-50"
              >
                <option value="info">info（提示）</option>
                <option value="warning">warning（警告）</option>
                <option value="blocker">blocker（阻断报表包）</option>
              </select>
            </label>
            <label className="text-xs text-slate-600">
              调度
              <select
                value={form.schedule}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, schedule: event.target.value as RuleForm["schedule"] })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500 disabled:bg-slate-50"
              >
                <option value="manual">manual（仅手动）</option>
                <option value="sync">sync（同步后）</option>
                <option value="report">report（报告前）</option>
                <option value="sync_report">sync_report（同步与报告前）</option>
              </select>
            </label>
            <label className="text-xs text-slate-600">
              容差
              <input
                type="number"
                min={0}
                step="0.01"
                value={form.tolerance}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, tolerance: Number(event.target.value) })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500 disabled:bg-slate-50"
              />
            </label>
            <label className="flex items-end gap-2 pb-1 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={form.enabled}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                className="h-4 w-4 rounded border-slate-300"
              />
              启用该规则
            </label>
            <label className="text-xs text-slate-600 md:col-span-2">
              描述
              <input
                value={form.description}
                disabled={!canWrite}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500 disabled:bg-slate-50"
              />
            </label>
            <div className="md:col-span-2">
              <SqlBlock
                sql={JSON.stringify(selectedRule.expression, null, 2)}
                title="规则表达式（只读，保存与执行均由后端校验）"
              />
            </div>
          </div>
        </SectionCard>
      ) : null}

      <SectionCard title="最近运行" description="手动执行与同步/报告触发的质量运行均在此留痕。">
        {runs.length === 0 ? (
          <EmptyState
            title="无质量运行"
            description="还没有质量运行。点击“立即执行质量检查”生成第一条记录。"
          />
        ) : (
          <>
            <DataTable
              columns={[
                {
                  key: "run_id",
                  label: "运行 ID",
                  render: (_row, value) => (
                    <button
                      type="button"
                      onClick={() => openRun(String(value))}
                      className="font-mono text-[11px] text-slate-700 underline-offset-2 hover:underline"
                    >
                      {String(value ?? "")}
                    </button>
                  ),
                },
                {
                  key: "trigger_type",
                  label: "触发",
                  render: (_row, value) => TRIGGER_LABELS[String(value ?? "")] ?? String(value ?? ""),
                },
                {
                  key: "status",
                  label: "状态",
                  align: "center",
                  render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
                },
                {
                  key: "totals",
                  label: "通过/失败/跳过",
                  align: "center",
                  render: (row) => `${row.rules_passed ?? 0}/${row.rules_failed ?? 0}/${row.rules_skipped ?? 0}`,
                },
                {
                  key: "blocker_failed",
                  label: "Blocker",
                  align: "center",
                  render: (_row, value) => (
                    <span className={Number(value) > 0 ? "font-semibold text-rose-600" : "text-slate-600"}>
                      {String(value ?? 0)}
                    </span>
                  ),
                },
                {
                  key: "duration",
                  label: "耗时",
                  align: "right",
                  render: (row) => runDuration(row as unknown as QualityRun),
                },
                {
                  key: "started_at",
                  label: "开始",
                  render: (_row, value) => (
                    <span title={String(value ?? "")}>{formatRelativeTime(value)}</span>
                  ),
                },
              ]}
              rows={runs.map((run) => ({ ...run, duration: runDuration(run) }))}
              emptyText="暂无运行记录"
              maxHeight={320}
            />
            {expandedRunId ? (
              <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
                {expandedLoading ? <LoadingState text="正在加载运行明细…" /> : null}
                {expandedError ? <ErrorAlert message={expandedError} /> : null}
                {expandedRun ? (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
                      <span className="font-mono">{expandedRun.run_id}</span>
                      <StatusBadge status={expandedRun.status} />
                      <span>期间：{expandedRun.period_code || "全部期间"}</span>
                      <span>触发：{TRIGGER_LABELS[expandedRun.trigger_type] ?? expandedRun.trigger_type}</span>
                      {expandedRun.error_message ? (
                        <span className="text-rose-600">{expandedRun.error_message}</span>
                      ) : null}
                    </div>
                    <DataTable
                      columns={[
                        { key: "rule_name", label: "规则" },
                        {
                          key: "status",
                          label: "状态",
                          align: "center",
                          render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
                        },
                        {
                          key: "left_value",
                          label: "左值",
                          align: "right",
                          render: (_row, value) => formatNumber(value, 2),
                        },
                        {
                          key: "right_value",
                          label: "右值",
                          align: "right",
                          render: (_row, value) => formatNumber(value, 2),
                        },
                        {
                          key: "diff",
                          label: "差异",
                          align: "right",
                          render: (_row, value) => formatNumber(value, 2),
                        },
                        {
                          key: "tolerance",
                          label: "容差",
                          align: "right",
                          render: (_row, value) => formatNumber(value, 2),
                        },
                        {
                          key: "error_message",
                          label: "错误",
                          render: (_row, value) => String(value || "—"),
                        },
                      ]}
                      rows={(expandedRun.results ?? []).map((result) => ({ ...result }))}
                      emptyText="该运行没有结果"
                      maxHeight={320}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </SectionCard>

      <SectionCard title="失败明细与站内告警" description="最近 7 天的 failed 与 error 结果，skipped 不进入告警。">
        {alerts.length === 0 ? (
          <EmptyState
            title="最近没有失败规则"
            description="数据质量正常；如需重新检查，点击“立即执行质量检查”。"
          />
        ) : (
          <DataTable
            columns={[
              {
                key: "created_at",
                label: "时间",
                render: (_row, value) => (
                  <span title={String(value ?? "")}>{formatRelativeTime(value)}</span>
                ),
              },
              {
                key: "rule_name",
                label: "规则",
                render: (row) => (
                  <span>
                    <span className="block text-sm text-slate-700">{String(row.rule_name ?? "")}</span>
                    <span className="block font-mono text-[10px] text-slate-400">{String(row.rule_code ?? "")}</span>
                  </span>
                ),
              },
              {
                key: "severity",
                label: "严重级别",
                align: "center",
                render: (_row, value) => <SeverityBadge severity={String(value ?? "")} />,
              },
              {
                key: "status",
                label: "状态",
                align: "center",
                render: (_row, value) => <StatusBadge status={String(value ?? "")} />,
              },
              {
                key: "diff",
                label: "差异",
                align: "right",
                render: (_row, value) => formatNumber(value, 2),
              },
              {
                key: "tolerance",
                label: "容差",
                align: "right",
                render: (_row, value) => formatNumber(value, 2),
              },
              {
                key: "run_id",
                label: "运行",
                align: "center",
                render: (_row, value) => (
                  <button
                    type="button"
                    onClick={() => openRun(String(value))}
                    className="font-mono text-[11px] text-slate-600 underline-offset-2 hover:underline"
                  >
                    查看运行
                  </button>
                ),
              },
            ]}
            rows={alerts.map((alert) => ({ ...alert }))}
            emptyText="暂无告警"
            maxHeight={320}
          />
        )}
      </SectionCard>
    </div>
  );
}
