"use client";

import { Database, Play, Plus, RefreshCw, Settings2, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import SourceMetadataPanel from "@/components/SourceMetadataPanel";
import SourceWizard from "@/components/SourceWizard";
import StatusBadge from "@/components/StatusBadge";
import {
  deleteSource,
  errorMessage,
  replaceSourceFile,
  seedSource,
  sources,
  syncSource,
  syncSources,
  testSource,
  testSourceById,
  useDemoRole,
} from "@/lib/api";
import { formatBytes, formatDate, formatRelativeTime } from "@/lib/format";
import type { SeedResult, SourceItem, SourceTestResult, SyncSourceStatus } from "@/lib/types";

const FILE_KINDS = new Set(["csv", "excel", "sqlite"]);

function KindBadge({ kind }: { kind: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-600">
      {kind}
    </span>
  );
}

export default function SourcesPage() {
  const [role] = useDemoRole();
  const [loading, setLoading] = useState(true);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceList, setSourceList] = useState<SourceItem[]>([]);
  const [syncById, setSyncById] = useState<Record<string, SyncSourceStatus>>({});
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [testLoadingId, setTestLoadingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, SourceTestResult>>({});

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [seedLoading, setSeedLoading] = useState(false);
  const [seedResult, setSeedResult] = useState<SeedResult | null>(null);
  const [seedError, setSeedError] = useState<string | null>(null);

  const [wizardOpen, setWizardOpen] = useState(false);
  const [metadataSource, setMetadataSource] = useState<SourceItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SourceItem | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const isAdmin = role === "admin";
  const builtin = sourceList.find((item) => item.id === "sqlite-local");
  const registered = sourceList.filter((item) => item.id !== "sqlite-local");

  const loadSources = useCallback(async () => {
    setLoading(true);
    setSourceError(null);
    const [sourceResult, syncResult] = await Promise.allSettled([sources(), syncSources()]);
    if (sourceResult.status === "fulfilled") {
      setSourceList(sourceResult.value.items);
    } else {
      setSourceError(errorMessage(sourceResult.reason));
      setSourceList([]);
    }
    if (syncResult.status === "fulfilled") {
      setSyncById(
        Object.fromEntries(syncResult.value.items.map((item) => [item.source_id, item])),
      );
      setSyncError(null);
    } else {
      setSyncById({});
      setSyncError(errorMessage(syncResult.reason));
    }
    setLoading(false);
  }, [role]);

  useEffect(() => {
    void loadSources();
  }, [loadSources]);

  const handleSyncSource = async (source: SourceItem) => {
    setSyncingId(source.id);
    setActionError(null);
    try {
      await syncSource(source.id);
      await loadSources();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setSyncingId(null);
    }
  };

  const handleTest = async (source: SourceItem) => {
    setTestLoadingId(source.id);
    setActionError(null);
    try {
      const result =
        source.id === "sqlite-local"
          ? await testSource({})
          : await testSourceById(source.id);
      setTestResults((current) => ({ ...current, [source.id]: result }));
      await loadSources();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setTestLoadingId(null);
    }
  };

  const handleSeed = async () => {
    setSeedLoading(true);
    setSeedError(null);
    try {
      const result = await seedSource({ reset: true });
      setSeedResult(result);
      setConfirmOpen(false);
      await loadSources();
    } catch (err) {
      setSeedError(errorMessage(err));
    } finally {
      setSeedLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      await deleteSource(deleteTarget.id);
      setDeleteTarget(null);
      await loadSources();
    } catch (err) {
      setDeleteError(errorMessage(err));
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleReplace = async (source: SourceItem, file: File | null) => {
    if (!file) return;
    setActionError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      await replaceSourceFile(source.id, form);
      await loadSources();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  };

  const renderTestResult = (source: SourceItem) => {
    const result = testResults[source.id];
    if (!result) return null;
    return (
      <div className="mt-2 text-[11px] text-emerald-700">
        连接测试通过 · {result.table_count} 张表 · {result.latency_ms} ms
        {result.server_version ? ` · ${result.server_version}` : ""}
      </div>
    );
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">数据源</h1>
          <p className="mt-1 text-sm text-slate-500">
            内置 SQLite 业务库 + 外部数据源接入；外部源仅做连接测试与元数据浏览，不做数据物化。
          </p>
        </div>
        <div className="no-print flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void loadSources()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} aria-hidden />
            刷新
          </button>
          {isAdmin ? (
            <button
              type="button"
              onClick={() => setWizardOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
            >
              <Plus size={13} aria-hidden />
              新增数据源
            </button>
          ) : null}
        </div>
      </div>

      {!isAdmin ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          当前角色为 {role}，新增、测试、上传、删除仅 admin 可用；你仍可查看源列表与元数据。
        </div>
      ) : null}

      {loading ? <LoadingState text="正在读取数据源…" /> : null}
      {sourceError ? <ErrorAlert message={sourceError} onRetry={loadSources} /> : null}
      {actionError ? <ErrorAlert message={actionError} /> : null}
      {syncError ? (
        <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          同步状态暂不可用：{syncError}
        </div>
      ) : null}

      {builtin ? (
        <SectionCard
          title={builtin.name}
          description="内置演示库；数据库路径与后端启动目录无关，由后端统一解析"
          actions={
            <>
              <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-500">
                由播种维护
              </span>
              <button
                type="button"
                onClick={() => void handleTest(builtin)}
                disabled={!isAdmin || testLoadingId === builtin.id}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                title={!isAdmin ? "仅 admin 可测试连接" : undefined}
              >
                {testLoadingId === builtin.id ? (
                  <RefreshCw size={13} className="animate-spin" aria-hidden />
                ) : (
                  <Play size={13} aria-hidden />
                )}
                测试连接
              </button>
              <button
                type="button"
                onClick={() => setMetadataSource(builtin)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Settings2 size={13} aria-hidden />
                元数据
              </button>
              <button
                type="button"
                onClick={() => setConfirmOpen(true)}
                disabled={!isAdmin}
                className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                title={!isAdmin ? "仅 admin 可重新播种" : undefined}
              >
                <Database size={13} aria-hidden />
                重新播种
              </button>
            </>
          }
        >
          <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt className="text-xs text-slate-500">类型</dt>
              <dd className="mt-1"><KindBadge kind={builtin.kind} /></dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">状态</dt>
              <dd className="mt-1"><StatusBadge status={builtin.status} /></dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">业务表数量</dt>
              <dd className="mt-1 font-medium text-slate-900">{builtin.table_count}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">文件大小</dt>
              <dd className="mt-1 font-medium text-slate-900">{formatBytes(builtin.size_bytes)}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs text-slate-500">数据库路径</dt>
              <dd className="mt-1 break-all font-mono text-xs text-slate-700">{builtin.path}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">最近播种</dt>
              <dd className="mt-1 font-medium text-slate-900">{formatDate(builtin.last_seeded_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">最近同步</dt>
              <dd className="mt-1 font-medium text-slate-900">由播种维护</dd>
            </div>
          </dl>
          {renderTestResult(builtin)}
        </SectionCard>
      ) : null}

      {registered.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {registered.map((source) => (
            <SectionCard
              key={source.id}
              title={source.name}
              description={source.original_filename ?? undefined}
              actions={
                <>
                  {isAdmin ? (
                    <button
                      type="button"
                      onClick={() => void handleSyncSource(source)}
                      disabled={syncingId === source.id || syncById[source.id]?.sync_status === "running"}
                      title={
                        syncById[source.id]?.sync_status === "running"
                          ? "该源已有运行中的同步任务"
                          : "触发单源同步"
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <RefreshCw
                        size={13}
                        className={
                          syncingId === source.id || syncById[source.id]?.sync_status === "running"
                            ? "animate-spin"
                            : ""
                        }
                        aria-hidden
                      />
                      {syncingId === source.id ? "提交中" : "同步"}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void handleTest(source)}
                    disabled={!isAdmin || testLoadingId === source.id}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                    title={!isAdmin ? "仅 admin 可测试连接" : undefined}
                  >
                    {testLoadingId === source.id ? (
                      <RefreshCw size={13} className="animate-spin" aria-hidden />
                    ) : (
                      <Play size={13} aria-hidden />
                    )}
                    测试
                  </button>
                  <button
                    type="button"
                    onClick={() => setMetadataSource(source)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    <Settings2 size={13} aria-hidden />
                    元数据
                  </button>
                  {isAdmin && FILE_KINDS.has(source.kind) ? (
                    <label
                      className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                      title="替换文件源文件"
                    >
                      <Upload size={13} aria-hidden />
                      替换文件
                      <input
                        type="file"
                        className="hidden"
                        onChange={(event) => {
                          void handleReplace(source, event.target.files?.[0] ?? null);
                          event.target.value = "";
                        }}
                      />
                    </label>
                  ) : null}
                  {isAdmin ? (
                    <button
                      type="button"
                      onClick={() => {
                        setDeleteError(null);
                        setDeleteTarget(source);
                      }}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
                    >
                      <Trash2 size={13} aria-hidden />
                      删除
                    </button>
                  ) : null}
                </>
              }
            >
              <dl className="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-slate-500">类型</dt>
                  <dd className="mt-1"><KindBadge kind={source.kind} /></dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">状态</dt>
                  <dd className="mt-1"><StatusBadge status={source.status} /></dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">表数量</dt>
                  <dd className="mt-1 font-medium text-slate-900">{source.table_count}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">
                    {FILE_KINDS.has(source.kind) ? "文件大小" : "连接主机"}
                  </dt>
                  <dd className="mt-1 break-all font-medium text-slate-900">
                    {FILE_KINDS.has(source.kind)
                      ? formatBytes(source.size_bytes)
                      : `${String(source.config?.host ?? "—")} / ${String(source.config?.database ?? "—")}`}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">最近测试</dt>
                  <dd className="mt-1 font-medium text-slate-900">{formatDate(source.last_tested_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">元数据更新时间</dt>
                  <dd className="mt-1 font-medium text-slate-900">{formatDate(source.metadata_updated_at)}</dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="text-xs text-slate-500">最近同步</dt>
                  <dd className="mt-1 flex flex-wrap items-center gap-2">
                    {syncById[source.id] ? (
                      <>
                        <StatusBadge
                          status={syncById[source.id].sync_status}
                          label={syncById[source.id].sync_status === "never" ? "未同步" : undefined}
                        />
                        <Link
                          href={`/governance?tab=sync&source=${encodeURIComponent(source.id)}`}
                          title={syncById[source.id].last_run_at ?? "查看同步监控"}
                          className="text-[11px] text-slate-600 underline-offset-2 hover:text-slate-900 hover:underline"
                        >
                          {formatRelativeTime(syncById[source.id].last_run_at)}
                        </Link>
                        <span className="text-[11px] text-slate-400">
                          {syncById[source.id].tables_total} 张表 / {syncById[source.id].rows_total} 行
                        </span>
                      </>
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </dd>
                </div>
              </dl>
              {source.last_error ? (
                <p className="mt-3 break-words rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] text-rose-700">
                  {source.last_error}
                </p>
              ) : null}
              {renderTestResult(source)}
            </SectionCard>
          ))}
        </div>
      ) : null}

      {!loading && !sourceError && registered.length === 0 ? (
        <EmptyState
          title="尚未接入外部数据源"
          description={
            isAdmin
              ? "点击右上角“新增数据源”，可连接 PostgreSQL/MySQL 或上传 CSV/Excel/SQLite。"
              : "当前仅有内置 SQLite 业务库；新增数据源仅 admin 可操作。"
          }
        />
      ) : null}

      {seedError ? <ErrorAlert message={seedError} onRetry={() => setConfirmOpen(true)} /> : null}
      {seedResult ? (
        <SectionCard
          title="播种结果"
          description={`耗时 ${seedResult.duration_ms} ms · 共 ${seedResult.tables.length} 张表`}
        >
          <div className="mb-4">
            <StatusBadge status={seedResult.reconciliation?.passed ? "passed" : "failed"} />
            <span className="ml-2 text-xs text-slate-500">
              勾稽校验 {seedResult.reconciliation?.passed ? "通过" : "未通过"}
            </span>
          </div>
          <DataTable
            columns={[
              { key: "table", label: "表名" },
              { key: "rows", label: "行数", align: "right" },
            ]}
            rows={Object.entries(seedResult.rows).map(([table, rows]) => ({ table, rows }))}
            maxHeight={320}
          />
        </SectionCard>
      ) : null}

      <SourceWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={loadSources}
      />

      <SourceMetadataPanel
        open={metadataSource !== null}
        source={metadataSource}
        onClose={() => setMetadataSource(null)}
      />

      <ConfirmDialog
        open={confirmOpen}
        title="重新播种数据库？"
        description="将删除并重建 data/finance.db 中的全部表，生成固定种子的 mock 财务数据。已有本体校准与查询审计会被清空；上传的数据源与文件不受影响。操作不可撤销。"
        confirmText="确认重新播种"
        danger
        loading={seedLoading}
        onConfirm={handleSeed}
        onCancel={() => setConfirmOpen(false)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`删除数据源「${deleteTarget?.name ?? ""}」？`}
        description={
          deleteError
            ? `删除失败：${deleteError}`
            : "将删除注册信息与已上传文件，操作不可撤销。内置 SQLite 源不受影响。"
        }
        confirmText="确认删除"
        danger
        loading={deleteLoading}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
