"use client";

import { RefreshCw, ShieldCheck } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import ErrorAlert from "@/components/ErrorAlert";
import KpiCard from "@/components/KpiCard";
import LoadingState from "@/components/LoadingState";
import CatalogPanel from "@/components/governance/CatalogPanel";
import QualityPanel from "@/components/governance/QualityPanel";
import SyncMonitorPanel, { type ActionFeedback } from "@/components/governance/SyncMonitorPanel";
import {
  ApiError,
  catalogSummary,
  errorMessage,
  syncAll,
  syncRun,
  syncRuns,
  syncSource,
  syncSources,
  useDemoRole,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { CatalogSummary, SyncRun, SyncSourcesResponse } from "@/lib/types";

const FAILED_STATUSES = new Set(["failed", "dead_letter", "partial"]);
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

function toActionFeedback(error: unknown): ActionFeedback {
  if (error instanceof ApiError) {
    let message = error.message;
    const details = error.details as { run_id?: string } | undefined;
    if (error.code === "SYNC_ALREADY_RUNNING" && details?.run_id) {
      message = `${message}（运行 ID：${details.run_id}）`;
    }
    if (error.code === "STAGING_UNAVAILABLE") {
      message = "staging 库不可用，请检查部署配置或稍后重试。";
    }
    return { message, details: error.details };
  }
  return { message: errorMessage(error) };
}

function withinSevenDays(value: string): boolean {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return false;
  return Date.now() - timestamp <= SEVEN_DAYS_MS;
}

function GovernanceWorkbench() {
  const router = useRouter();
  const pathname = usePathname() ?? "/governance";
  const searchParams = useSearchParams();
  const [role] = useDemoRole();

  const tabParam = searchParams.get("tab");
  const tab = tabParam === "catalog" ? "catalog" : tabParam === "quality" ? "quality" : "sync";
  const selectedSource = searchParams.get("source");
  const runParam = searchParams.get("run");
  const tableParam = searchParams.get("table");
  const selectedTable = tableParam && /^\d+$/.test(tableParam) ? Number(tableParam) : null;

  const [summary, setSummary] = useState<CatalogSummary | null>(null);
  const [sourceData, setSourceData] = useState<SyncSourcesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [runFilter, setRunFilter] = useState("");
  const [failureCount, setFailureCount] = useState(0);

  const [expandedRun, setExpandedRun] = useState<SyncRun | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [runDetailError, setRunDetailError] = useState<string | null>(null);

  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [actionError, setActionError] = useState<ActionFeedback | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const setParams = useCallback(
    (updates: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      Object.entries(updates).forEach(([key, value]) => {
        if (value === null || value === "") params.delete(key);
        else params.set(key, value);
      });
      const text = params.toString();
      router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const loadOverview = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [summaryResult, sourcesResult] = await Promise.all([catalogSummary(), syncSources()]);
      setSummary(summaryResult);
      setSourceData(sourcesResult);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  const loadRuns = useCallback(
    async (quiet = false) => {
      try {
        const [list, recent] = await Promise.all([
          syncRuns({
            source_id: selectedSource ?? undefined,
            status: runFilter || undefined,
            limit: 20,
          }),
          syncRuns({ limit: 100 }),
        ]);
        setRuns(list.items);
        setFailureCount(
          recent.items.filter(
            (item) => FAILED_STATUSES.has(item.status) && withinSevenDays(item.created_at),
          ).length,
        );
        setRunsError(null);
      } catch (err) {
        setRunsError(errorMessage(err));
        if (!quiet) setRuns([]);
      }
    },
    [runFilter, selectedSource],
  );

  const loadRunDetail = useCallback(async (runId: string, quiet = false) => {
    if (!quiet) setRunDetailLoading(true);
    try {
      const result = await syncRun(runId);
      setExpandedRun(result);
      setRunDetailError(null);
    } catch (err) {
      setRunDetailError(errorMessage(err));
      setExpandedRun(null);
    } finally {
      if (!quiet) setRunDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (!runParam) {
      setExpandedRun(null);
      setRunDetailError(null);
      return;
    }
    void loadRunDetail(runParam);
  }, [runParam, loadRunDetail]);

  const hasActiveRun = runs.some((run) => run.status === "queued" || run.status === "running");
  const hasRetryingSource = (sourceData?.items ?? []).some((item) => {
    if (item.sync_status !== "failed" || !item.last_run_at) return false;
    const timestamp = new Date(item.last_run_at).getTime();
    return Number.isFinite(timestamp) && Date.now() - timestamp < 60_000;
  });
  const shouldPoll = hasActiveRun || hasRetryingSource;

  useEffect(() => {
    if (!shouldPoll) return;
    let cancelled = false;
    const tick = async () => {
      if (document.hidden || cancelled) return;
      await Promise.all([loadOverview(true), loadRuns(true)]);
      if (runParam && !cancelled) await loadRunDetail(runParam, true);
    };
    const timer = window.setInterval(() => {
      void tick();
    }, 3000);
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [shouldPoll, loadOverview, loadRuns, loadRunDetail, runParam]);

  const handleSyncSource = async (sourceId: string) => {
    setSyncingId(sourceId);
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await syncSource(sourceId);
      setActionNotice(`已提交同步任务 ${result.run_id}，运行状态将自动刷新。`);
      await Promise.all([loadRuns(true), loadOverview(true)]);
    } catch (err) {
      setActionError(toActionFeedback(err));
    } finally {
      setSyncingId(null);
    }
  };

  const handleSyncAll = async () => {
    setBatchLoading(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await syncAll({});
      const skippedText = result.skipped.length ? `，跳过 ${result.skipped.length} 个运行中源` : "";
      setActionNotice(`已提交 ${result.runs.length} 个同步任务${skippedText}。`);
      await Promise.all([loadRuns(true), loadOverview(true)]);
    } catch (err) {
      setActionError(toActionFeedback(err));
    } finally {
      setBatchLoading(false);
    }
  };

  const handleToggleRun = (runId: string) => {
    setParams({ tab: "sync", run: runParam === runId ? null : runId });
  };

  const sourceCount = sourceData?.items.length ?? 0;
  const isAdmin = role === "admin";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">治理工作台</h1>
          <p className="mt-1 text-sm text-slate-500">
            数据接入与元数据资产的一站式视图
            {summary?.last_sync_at ? ` · 最近同步 ${formatDate(summary.last_sync_at)}` : ""}
          </p>
        </div>
        <div className="no-print flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              void loadOverview();
              void loadRuns();
            }}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} aria-hidden />
            刷新
          </button>
          {isAdmin ? (
            <button
              type="button"
              onClick={() => void handleSyncAll()}
              disabled={batchLoading || sourceCount === 0}
              title={sourceCount === 0 ? "暂无可同步的外部数据源" : "同步全部已注册外部数据源"}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ShieldCheck size={13} aria-hidden />
              {batchLoading ? "提交中…" : "全部同步"}
            </button>
          ) : null}
        </div>
      </div>

      {!isAdmin ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          当前角色为 {role}，触发同步仅 admin 可用；目录编辑 admin/analyst 可用，viewer 只读。
        </div>
      ) : null}

      {error ? (
        <div role="alert">
          <ErrorAlert
            message={error}
            onRetry={() => {
              void loadOverview();
              void loadRuns();
            }}
          />
        </div>
      ) : null}

      {loading && !summary ? <LoadingState text="正在加载治理工作台…" /> : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="待认领表" formatted={`${summary?.tables_unclaimed ?? 0} 张`} />
        <KpiCard label="待补充字段" formatted={`${summary?.columns_pending ?? 0} 个`} />
        <KpiCard label="失败运行（7 天）" formatted={`${failureCount} 次`} />
        <KpiCard
          label="已同步源"
          formatted={`${summary?.sources_synced ?? 0}/${summary?.sources_total ?? 0}`}
        />
      </div>

      <div role="tablist" aria-label="治理视图" className="flex gap-1 border-b border-slate-200">
        <button
          type="button"
          role="tab"
          id="governance-tab-sync"
          aria-selected={tab === "sync"}
          aria-controls="governance-panel-sync"
          onClick={() => setParams({ tab: "sync" })}
          className={[
            "cursor-pointer border-b-2 px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
            tab === "sync"
              ? "border-slate-900 text-slate-900"
              : "border-transparent text-slate-500 hover:text-slate-800",
          ].join(" ")}
        >
          同步监控
        </button>
        <button
          type="button"
          role="tab"
          id="governance-tab-catalog"
          aria-selected={tab === "catalog"}
          aria-controls="governance-panel-catalog"
          onClick={() => setParams({ tab: "catalog" })}
          className={[
            "cursor-pointer border-b-2 px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
            tab === "catalog"
              ? "border-slate-900 text-slate-900"
              : "border-transparent text-slate-500 hover:text-slate-800",
          ].join(" ")}
        >
          元数据目录
        </button>
        <button
          type="button"
          role="tab"
          id="governance-tab-quality"
          aria-selected={tab === "quality"}
          aria-controls="governance-panel-quality"
          onClick={() => setParams({ tab: "quality" })}
          className={[
            "cursor-pointer border-b-2 px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
            tab === "quality"
              ? "border-slate-900 text-slate-900"
              : "border-transparent text-slate-500 hover:text-slate-800",
          ].join(" ")}
        >
          质量规则
        </button>
      </div>

      {tab === "sync" ? (
        <div role="tabpanel" id="governance-panel-sync" aria-labelledby="governance-tab-sync">
          <SyncMonitorPanel
            role={role}
            items={sourceData?.items ?? []}
            lastBatchAt={sourceData?.last_batch_at ?? null}
            loading={loading}
            error={null}
            onRefresh={() => {
              void loadOverview();
              void loadRuns();
            }}
            selectedSource={selectedSource}
            onSelectSource={(sourceId) => setParams({ tab: "sync", source: sourceId })}
            onSyncSource={(sourceId) => void handleSyncSource(sourceId)}
            syncingId={syncingId}
            actionError={actionError}
            actionNotice={actionNotice}
            runs={runs}
            runsError={runsError}
            runFilter={runFilter}
            onRunFilterChange={setRunFilter}
            expandedRunId={runParam}
            expandedRun={expandedRun}
            expandedLoading={runDetailLoading}
            expandedError={runDetailError}
            onToggleRun={handleToggleRun}
            onGoCatalog={() => setParams({ tab: "catalog" })}
          />
        </div>
      ) : tab === "catalog" ? (
        <div role="tabpanel" id="governance-panel-catalog" aria-labelledby="governance-tab-catalog">
          <CatalogPanel
            role={role}
            selectedId={selectedTable}
            onSelect={(tableId) => setParams({ tab: "catalog", table: tableId === null ? null : String(tableId) })}
            onChanged={() => void loadOverview(true)}
          />
        </div>
      ) : (
        <div role="tabpanel" id="governance-panel-quality" aria-labelledby="governance-tab-quality">
          <QualityPanel role={role} onChanged={() => void loadOverview(true)} />
        </div>
      )}
    </div>
  );
}

export default function GovernancePage() {
  return (
    <Suspense fallback={<LoadingState text="正在加载治理工作台…" />}>
      <GovernanceWorkbench />
    </Suspense>
  );
}
