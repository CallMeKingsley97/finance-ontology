"use client";

import type { ReportAppendix, ReportSection as ReportSectionType } from "@/lib/types";

import ChartPanel from "./ChartPanel";
import DataTable from "./DataTable";
import EmptyState from "./EmptyState";
import KpiCard from "./KpiCard";

export default function ReportSection({
  section,
  appendix,
}: {
  section: ReportSectionType;
  appendix?: ReportAppendix;
}) {
  const kpiItems = section.data?.items ?? [];
  const chart = section.chart;
  const labels =
    chart?.labels ?? chart?.series?.[0]?.data.map((_, index) => String(index + 1)) ?? [];

  return (
    <section className="report-section card p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">{section.title}</h2>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-500">
          {section.type}
        </span>
      </div>

      {section.type === "kpi" ? (
        kpiItems.length ? (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {kpiItems.map((item) => (
              <KpiCard
                key={item.key}
                label={item.label}
                value={item.value ?? undefined}
                unit={item.unit}
                formatted={item.formatted}
                changePct={item.change_pct}
                trend={item.trend}
              />
            ))}
          </div>
        ) : (
          <EmptyState title="暂无 KPI" />
        )
      ) : null}

      {section.type === "table" ? (
        section.table ? (
          <DataTable
            columns={section.table.columns.map((column) => ({
              key: column.key,
              label: column.label,
              align: typeof section.table?.rows?.[0]?.[column.key] === "number" ? "right" : "left",
            }))}
            rows={section.table.rows}
            maxHeight={360}
          />
        ) : (
          <EmptyState title="暂无表格数据" />
        )
      ) : null}

      {section.type === "chart" && chart ? (
        <ChartPanel type={chart.type} labels={labels} series={chart.series ?? []} height={300} />
      ) : null}

      {section.type === "text" ? (
        <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700">{section.text ?? "—"}</p>
      ) : null}

      {section.type === "appendix" ? (
        <div className="space-y-4">
          <DataTable
            columns={[
              { key: "code", label: "指标编码" },
              { key: "name", label: "指标" },
              { key: "formula", label: "口径" },
              { key: "basis", label: "依据" },
              { key: "source_name", label: "来源" },
              {
                key: "sql",
                label: "SQL",
                render: (_row, value) => (
                  <code
                    className="block max-w-md truncate text-[10px] text-slate-500"
                    title={String(value ?? "")}
                  >
                    {String(value ?? "—")}
                  </code>
                ),
              },
            ]}
            rows={(section.data?.metrics ?? appendix?.metrics ?? []).map((metric) => ({
              code: metric.code,
              name: metric.name,
              formula: metric.formula,
              basis: metric.basis ?? "",
              source_name: metric.source_name ?? "",
              sql: metric.sql,
            }))}
            emptyText="暂无指标口径"
            maxHeight={300}
          />
          {(section.data?.assumptions ?? appendix?.assumptions ?? []).length ? (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-600">假设与边界</div>
              <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-slate-600">
                {(section.data?.assumptions ?? appendix?.assumptions ?? []).map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {(appendix?.knowledge_sources ?? section.data?.knowledge_sources ?? []).length ? (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-600">知识来源与许可</div>
              <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-slate-600">
                {(appendix?.knowledge_sources ?? section.data?.knowledge_sources ?? []).map(
                  (source) => (
                    <li key={source.source_id}>
                      <span className="text-slate-700">{source.name}</span>
                      {source.license ? <span> · {source.license}</span> : null}
                      {source.notice ? (
                        <div className="mt-0.5 text-[10px] leading-relaxed text-slate-500">
                          {source.notice}
                        </div>
                      ) : null}
                    </li>
                  ),
                )}
              </ul>
            </div>
          ) : null}
          {section.data?.ontology_version ? (
            <div className="text-xs text-slate-500">本体版本：{section.data.ontology_version}</div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
