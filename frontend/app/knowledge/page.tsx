"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import ErrorAlert from "@/components/ErrorAlert";
import KnowledgeDetailPanel from "@/components/knowledge/KnowledgeDetailPanel";
import KnowledgeFilters from "@/components/knowledge/KnowledgeFilters";
import KnowledgeSourcePanel from "@/components/knowledge/KnowledgeSourcePanel";
import KnowledgeSummaryCards from "@/components/knowledge/KnowledgeSummaryCards";
import KnowledgeTable from "@/components/knowledge/KnowledgeTable";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import {
  ApiError,
  errorMessage,
  knowledgeEntries,
  knowledgeEntry,
  knowledgeSources,
  knowledgeSummary,
  reloadKnowledge,
  useDemoRole,
} from "@/lib/api";
import type {
  KnowledgeEntryDetail,
  KnowledgeListResponse,
  KnowledgeSource,
  KnowledgeSummary,
  KnowledgeViolation,
} from "@/lib/types";

const PAGE_SIZE = 20;

export default function KnowledgePage() {
  const [role] = useDemoRole();
  const isAdmin = role === "admin";

  const [summary, setSummary] = useState<KnowledgeSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [list, setList] = useState<KnowledgeListResponse | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [detail, setDetail] = useState<KnowledgeEntryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [qInput, setQInput] = useState("");
  const [domainInput, setDomainInput] = useState("");
  const [kindInput, setKindInput] = useState("");
  const [sourceInput, setSourceInput] = useState("");
  const [applied, setApplied] = useState({ q: "", domain: "", kind: "", source_id: "" });
  const [page, setPage] = useState(1);

  const [reloading, setReloading] = useState(false);
  const [reloadNotice, setReloadNotice] = useState<string | null>(null);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [reloadViolations, setReloadViolations] = useState<KnowledgeViolation[]>([]);

  const loadSummary = useCallback(async (quiet = false) => {
    if (!quiet) setSummaryLoading(true);
    try {
      const result = await knowledgeSummary();
      setSummary(result);
      setSummaryError(null);
    } catch (err) {
      setSummaryError(errorMessage(err));
    } finally {
      if (!quiet) setSummaryLoading(false);
    }
  }, []);

  const loadList = useCallback(
    async (quiet = false) => {
      if (!quiet) setListLoading(true);
      try {
        const result = await knowledgeEntries({
          q: applied.q || undefined,
          domain: applied.domain || undefined,
          kind: applied.kind || undefined,
          source_id: applied.source_id || undefined,
          page,
          page_size: PAGE_SIZE,
        });
        setList(result);
        setListError(null);
      } catch (err) {
        setListError(errorMessage(err));
        if (!quiet) setList(null);
      } finally {
        if (!quiet) setListLoading(false);
      }
    },
    [applied, page],
  );

  const loadSources = useCallback(async () => {
    try {
      const result = await knowledgeSources();
      setSources(result.items ?? []);
      setSourcesError(null);
    } catch (err) {
      setSources([]);
      setSourcesError(errorMessage(err));
    }
  }, []);

  const loadDetail = useCallback(async (entryId: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const result = await knowledgeEntry(entryId);
      setDetail(result);
    } catch (err) {
      setDetail(null);
      setDetailError(errorMessage(err));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
    void loadSources();
  }, [loadSummary, loadSources]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const applyFilters = () => {
    setPage(1);
    setApplied({
      q: qInput.trim(),
      domain: domainInput,
      kind: kindInput,
      source_id: sourceInput,
    });
  };

  const openEntry = (entryId: string) => {
    void loadDetail(entryId);
  };

  const handleReload = async () => {
    setReloading(true);
    setReloadNotice(null);
    setReloadError(null);
    setReloadViolations([]);
    try {
      const result = await reloadKnowledge();
      const removed = result.removed ?? 0;
      setReloadNotice(
        `知识包 ${result.pack_version} 已重载：${result.entries} 条知识、${result.sources} 个来源` +
          (removed > 0 ? `，回收 ${removed} 条已删除条目` : "") +
          `，耗时 ${result.duration_ms} ms。`,
      );
      await Promise.all([loadSummary(true), loadList(true), loadSources()]);
      if (detail) await loadDetail(detail.entry_id);
    } catch (err) {
      setReloadError(errorMessage(err));
      if (err instanceof ApiError) {
        const details = err.details as { violations?: KnowledgeViolation[] } | undefined;
        if (Array.isArray(details?.violations)) {
          setReloadViolations(details.violations.slice(0, 5));
        }
      }
    } finally {
      setReloading(false);
    }
  };

  const unavailable = summary !== null && summary.available === false;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">行业知识库</h1>
          <p className="mt-1 text-sm text-slate-500">
            浏览准则、开源项目与行业实践沉淀的知识条目，追溯每条依据的来源、许可与关联指标。
          </p>
        </div>
        {role === "admin" ? (
          <div className="no-print flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleReload()}
              disabled={reloading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw size={13} className={reloading ? "animate-spin" : ""} aria-hidden />
              {reloading ? "重载中…" : "重载知识包"}
            </button>
          </div>
        ) : null}
      </div>

      {summaryError ? (
        <ErrorAlert message={summaryError} onRetry={() => void loadSummary()} />
      ) : (
        <KnowledgeSummaryCards summary={summary} />
      )}
      {summaryLoading && summary ? (
        <div className="text-[11px] text-slate-400">知识概况刷新中…</div>
      ) : null}

      {unavailable ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          <div className="font-medium">知识库未加载，系统已回退到内置常量。</div>
          <p className="mt-1 leading-relaxed">
            指标依据、勾稽规则与行业术语当前使用内置兜底数据；
            {isAdmin ? "可点击右上角「重载知识包」重新加载。" : "请联系 admin 重载知识包。"}
          </p>
          {summary?.last_error ? (
            <p className="mt-1 break-words text-amber-600">最近错误：{summary.last_error}</p>
          ) : null}
        </div>
      ) : null}

      {reloadNotice ? (
        <div
          aria-live="polite"
          className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
        >
          {reloadNotice}
        </div>
      ) : null}
      {reloadError ? (
        <div className="space-y-2">
          <ErrorAlert message={reloadError} />
          {reloadViolations.length > 0 ? (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700">
              <div className="font-medium">知识包校验未通过（前 {reloadViolations.length} 条）</div>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {reloadViolations.map((violation, index) => (
                  <li key={`${violation.file}-${violation.entry_id}-${violation.field}-${index}`}>
                    <span className="font-mono">{violation.file || "—"}</span>
                    {" · "}
                    <span className="font-mono">{violation.entry_id || "—"}</span>
                    {" · "}
                    {violation.field || "未知字段"}：{violation.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      <KnowledgeFilters
        q={qInput}
        domain={domainInput}
        kind={kindInput}
        sourceId={sourceInput}
        onQChange={setQInput}
        onDomainChange={setDomainInput}
        onKindChange={setKindInput}
        onSourceChange={setSourceInput}
        sources={sources}
        onSearch={applyFilters}
      />

      {listError ? (
        <ErrorAlert message={listError} onRetry={() => void loadList()} />
      ) : null}

      <div
        className={[
          "grid gap-4",
          detail ? "lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]" : "grid-cols-1",
        ].join(" ")}
      >
        <SectionCard title="知识清单" description="点击条目查看陈述、依据、来源与关联对象。">
          {listLoading && !list ? (
            <LoadingState text="正在加载知识条目…" />
          ) : (
            <KnowledgeTable
              items={list?.items ?? []}
              total={list?.total ?? 0}
              page={page}
              pageSize={PAGE_SIZE}
              onSelect={openEntry}
              onPageChange={setPage}
            />
          )}
        </SectionCard>

        {detail ? (
          <div className="min-w-0">
            <KnowledgeDetailPanel detail={detail} loading={detailLoading} error={detailError} />
          </div>
        ) : null}
      </div>

      {sourcesError ? <ErrorAlert message={sourcesError} onRetry={() => void loadSources()} /> : null}
      <KnowledgeSourcePanel sources={sources} />
    </div>
  );
}
