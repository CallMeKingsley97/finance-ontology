"use client";

import { FileText, Printer, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import ReportPackagePanel from "@/components/report-package/ReportPackagePanel";
import ReportSection from "@/components/ReportSection";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { errorMessage, generateReport, health, reportTemplates, useDemoRole } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ReportDocument, ReportTemplate } from "@/lib/types";

type Comparison = "yoy" | "mom" | "none";

export default function ReportsPage() {
  const [role] = useDemoRole();
  const canGenerate = role === "admin" || role === "analyst";

  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [periods, setPeriods] = useState<{ code: string; label: string }[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState<string | null>(null);

  const [templateCode, setTemplateCode] = useState("");
  const [period, setPeriod] = useState("");
  const [comparison, setComparison] = useState<Comparison>("yoy");

  const [doc, setDoc] = useState<ReportDocument | null>(null);
  const [generateLoading, setGenerateLoading] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true);
    setTemplatesError(null);
    try {
      const data = await reportTemplates();
      setTemplates(data.items);
      let periodOptions = Array.isArray(data.periods) ? data.periods : [];
      if (periodOptions.length === 0) {
        try {
          const info = await health();
          if (info.latest_period) {
            periodOptions = [{ code: info.latest_period, label: info.latest_period }];
          }
        } catch {
          periodOptions = [];
        }
      }
      setPeriods(periodOptions);
      setTemplateCode((current) =>
        current && data.items.some((item) => item.code === current) ? current : data.items[0]?.code ?? "",
      );
      setPeriod((current) =>
        current && periodOptions.some((item) => item.code === current)
          ? current
          : periodOptions[0]?.code ?? "",
      );
    } catch (err) {
      setTemplatesError(errorMessage(err));
      setTemplates([]);
      setPeriods([]);
    } finally {
      setTemplatesLoading(false);
    }
  }, [role]);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const handleGenerate = async () => {
    if (!templateCode || !period) return;
    if (!canGenerate) {
      setGenerateError("当前角色无报告生成权限，请切换到 admin 或 analyst。");
      return;
    }
    setGenerateLoading(true);
    setGenerateError(null);
    try {
      const result = await generateReport({
        template_code: templateCode,
        period,
        comparison,
      });
      setDoc(result);
    } catch (err) {
      setGenerateError(errorMessage(err));
      setDoc(null);
    } finally {
      setGenerateLoading(false);
    }
  };

  const selectedTemplate = templates.find((item) => item.code === templateCode);
  const hasAppendixSection = doc?.sections.some((section) => section.type === "appendix") ?? false;

  return (
    <div className="space-y-5">
      <div className="no-print flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">财务报告</h1>
          <p className="mt-1 text-sm text-slate-500">
            两个模板：财务状况分析与损益分析；含 KPI、表格、图表、勾稽校验与口径附录，支持浏览器打印。
          </p>
        </div>
        {doc ? (
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <Printer size={14} aria-hidden />
            打印 / 另存 PDF
          </button>
        ) : null}
      </div>

      {!canGenerate ? (
        <div className="no-print rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          当前角色为 {role}，报告生成仅 admin / analyst 可用；你仍可查看模板与已有报告结果。
        </div>
      ) : null}

      {templatesLoading ? <LoadingState text="正在读取报告模板…" /> : null}
      {templatesError ? <ErrorAlert message={templatesError} onRetry={loadTemplates} /> : null}

      {templates.length > 0 ? (
        <SectionCard
          title="模板与参数"
          description={selectedTemplate?.description}
          actions={
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generateLoading || !canGenerate || !templateCode || !period}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {generateLoading ? (
                <RefreshCw size={13} className="animate-spin" aria-hidden />
              ) : (
                <FileText size={13} aria-hidden />
              )}
              生成报告
            </button>
          }
        >
          <div className="grid gap-3 md:grid-cols-2">
            {templates.map((template) => {
              const active = template.code === templateCode;
              return (
                <button
                  key={template.code}
                  type="button"
                  onClick={() => setTemplateCode(template.code)}
                  className={[
                    "rounded-xl border p-4 text-left transition-colors",
                    active
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
                  ].join(" ")}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold">{template.name}</span>
                    <span className={`font-mono text-[10px] ${active ? "text-slate-300" : "text-slate-400"}`}>
                      {template.code}
                    </span>
                  </div>
                  <p className={`mt-1 text-xs leading-relaxed ${active ? "text-slate-200" : "text-slate-500"}`}>
                    {template.description}
                  </p>
                  <div className={`mt-2 text-[11px] ${active ? "text-slate-300" : "text-slate-400"}`}>
                    {template.sections.length} 个章节
                  </div>
                </button>
              );
            })}
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3">
            <label className="text-xs text-slate-600">
              期间
              <select
                value={period}
                onChange={(event) => setPeriod(event.target.value)}
                className="mt-1 block min-w-36 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              >
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
                className="mt-1 block min-w-32 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
              >
                <option value="yoy">同比 yoy</option>
                <option value="mom">环比 mom</option>
                <option value="none">不对比 none</option>
              </select>
            </label>
          </div>
        </SectionCard>
      ) : null}

      {generateError ? <ErrorAlert message={generateError} onRetry={handleGenerate} /> : null}

      {!doc && !generateLoading && !generateError ? (
        <EmptyState
          title="尚未生成报告"
          description="选择模板、期间与对比方式后点击“生成报告”。勾稽校验失败时仍会返回报告，并以红色徽标提示。"
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
              <div
                className={[
                  "no-print inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
                  doc.reconciliation.passed
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-rose-200 bg-rose-50 text-rose-700",
                ].join(" ")}
              >
                勾稽校验 {doc.reconciliation.passed ? "通过" : "未通过"}
              </div>
            </div>

            {doc.warnings && doc.warnings.length > 0 ? (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {doc.warnings.map((warning, index) => (
                  <div key={index}>{warning}</div>
                ))}
              </div>
            ) : null}
          </div>

          <SectionCard title="勾稽校验" description="至少覆盖资产=负债+权益、流动/非流动拆分与利润表勾稽">
            <DataTable
              columns={[
                { key: "name", label: "校验项" },
                { key: "left", label: "左侧", align: "right" },
                { key: "right", label: "右侧", align: "right" },
                { key: "diff", label: "差异", align: "right" },
                {
                  key: "passed",
                  label: "结果",
                  align: "center",
                  render: (_row, value) => <StatusBadge status={value ? "passed" : "failed"} />,
                },
              ]}
              rows={(doc.reconciliation.checks ?? []).map((check) => ({
                name: check.name,
                left: check.left.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
                right: check.right.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
                diff: check.diff.toFixed(2),
                passed: check.passed,
              }))}
            />
          </SectionCard>

          {doc.sections.map((section) => (
            <ReportSection key={section.key} section={section} appendix={doc.appendix} />
          ))}

          {!hasAppendixSection ? (
            <ReportSection
              section={{
                key: "appendix",
                title: "附录：指标口径与假设",
                type: "appendix",
                data: {
                  metrics: doc.appendix.metrics,
                  assumptions: doc.appendix.assumptions,
                  ontology_version: doc.appendix.ontology_version,
                },
              }}
              appendix={doc.appendix}
            />
          ) : null}

          {(doc.meta.masked_columns ?? []).length > 0 ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              本报告已掩码列：{(doc.meta.masked_columns ?? []).join("、")}（由后端按角色统一处理）
            </div>
          ) : null}
        </div>
      ) : null}

      <ReportPackagePanel role={role} />
    </div>
  );
}
