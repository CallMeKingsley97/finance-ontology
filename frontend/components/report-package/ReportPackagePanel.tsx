"use client";

import { FileSpreadsheet, Printer, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import ReportSection from "@/components/ReportSection";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { ApiError, errorMessage, generateReportPackage, reportPackageTemplates } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { downloadReportPackage } from "@/lib/report-download";
import type {
  ReportAppendix,
  ReportMetric,
  ReportPackageDocument,
  ReportPackageRequestBody,
  ReportPackageTemplate,
  ReportSection as ReportSectionType,
  Role,
} from "@/lib/types";

type Comparison = "yoy" | "mom" | "none";

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

const BOUNDARY_LABELS: Record<string, string> = {
  tax_basis: "含税口径",
  period_basis: "期间口径",
  entity_scope: "主体范围",
  currency: "币种",
  internal_trade: "内部交易",
  note: "其他说明",
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

function boundaryText(boundary: Record<string, string>): string {
  const entries = Object.entries(boundary ?? {}).filter(([, value]) => value);
  if (!entries.length) return "—";
  return entries.map(([key, value]) => `${BOUNDARY_LABELS[key] ?? key}：${value}`).join("；");
}

function errorInfo(error: unknown): {
  message: string;
  details?: unknown;
  code?: string;
  runId?: string;
  failed: string[];
} {
  if (error instanceof ApiError) {
    const details = error.details as
      | {
          run_id?: string;
          failed?: { code?: string; name?: string; message?: string }[] | string[];
        }
      | undefined;
    const failed = Array.isArray(details?.failed)
      ? details.failed
          .map((item) => {
            if (typeof item === "string") return item;
            return item.name || item.code || item.message || "";
          })
          .filter(Boolean)
      : [];
    return {
      message: error.message,
      details: error.details,
      code: error.code,
      runId: details?.run_id,
      failed,
    };
  }
  return { message: errorMessage(error), failed: [] };
}

export default function ReportPackagePanel({ role }: { role: Role }) {
  const canGenerate = role === "admin" || role === "analyst";

  const [templates, setTemplates] = useState<ReportPackageTemplate[]>([]);
  const [periods, setPeriods] = useState<{ code: string; label: string }[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState<string | null>(null);

  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
  const [period, setPeriod] = useState("");
  const [comparison, setComparison] = useState<Comparison>("yoy");

  const [doc, setDoc] = useState<ReportPackageDocument | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<ReturnType<typeof errorInfo> | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true);
    setTemplatesError(null);
    try {
      const result = await reportPackageTemplates();
      setTemplates(result.items);
      const options = Array.isArray(result.periods) ? result.periods : [];
      setPeriods(options);
      setSelectedTemplates(result.items.map((item) => item.code));
      setPeriod((current) =>
        current && options.some((item) => item.code === current) ? current : options[0]?.code ?? "",
      );
    } catch (err) {
      setTemplatesError(errorMessage(err));
      setTemplates([]);
      setPeriods([]);
    } finally {
      setTemplatesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const toggleTemplate = (code: string) => {
    setSelectedTemplates((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    );
  };

  const handleGenerate = async () => {
    if (!canGenerate) {
      setGenerateError({ message: "当前角色无报表包生成权限，请切换到 admin 或 analyst。", failed: [] });
      return;
    }
    if (!period) {
      setGenerateError({ message: "请选择报表期间。", failed: [] });
      return;
    }
    if (selectedTemplates.length === 0) {
      setGenerateError({ message: "至少选择一个报表包模板。", failed: [] });
      return;
    }
    setGenerating(true);
    setGenerateError(null);
    setDownloadError(null);
    try {
      const body: ReportPackageRequestBody = {
        period,
        templates: selectedTemplates,
        comparison,
      };
      const result = await generateReportPackage(body);
      setDoc(result);
    } catch (err) {
      setGenerateError(errorInfo(err));
    } finally {
      setGenerating(false);
    }
  };

  const handleDownload = async () => {
    if (!doc || !canGenerate) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadReportPackage({
        period: doc.period.code,
        templates: doc.templates.map((item) => item.code),
        comparison: (doc.period.comparison as Comparison | null) ?? "none",
      });
    } catch (err) {
      setDownloadError(errorMessage(err));
    } finally {
      setDownloading(false);
    }
  };

  const appendixMetrics: ReportMetric[] = (doc?.appendix.metrics ?? []).map((metric) => ({
    code: metric.code,
    name: metric.name,
    formula: metric.definition || metric.formula_text || "—",
    sql: metric.formula_sql || "—",
  }));
  const appendix: ReportAppendix = {
    metrics: appendixMetrics,
    assumptions: doc?.appendix.assumptions ?? [],
    ontology_version: doc?.appendix.ontology_version,
  };
  const appendixSection: ReportSectionType = {
    key: "package-appendix",
    title: "附录：指标口径",
    type: "appendix",
    data: appendix,
  };

  const failedQualityResults = (doc?.quality.results ?? []).filter(
    (result) => result.status === "failed" || result.status === "error",
  );

  return (
    <div className="space-y-4">
      <SectionCard
        title="月度报表包"
        description="选择期间与模板生成三表报表包；生成前自动执行质量检查，blocker 失败将中止生成。"
        actions={
          doc ? (
            <div className="no-print flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleDownload()}
                disabled={!canGenerate || downloading}
                title={canGenerate ? "下载当前报表包 Excel" : "仅 admin/analyst 可导出"}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <FileSpreadsheet size={13} aria-hidden />
                {downloading ? "下载中…" : "下载 Excel"}
              </button>
              <button
                type="button"
                onClick={() => window.print()}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Printer size={13} aria-hidden />
                打印 / 另存 PDF
              </button>
            </div>
          ) : undefined
        }
      >
        {templatesLoading ? <LoadingState text="正在读取报表包模板…" /> : null}
        {templatesError ? <ErrorAlert message={templatesError} onRetry={() => void loadTemplates()} /> : null}

        {!templatesLoading && !templatesError && templates.length === 0 ? (
          <EmptyState
            title="暂无报表包模板"
            description="后端未返回报表包模板，请检查服务初始化日志。"
          />
        ) : null}

        {templates.length > 0 ? (
          <div className="space-y-3">
            {!canGenerate ? (
              <div className="no-print rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                当前角色为 {role}：可查看模板与已生成预览，生成与导出仅 admin/analyst 可用。
              </div>
            ) : null}
            <div className="grid gap-2 md:grid-cols-3">
              {templates.map((template) => {
                const checked = selectedTemplates.includes(template.code);
                return (
                  <label
                    key={template.code}
                    className={[
                      "flex cursor-pointer items-start gap-2 rounded-xl border p-3 transition-colors",
                      checked ? "border-slate-900 bg-slate-50" : "border-slate-200 bg-white hover:bg-slate-50",
                    ].join(" ")}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTemplate(template.code)}
                      className="mt-0.5 h-4 w-4 rounded border-slate-300"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-slate-800">{template.name}</span>
                      <span className="mt-0.5 block text-[11px] leading-relaxed text-slate-500">
                        {template.description}
                      </span>
                      <span className="mt-1 block font-mono text-[10px] text-slate-400">{template.code}</span>
                    </span>
                  </label>
                );
              })}
            </div>

            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <label className="text-xs text-slate-600">
                期间
                <select
                  value={period}
                  onChange={(event) => setPeriod(event.target.value)}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                >
                  {periods.length === 0 ? <option value="">暂无可用期间</option> : null}
                  {periods.map((item) => (
                    <option key={item.code} value={item.code}>
                      {item.label}（{item.code}）
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-600">
                对比方式
                <select
                  value={comparison}
                  onChange={(event) => setComparison(event.target.value as Comparison)}
                  className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                >
                  <option value="yoy">同比 yoy</option>
                  <option value="mom">环比 mom</option>
                  <option value="none">不对比 none</option>
                </select>
              </label>
              <div className="flex items-end">
                <button
                  type="button"
                  onClick={() => void handleGenerate()}
                  disabled={generating || !canGenerate || !period || selectedTemplates.length === 0}
                  title={
                    canGenerate
                      ? selectedTemplates.length === 0
                        ? "至少选择一个模板"
                        : "生成报表包"
                      : "仅 admin/analyst 可生成"
                  }
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {generating ? (
                    <RefreshCw size={13} className="animate-spin" aria-hidden />
                  ) : (
                    <FileSpreadsheet size={13} aria-hidden />
                  )}
                  {generating ? "生成中…" : "生成报表包"}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </SectionCard>

      {generateError ? (
        <div className="space-y-2">
          <ErrorAlert message={generateError.message} details={generateError.details ?? undefined} />
          {generateError.code === "QUALITY_BLOCKER_FAILED" ? (
            <div className="no-print rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
              {generateError.failed.length ? (
                <div>未通过的 blocker 规则：{generateError.failed.join("、")}</div>
              ) : (
                <div>存在 blocker 质量规则未通过，报表包已中止生成。</div>
              )}
              <Link
                href={`/governance?tab=quality${generateError.runId ? `&run=${encodeURIComponent(generateError.runId)}` : ""}`}
                className="mt-1 inline-block font-medium text-rose-700 underline underline-offset-2 hover:text-rose-800"
              >
                去质量 Tab 查看失败详情
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}
      {downloadError ? <ErrorAlert message={downloadError} /> : null}

      {!doc && !generating && !generateError && templates.length > 0 ? (
        <EmptyState
          title="报表包尚未生成"
          description="选择期间与模板后点击“生成报表包”；成功后可预览三表、勾稽结果与指标口径附录。"
        />
      ) : null}

      {doc ? (
        <div className="space-y-4">
          <div className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h1 className="text-lg font-semibold tracking-tight text-slate-900">{doc.title}</h1>
                <p className="mt-1 text-xs text-slate-500">
                  期间 {doc.period.label}（{doc.period.code}） · 对比 {doc.period.comparison ?? "无"} · 生成于{" "}
                  {formatDate(doc.generated_at)} · 角色 {doc.meta.role} · 耗时 {doc.meta.duration_ms} ms
                </p>
              </div>
              <span
                className={[
                  "no-print inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
                  doc.reconciliation.passed
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-rose-200 bg-rose-50 text-rose-700",
                ].join(" ")}
              >
                勾稽校验 {doc.reconciliation.passed ? "通过" : "未通过"}
              </span>
            </div>
            {doc.warnings && doc.warnings.length > 0 ? (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {doc.warnings.map((warning, index) => (
                  <div key={index}>{warning}</div>
                ))}
              </div>
            ) : null}
            {(doc.meta.masked_columns ?? []).length > 0 ? (
              <div className="mt-2 text-[11px] text-slate-500">
                已掩码列：{(doc.meta.masked_columns ?? []).join("、")}（由后端按角色统一处理）
              </div>
            ) : null}
          </div>

          <SectionCard title="质量前置检查" description="本次报表包生成触发的质量运行结果">
            <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-slate-600">
              <span className="font-mono">{doc.quality.run_id ?? "无运行 ID"}</span>
              <StatusBadge status={doc.quality.status} />
              <span>
                通过 {doc.quality.rules_passed} / 失败 {doc.quality.rules_failed} / 跳过 {doc.quality.rules_skipped}
              </span>
              <span className={doc.quality.blocker_failed > 0 ? "font-semibold text-rose-600" : ""}>
                Blocker {doc.quality.blocker_failed}
              </span>
            </div>
            {doc.quality.results.length === 0 ? (
              <EmptyState title="无质量结果" description="本次运行没有质量规则结果，可能全部规则被跳过。" />
            ) : (
              <DataTable
                columns={[
                  { key: "rule_name", label: "规则" },
                  {
                    key: "rule_type",
                    label: "类型",
                  },
                  {
                    key: "severity",
                    label: "严重级别",
                    align: "center",
                    render: (_row, value) => <SeverityBadge severity={String(value ?? "")} />,
                  },
                  {
                    key: "status",
                    label: "结果",
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
                ]}
                rows={doc.quality.results.map((result) => ({ ...result }))}
                emptyText="无质量结果"
                maxHeight={320}
              />
            )}
            {failedQualityResults.length > 0 ? (
              <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                未通过规则：{failedQualityResults.map((result) => result.rule_name || result.rule_code).join("、")}
                （非 blocker 失败不阻断生成）
              </div>
            ) : null}
          </SectionCard>

          <SectionCard title="勾稽校验" description="5 条固定勾稽关系，与旧报告口径一致">
            <DataTable
              columns={[
                { key: "name", label: "校验项" },
                {
                  key: "left",
                  label: "左侧",
                  align: "right",
                  render: (_row, value) => formatNumber(value, 2),
                },
                {
                  key: "right",
                  label: "右侧",
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
                  key: "passed",
                  label: "结果",
                  align: "center",
                  render: (_row, value) => <StatusBadge status={value ? "passed" : "failed"} />,
                },
              ]}
              rows={(doc.reconciliation.checks ?? []).map((check) => ({
                ...check,
              }))}
              emptyText="无勾稽检查"
            />
          </SectionCard>

          {doc.templates.map((template) => (
            <div key={template.code} className="space-y-3">
              <h2 className="px-1 text-sm font-semibold text-slate-700">
                {template.name}
                <span className="ml-2 font-mono text-[10px] font-normal text-slate-400">{template.code}</span>
              </h2>
              {template.sections.length === 0 ? (
                <EmptyState title="模板无章节" description="该模板未返回 sections。" />
              ) : (
                template.sections.map((section) => (
                  <ReportSection key={`${template.code}-${section.key}`} section={section} appendix={appendix} />
                ))
              )}
            </div>
          ))}

          <ReportSection section={appendixSection} appendix={appendix} />

          {doc.appendix.metrics.length > 0 ? (
            <SectionCard title="附录明细：版本与边界" description="指标口径的责任人与口径边界，便于审计追溯">
              <DataTable
                columns={[
                  { key: "code", label: "指标编码" },
                  { key: "name", label: "指标" },
                  { key: "version", label: "版本" },
                  { key: "owner", label: "负责人" },
                  {
                    key: "boundary",
                    label: "边界",
                    render: (_row, value) => String(value || "—"),
                  },
                ]}
                rows={doc.appendix.metrics.map((metric) => ({
                  code: metric.code,
                  name: metric.name,
                  version: metric.version || "—",
                  owner: metric.owner || "—",
                  boundary: boundaryText(metric.boundary),
                }))}
                emptyText="暂无指标口径"
                maxHeight={320}
              />
            </SectionCard>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
