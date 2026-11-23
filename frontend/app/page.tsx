"use client";

import { ArrowRight, Database, FileText, MessageSquare, Network, Shield } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import ChartPanel from "@/components/ChartPanel";
import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import KpiCard from "@/components/KpiCard";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import { dashboard, errorMessage, useDemoRole } from "@/lib/api";
import type { DashboardSummary } from "@/lib/types";

const QUICK_LINKS = [
  { href: "/sources", label: "数据源与播种", icon: Database },
  { href: "/metadata", label: "元数据浏览", icon: Database },
  { href: "/ontology", label: "本体生成与校准", icon: Network },
  { href: "/query", label: "自然语言问数", icon: MessageSquare },
  { href: "/reports", label: "财务报告", icon: FileText },
  { href: "/security", label: "角色与脱敏", icon: Shield },
];

export default function DashboardPage() {
  const [role] = useDemoRole();
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await dashboard());
    } catch (err) {
      setError(errorMessage(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !data) return <LoadingState text="正在加载仪表盘…" />;
  if (error && !data) {
    return (
      <div className="space-y-4">
        <ErrorAlert message={error} onRetry={load} />
        <EmptyState
          title="数据库可能尚未播种"
          description="请先前往数据源页面执行“重新播种”，生成 mock 财务数据。"
          action={
            <Link
              href="/sources"
              className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
            >
              前往数据源
              <ArrowRight size={14} aria-hidden />
            </Link>
          }
        />
      </div>
    );
  }
  if (!data) return <EmptyState title="暂无仪表盘数据" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">经营仪表盘</h1>
          <p className="mt-1 text-sm text-slate-500">
            最新期间 {data.latest_period ?? "—"} · 当前演示角色 {role}
          </p>
        </div>
        <div
          className={[
            "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
            data.reconciliation.passed
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-rose-200 bg-rose-50 text-rose-700",
          ].join(" ")}
        >
          勾稽校验 {data.reconciliation.passed ? "通过" : "未通过"}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        {data.kpis.map((item) => (
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

      <div className="grid gap-5 lg:grid-cols-3">
        <SectionCard
          title="近 12 期收入与净利润"
          description="按期间汇总的收入类科目发生额与净利润"
          className="lg:col-span-2"
        >
          <ChartPanel
            type="line"
            labels={data.trend.map((item) => item.period)}
            series={[
              { name: "营业收入", data: data.trend.map((item) => item.revenue) },
              { name: "净利润", data: data.trend.map((item) => item.net_profit) },
            ]}
            height={300}
          />
        </SectionCard>

        <SectionCard title="模块状态" description="当前数据库中的对象数量">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            {[
              ["业务表", data.counts.tables],
              ["本体术语", data.counts.terms],
              ["关系", data.counts.relations],
              ["SQL 属性", data.counts.sql_attributes],
              ["问数审计", data.counts.queries],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <dt className="text-xs text-slate-500">{label}</dt>
                <dd className="mt-1 text-lg font-semibold text-slate-900">{String(value)}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 space-y-2">
            {QUICK_LINKS.map((link) => {
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  <span className="inline-flex items-center gap-2">
                    <Icon size={14} aria-hidden />
                    {link.label}
                  </span>
                  <ArrowRight size={13} className="text-slate-400" aria-hidden />
                </Link>
              );
            })}
          </div>
        </SectionCard>
      </div>

      <SectionCard title="勾稽校验明细" description="资产=负债+权益、流动/非流动拆分、利润表勾稽">
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
          rows={(data.reconciliation.checks ?? []).map((check) => ({            name: check.name,
            left: check.left.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
            right: check.right.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
            diff: check.diff.toFixed(2),
            passed: check.passed,
          }))}
          emptyText="暂无校验结果"
        />
      </SectionCard>
    </div>
  );
}
