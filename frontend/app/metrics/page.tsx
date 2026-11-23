"use client";

import { Plus, RefreshCw, Search } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import Tabs, { tabButtonId, tabPanelId, type TabItem } from "@/components/Tabs";
import MetricDetailPanel, { type MetricDetailTab } from "@/components/metrics/MetricDetailPanel";
import MetricSummaryCards from "@/components/metrics/MetricSummaryCards";
import MetricTable from "@/components/metrics/MetricTable";
import SectionCard from "@/components/SectionCard";
import WorkbenchPanel from "@/components/metrics/WorkbenchPanel";
import {
  ApiError,
  createMetric,
  errorMessage,
  metricDetail,
  metricSummary,
  metricsList,
  useDemoRole,
} from "@/lib/api";
import type {
  MetricCreateBody,
  MetricDetail,
  MetricListResponse,
  MetricSummary,
} from "@/lib/types";

const PAGE_SIZE = 20;

type PageTab = "library" | "detail" | "workbench";
type DetailTab = MetricDetailTab;

const PAGE_TABS: PageTab[] = ["library", "detail", "workbench"];
const DETAIL_TABS: DetailTab[] = ["definition", "versions", "tests", "aliases"];

const PAGE_TAB_ITEMS: TabItem[] = [
  { value: "library", label: "指标库" },
  { value: "detail", label: "指标详情" },
  { value: "workbench", label: "认证工作台" },
];

function parsePageTab(raw: string | null, metric: string | null): PageTab {
  if (raw && (PAGE_TABS as string[]).includes(raw)) return raw as PageTab;
  if (!raw && metric) return "detail";
  return "library";
}

function parseDetailTab(raw: string | null): DetailTab {
  if (raw && (DETAIL_TABS as string[]).includes(raw)) return raw as DetailTab;
  return "definition";
}

interface CreateForm {
  code: string;
  name: string;
  definition: string;
  formula_text: string;
  formula_sql: string;
  owner: string;
  business_domain: string;
  unit: string;
  sensitivity_level: "public" | "internal" | "confidential" | "restricted";
}

const EMPTY_FORM: CreateForm = {
  code: "",
  name: "",
  definition: "",
  formula_text: "",
  formula_sql: "",
  owner: "",
  business_domain: "",
  unit: "",
  sensitivity_level: "internal",
};

function scopedError(error: unknown): { message: string; details?: unknown } {
  if (error instanceof ApiError) return { message: error.message, details: error.details };
  return { message: errorMessage(error) };
}

function MetricsWorkbench() {
  const router = useRouter();
  const pathname = usePathname() ?? "/metrics";
  const searchParams = useSearchParams();
  const [role] = useDemoRole();

  const rawTab = searchParams.get("tab");
  const rawDetail = searchParams.get("detail");
  const metricParam = searchParams.get("metric");
  const activeTab = parsePageTab(rawTab, metricParam);
  const detailTab = parseDetailTab(rawDetail);

  const [summary, setSummary] = useState<MetricSummary | null>(null);
  const [list, setList] = useState<MetricListResponse | null>(null);
  const [detail, setDetail] = useState<MetricDetail | null>(null);

  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [qInput, setQInput] = useState("");
  const [ownerInput, setOwnerInput] = useState("");
  const [status, setStatus] = useState("");
  const [applied, setApplied] = useState({ q: "", owner: "", status: "" });
  const [page, setPage] = useState(1);

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<{ message: string; details?: unknown } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [definitionDirty, setDefinitionDirty] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<Record<string, string | null> | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const canWrite = role === "admin" || role === "analyst";

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

  const navigate = useCallback(
    (updates: Record<string, string | null>) => {
      if (definitionDirty) {
        setPendingNavigation(updates);
        return;
      }
      setParams(updates);
    },
    [definitionDirty, setParams],
  );

  const confirmPendingNavigation = () => {
    const updates = pendingNavigation;
    setPendingNavigation(null);
    setDefinitionDirty(false);
    if (updates) setParams(updates);
  };

  const loadSummary = useCallback(async (quiet = false) => {
    if (!quiet) setSummaryLoading(true);
    try {
      const result = await metricSummary();
      setSummary(result);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      if (!quiet) setSummaryLoading(false);
    }
  }, []);

  const loadList = useCallback(
    async (quiet = false) => {
      if (!quiet) setListLoading(true);
      try {
        const result = await metricsList({
          q: applied.q || undefined,
          owner: applied.owner || undefined,
          status: applied.status || undefined,
          page,
          page_size: PAGE_SIZE,
        });
        setList(result);
        setError(null);
      } catch (err) {
        setError(errorMessage(err));
        if (!quiet) setList(null);
      } finally {
        if (!quiet) setListLoading(false);
      }
    },
    [applied, page],
  );

  const loadDetail = useCallback(async (code: string, quiet = false) => {
    if (!quiet) setDetailLoading(true);
    try {
      const result = await metricDetail(code);
      setDetail(result);
      setDetailError(null);
    } catch (err) {
      setDetail(null);
      setDetailError(errorMessage(err));
    } finally {
      if (!quiet) setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (metricParam) {
      void loadDetail(metricParam);
    } else {
      setDetail(null);
      setDetailError(null);
    }
  }, [metricParam, loadDetail]);

  useEffect(() => {
    const desiredTab = parsePageTab(rawTab, metricParam);
    const desiredDetail = desiredTab === "detail" ? parseDetailTab(rawDetail) : null;
    const tabNeedsFix = rawTab !== desiredTab;
    const detailNeedsFix = desiredTab === "detail" ? rawDetail !== desiredDetail : rawDetail !== null;
    if (!tabNeedsFix && !detailNeedsFix) return;
    setParams({ tab: desiredTab, detail: desiredDetail });
  }, [rawTab, rawDetail, metricParam, setParams]);

  useEffect(() => {
    if (!definitionDirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [definitionDirty]);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      loadSummary(true),
      loadList(true),
      metricParam ? loadDetail(metricParam, true) : Promise.resolve(),
    ]);
    setRefreshToken((value) => value + 1);
  }, [loadSummary, loadList, loadDetail, metricParam]);

  const openMetric = useCallback(
    (code: string, target: DetailTab = "definition") => {
      navigate({ tab: "detail", metric: code, detail: target });
    },
    [navigate],
  );

  const handlePageTabChange = (next: PageTab) => {
    navigate({ tab: next });
  };

  const handleDetailTabChange = (next: DetailTab) => {
    navigate({ tab: "detail", detail: next });
  };

  const applyFilters = () => {
    setPage(1);
    setApplied({ q: qInput.trim(), owner: ownerInput.trim(), status });
  };

  const resetFilters = () => {
    setQInput("");
    setOwnerInput("");
    setStatus("");
    setPage(1);
    setApplied({ q: "", owner: "", status: "" });
  };

  const handleCreate = async () => {
    if (!form.code.trim() || !form.name.trim()) {
      setCreateError({ message: "指标编码与名称不能为空" });
      return;
    }
    setCreating(true);
    setCreateError(null);
    setNotice(null);
    try {
      const body: MetricCreateBody = {
        code: form.code.trim(),
        name: form.name.trim(),
        definition: form.definition,
        formula_text: form.formula_text,
        formula_sql: form.formula_sql,
        owner: form.owner,
        business_domain: form.business_domain,
        unit: form.unit,
        sensitivity_level: form.sensitivity_level,
        boundary: {},
      };
      const created = await createMetric(body);
      setForm(EMPTY_FORM);
      setCreateOpen(false);
      setNotice(`指标 ${created.name}（${created.code}）已创建为草稿。`);
      setParams({ tab: "detail", metric: created.code, detail: "definition" });
      await Promise.all([loadSummary(true), loadList(true), loadDetail(created.code, true)]);
      setRefreshToken((value) => value + 1);
    } catch (err) {
      setCreateError(scopedError(err));
    } finally {
      setCreating(false);
    }
  };

  const roleHint =
    role === "viewer"
      ? "当前角色 viewer：只读浏览指标定义、版本、用例与别名。"
      : role === "analyst"
        ? "当前角色 analyst：可新增/编辑指标、维护用例与别名、提交认证；认证仅 admin 可执行。"
        : "当前角色 admin：可编辑指标、维护用例与别名、提交并认证版本。";

  const activeDetail = detail && metricParam && detail.code === metricParam ? detail : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">指标注册表</h1>
          <p className="mt-1 text-sm text-slate-500">
            统一维护指标定义、口径边界、版本、测试用例与业务别名，认证后供问数引用。
          </p>
        </div>
        <div className="no-print flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void refreshAll()}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw size={13} className={listLoading ? "animate-spin" : ""} aria-hidden />
            刷新
          </button>
          {canWrite ? (
            <button
              type="button"
              onClick={() => {
                setCreateOpen((value) => !value);
                setCreateError(null);
                if (activeTab !== "library") navigate({ tab: "library" });
              }}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
            >
              <Plus size={13} aria-hidden />
              {createOpen ? "取消新增" : "新增指标"}
            </button>
          ) : null}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-600">
        {roleHint}
      </div>

      {error ? <ErrorAlert message={error} onRetry={() => void loadList()} /> : null}

      <Tabs
        items={PAGE_TAB_ITEMS}
        value={activeTab}
        onChange={(next) => handlePageTabChange(next as PageTab)}
        ariaLabel="指标页面视图"
        idPrefix="metrics"
      />

      <div
        role="tabpanel"
        id={tabPanelId("metrics", activeTab)}
        aria-labelledby={tabButtonId("metrics", activeTab)}
        className="mt-4"
      >
        {activeTab === "library" ? (
          <div className="space-y-5">
            <MetricSummaryCards summary={summary} loading={summaryLoading} />

            <SectionCard title="筛选指标" description="关键词匹配指标名称与编码；负责人为精确匹配。">
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
                <label className="text-xs text-slate-600">
                  关键词
                  <input
                    value={qInput}
                    onChange={(event) => setQInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") applyFilters();
                    }}
                    placeholder="指标名称或编码"
                    className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  状态
                  <select
                    value={status}
                    onChange={(event) => setStatus(event.target.value)}
                    className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                  >
                    <option value="">全部</option>
                    <option value="draft">草稿</option>
                    <option value="pending">待认证</option>
                    <option value="certified">已认证</option>
                    <option value="deprecated">已废弃</option>
                  </select>
                </label>
                <label className="text-xs text-slate-600">
                  负责人
                  <input
                    value={ownerInput}
                    onChange={(event) => setOwnerInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") applyFilters();
                    }}
                    className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
                <div className="flex items-end gap-2">
                  <button
                    type="button"
                    onClick={applyFilters}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
                  >
                    <Search size={13} aria-hidden />
                    查询
                  </button>
                  <button
                    type="button"
                    onClick={resetFilters}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    重置
                  </button>
                </div>
              </div>
            </SectionCard>

            {notice ? (
              <div
                aria-live="polite"
                className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
              >
                {notice}
              </div>
            ) : null}

            {createOpen && canWrite ? (
              <SectionCard
                title="新增指标"
                description="创建后自动生成 v0.1 草稿；补齐公式与用例后提交认证。"
                actions={
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setCreateOpen(false)}
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleCreate()}
                      disabled={creating}
                      className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                    >
                      {creating ? "创建中…" : "创建草稿"}
                    </button>
                  </div>
                }
              >
                {createError ? (
                  <ErrorAlert message={createError.message} details={createError.details ?? undefined} />
                ) : null}
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <label className="text-xs text-slate-600">
                    指标编码 *
                    <input
                      value={form.code}
                      onChange={(event) => setForm({ ...form, code: event.target.value })}
                      placeholder="小写字母开头，如 custom_metric"
                      className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-sm text-slate-800 outline-none focus:border-slate-500"
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    指标名称 *
                    <input
                      value={form.name}
                      onChange={(event) => setForm({ ...form, name: event.target.value })}
                      className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                    />
                  </label>
                  <label className="text-xs text-slate-600 md:col-span-2">
                    定义
                    <textarea
                      value={form.definition}
                      onChange={(event) => setForm({ ...form, definition: event.target.value })}
                      rows={2}
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
                      onChange={(event) =>
                        setForm({
                          ...form,
                          sensitivity_level: event.target.value as CreateForm["sensitivity_level"],
                        })
                      }
                      className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                    >
                      <option value="public">public</option>
                      <option value="internal">internal</option>
                      <option value="confidential">confidential</option>
                      <option value="restricted">restricted</option>
                    </select>
                  </label>
                  <label className="text-xs text-slate-600 md:col-span-2">
                    公式 SQL（可稍后补）
                    <textarea
                      value={form.formula_sql}
                      onChange={(event) => setForm({ ...form, formula_sql: event.target.value })}
                      rows={3}
                      className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 font-mono text-[11px] text-slate-800 outline-none focus:border-slate-500"
                    />
                  </label>
                </div>
              </SectionCard>
            ) : null}

            <SectionCard
              title="指标清单"
              description="点击指标行或指标名称查看详情；从详情返回时保留选中。"
            >
              <MetricTable
                items={list?.items ?? []}
                selectedCode={metricParam}
                total={list?.total ?? 0}
                page={page}
                pageSize={PAGE_SIZE}
                onSelect={(code) => openMetric(code)}
                onPageChange={setPage}
                loading={listLoading}
              />
            </SectionCard>
          </div>
        ) : null}

        {activeTab === "detail" ? (
          <div className="space-y-4">
            {detailError ? (
              <div className="space-y-2">
                <ErrorAlert
                  message={detailError}
                  onRetry={() => (metricParam ? void loadDetail(metricParam) : undefined)}
                />
                <button
                  type="button"
                  onClick={() => navigate({ tab: "library" })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  返回指标库
                </button>
              </div>
            ) : null}
            <MetricDetailPanel
              metric={activeDetail}
              role={role}
              loading={detailLoading}
              detailTab={detailTab}
              onDetailTabChange={handleDetailTabChange}
              onChanged={refreshAll}
              onDirtyChange={setDefinitionDirty}
              onGoLibrary={() => navigate({ tab: "library" })}
            />
          </div>
        ) : null}

        {activeTab === "workbench" ? (
          <WorkbenchPanel
            role={role}
            active={activeTab === "workbench"}
            refreshToken={refreshToken}
            onOpenMetric={(code, target) => openMetric(code, target)}
            onGoLibrary={() => navigate({ tab: "library" })}
          />
        ) : null}
      </div>

      <ConfirmDialog
        open={pendingNavigation !== null}
        title="放弃未保存的修改？"
        description="当前口径编辑尚未保存，离开后将丢失。"
        confirmText="放弃并离开"
        danger
        onConfirm={confirmPendingNavigation}
        onCancel={() => setPendingNavigation(null)}
      />
    </div>
  );
}

export default function MetricsPage() {
  return (
    <Suspense fallback={<LoadingState text="正在加载指标注册表…" />}>
      <MetricsWorkbench />
    </Suspense>
  );
}
