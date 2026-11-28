"use client";

import { BadgeCheck, Pencil, RefreshCw, Search, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import AutoOntologyCard from "@/components/AutoOntologyCard";
import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import SqlBlock from "@/components/SqlBlock";
import StatusBadge from "@/components/StatusBadge";
import {
  certifyTerm,
  errorMessage,
  generateOntology,
  ontologyTerm,
  ontologyTerms,
  relations,
  updateTerm,
  useDemoRole,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Relation, SqlAttribute, Term, TermDetail } from "@/lib/types";

const CATEGORY_LABELS: Record<string, string> = {
  table: "表",
  metric: "指标",
  sensitive: "敏感",
};

const CERTIFIED_OPTIONS = [
  { value: "all", label: "全部认证状态" },
  { value: "true", label: "已认证" },
  { value: "false", label: "未认证" },
];

const CATEGORY_OPTIONS = [
  { value: "all", label: "全部类别" },
  { value: "table", label: "表" },
  { value: "metric", label: "指标" },
  { value: "sensitive", label: "敏感" },
];

function splitList(text: string): string[] {
  return text
    .split(/[,，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function OntologyPage() {
  const [role] = useDemoRole();
  const isAdmin = role === "admin";

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [certifiedFilter, setCertifiedFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");

  const [terms, setTerms] = useState<Term[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [relationList, setRelationList] = useState<Relation[]>([]);
  const [relationError, setRelationError] = useState<string | null>(null);

  const [generateLoading, setGenerateLoading] = useState(false);
  const [generateMessage, setGenerateMessage] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<TermDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    display_name: "",
    description: "",
    synonyms: "",
    mapped_table: "",
    mapped_columns: "",
  });
  const [saveLoading, setSaveLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [formOriginal, setFormOriginal] = useState<Term | null>(null);

  const [fallbackSqlAttributes, setFallbackSqlAttributes] = useState<SqlAttribute[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(qInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [qInput]);

  const loadTerms = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await ontologyTerms({
        q: q || undefined,
        certified: certifiedFilter === "all" ? undefined : certifiedFilter === "true",
        category: categoryFilter === "all" ? undefined : categoryFilter,
        limit: 200,
      });
      setTerms(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(errorMessage(err));
      setTerms([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [role, q, certifiedFilter, categoryFilter]);

  const loadRelations = useCallback(async () => {
    setRelationError(null);
    try {
      const data = await relations();
      setRelationList(data.items);
    } catch (err) {
      setRelationError(errorMessage(err));
      setRelationList([]);
    }
  }, [role]);

  useEffect(() => {
    void loadTerms();
  }, [loadTerms]);

  useEffect(() => {
    void loadRelations();
  }, [loadRelations]);

  const fetchDetail = useCallback(async (id: number) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const data = await ontologyTerm(id, role);
      setDetail(data);
      return data;
    } catch (err) {
      setDetailError(errorMessage(err));
      setDetail(null);
      return null;
    } finally {
      setDetailLoading(false);
    }
  }, [role]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    void fetchDetail(selectedId);
  }, [selectedId, fetchDetail, role]);

  const inlineSqlAttributes = useMemo(() => {
    const map = new Map<string, SqlAttribute>();
    terms.forEach((term) => {
      (term.sql_attributes ?? []).forEach((attribute) => {
        if (!map.has(attribute.code)) map.set(attribute.code, attribute);
      });
    });
    return Array.from(map.values());
  }, [terms]);

  useEffect(() => {
    if (terms.length === 0) {
      setFallbackSqlAttributes([]);
      return;
    }
    if (inlineSqlAttributes.length > 0) {
      setFallbackSqlAttributes([]);
      return;
    }
    let cancelled = false;
    const ids = terms.slice(0, 40).map((term) => term.term_id);
    Promise.all(ids.map((id) => ontologyTerm(id).catch(() => null))).then((details) => {
      if (cancelled) return;
      const map = new Map<string, SqlAttribute>();
      details.forEach((item) => {
        (item?.sql_attributes ?? []).forEach((attribute) => {
          if (!map.has(attribute.code)) map.set(attribute.code, attribute);
        });
      });
      setFallbackSqlAttributes(Array.from(map.values()));
    });
    return () => {
      cancelled = true;
    };
  }, [terms, inlineSqlAttributes.length]);

  const sqlAttributes = inlineSqlAttributes.length > 0 ? inlineSqlAttributes : fallbackSqlAttributes;
  const certifiedCount = terms.filter((term) => term.certified).length;

  const handleGenerate = async () => {
    setGenerateLoading(true);
    setGenerateError(null);
    setGenerateMessage(null);
    try {
      const result = await generateOntology({ force: terms.length > 0 });
      const skipped = result.status === "skipped" || result.skipped === true || result.generated === false;
      const counts = result.counts ?? { terms: 0, relations: 0, sql_attributes: 0, columns: 0 };
      setGenerateMessage(
        `${skipped ? "已跳过" : "已生成"}：术语 ${counts.terms}、关系 ${counts.relations}、SQL 属性 ${counts.sql_attributes}、列映射 ${counts.columns}（版本 ${result.version ?? "—"}，${result.duration_ms ?? 0} ms）`,
      );
      await Promise.all([loadTerms(), loadRelations()]);
    } catch (err) {
      setGenerateError(errorMessage(err));
    } finally {
      setGenerateLoading(false);
    }
  };

  const startEdit = (term: Term) => {
    setSelectedId(term.term_id);
    setEditing(true);
    setActionError(null);
    setFormOriginal(term);
    setForm({
      display_name: term.display_name ?? "",
      description: term.description ?? "",
      synonyms: (term.synonyms ?? []).join("，"),
      mapped_table: term.mapped_table ?? "",
      mapped_columns: (term.mapped_columns ?? []).join("，"),
    });
  };

  const saveEdit = async () => {
    if (selectedId === null) return;
    const original = formOriginal;
    const payload: {
      display_name?: string;
      description?: string;
      synonyms?: string[];
      mapped_table?: string | null;
      mapped_columns?: string[];
    } = {};
    const displayName = form.display_name.trim();
    const description = form.description.trim();
    const synonyms = splitList(form.synonyms);
    const mappedTable = form.mapped_table.trim() || null;
    const mappedColumns = splitList(form.mapped_columns);
    if (displayName !== (original?.display_name ?? "")) payload.display_name = displayName;
    if (description !== (original?.description ?? "")) payload.description = description;
    if (JSON.stringify(synonyms) !== JSON.stringify(original?.synonyms ?? [])) payload.synonyms = synonyms;
    const tableChanged = mappedTable !== (original?.mapped_table ?? null);
    const columnsChanged =
      JSON.stringify(mappedColumns) !== JSON.stringify(original?.mapped_columns ?? []);
    // 指标/敏感术语通常没有表映射；只提交实际变更字段，避免空映射触发列校验
    if (tableChanged) {
      payload.mapped_table = mappedTable;
      payload.mapped_columns = mappedColumns;
    } else if (columnsChanged) {
      payload.mapped_columns = mappedColumns;
    }
    if (Object.keys(payload).length === 0) {
      setEditing(false);
      return;
    }
    setSaveLoading(true);
    setActionError(null);
    try {
      await updateTerm(selectedId, payload);
      setEditing(false);
      await loadTerms();
      await fetchDetail(selectedId);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setSaveLoading(false);
    }
  };

  const handleCertify = async (term: Term) => {
    setActionError(null);
    try {
      await certifyTerm(term.term_id, { certified: true });
      await loadTerms();
      if (selectedId === term.term_id) await fetchDetail(term.term_id);
    } catch (err) {
      setActionError(errorMessage(err));
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">本体</h1>
          <p className="mt-1 text-sm text-slate-500">
            规则驱动生成术语、列映射、关系与 SQL 属性；支持人工编辑与认证，所有变更写入变更日志。
          </p>
        </div>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={!isAdmin || generateLoading}
          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
          title={!isAdmin ? "仅 admin 可生成本体" : undefined}
        >
          {generateLoading ? (
            <RefreshCw size={14} className="animate-spin" aria-hidden />
          ) : (
            <Sparkles size={14} aria-hidden />
          )}
          {terms.length > 0 ? "重新生成本体" : "生成本体"}
        </button>
      </div>

      <AutoOntologyCard role={role} />

      {!isAdmin ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          当前角色为 {role}，本体生成、编辑与认证仅 admin 可用；你可以查看术语、关系与 SQL 属性。
        </div>
      ) : null}

      {generateError ? <ErrorAlert message={generateError} onRetry={handleGenerate} /> : null}
      {generateMessage ? (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-700">
          {generateMessage}
        </div>
      ) : null}
      {actionError ? <ErrorAlert message={actionError} /> : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["术语", total],
          ["当前筛选已认证", certifiedCount],
          ["关系", relationList.length],
          ["SQL 属性", sqlAttributes.length],
        ].map(([label, value]) => (
          <div key={String(label)} className="card p-4">
            <div className="text-xs text-slate-500">{label}</div>
            <div className="mt-1 text-xl font-semibold text-slate-900">{String(value)}</div>
          </div>
        ))}
      </div>

      <SectionCard
        title="术语"
        description="按 term_code 幂等生成，重复生成保留已认证状态"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative">
              <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden />
              <input
                value={qInput}
                onChange={(event) => setQInput(event.target.value)}
                placeholder="搜索名称/描述/同义词"
                className="w-48 rounded-lg border border-slate-300 bg-white py-1.5 pl-8 pr-3 text-xs text-slate-700 outline-none focus:border-slate-500"
              />
            </label>
            <select
              value={certifiedFilter}
              onChange={(event) => setCertifiedFilter(event.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-slate-500"
            >
              {CERTIFIED_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={categoryFilter}
              onChange={(event) => setCategoryFilter(event.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-slate-500"
            >
              {CATEGORY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {loading ? <LoadingState text="正在读取术语…" /> : null}
        {error ? <ErrorAlert message={error} onRetry={loadTerms} /> : null}
        {!loading && !error && terms.length === 0 ? (
          <EmptyState
            title="尚未生成本体"
            description="点击右上角“生成本体”，根据业务表与财务词典生成 27 个术语、16 条关系与 20 个 SQL 属性。"
          />
        ) : null}
        {terms.length > 0 ? (
          <DataTable
            columns={[
              { key: "display_name", label: "显示名" },
              { key: "term_code", label: "术语编码" },
              {
                key: "category",
                label: "类别",
                render: (_row, value) => (
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                    {CATEGORY_LABELS[String(value)] ?? String(value)}
                  </span>
                ),
              },
              {
                key: "certified",
                label: "认证",
                align: "center",
                render: (_row, value) => <StatusBadge status={value ? "certified" : "uncertified"} />,
              },
              { key: "mapped_table", label: "映射表" },
              {
                key: "synonyms",
                label: "同义词",
                render: (_row, value) => (Array.isArray(value) ? value.join("、") : "—"),
              },
              {
                key: "actions",
                label: "操作",
                render: (_row) => {
                  const term = _row as unknown as Term;
                  return (
                    <div className="flex flex-wrap gap-1">
                      <button
                        type="button"
                        onClick={() => setSelectedId(term.term_id)}
                        className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
                      >
                        查看
                      </button>
                      <button
                        type="button"
                        onClick={() => startEdit(term)}
                        disabled={!isAdmin}
                        className="inline-flex items-center gap-0.5 rounded border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                      >
                        <Pencil size={10} aria-hidden />
                        编辑
                      </button>
                      {!term.certified ? (
                        <button
                          type="button"
                          onClick={() => handleCertify(term)}
                          disabled={!isAdmin}
                          className="inline-flex items-center gap-0.5 rounded border border-emerald-300 px-1.5 py-0.5 text-[11px] text-emerald-700 hover:bg-emerald-50 disabled:opacity-40"
                        >
                          <BadgeCheck size={10} aria-hidden />
                          认证
                        </button>
                      ) : null}
                    </div>
                  );
                },
              },
            ]}
            rows={terms.map((term) => ({ ...term }))}
            maxHeight={440}
          />
        ) : null}
      </SectionCard>

      {selectedId !== null ? (
        <SectionCard
          title={detail ? `${detail.display_name} · ${detail.term_code}` : "术语详情"}
          description="术语定义、列映射、SQL 属性与最近 20 条变更日志"
          actions={
            <button
              type="button"
              onClick={() => {
                setSelectedId(null);
                setEditing(false);
              }}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              <X size={12} aria-hidden />
              关闭
            </button>
          }
        >
          {detailLoading ? <LoadingState text="正在读取术语详情…" /> : null}
          {detailError ? <ErrorAlert message={detailError} onRetry={() => fetchDetail(selectedId)} /> : null}

          {detail && !editing ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <StatusBadge status={detail.certified ? "certified" : "uncertified"} />
                <span>类别：{CATEGORY_LABELS[detail.category] ?? detail.category}</span>
                {detail.certified_by ? <span>认证人：{detail.certified_by}</span> : null}
                <span>更新：{formatDate(detail.updated_at)}</span>
                {isAdmin ? (
                  <button
                    type="button"
                    onClick={() => startEdit(detail)}
                    className="ml-auto inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
                  >
                    <Pencil size={12} aria-hidden />
                    编辑
                  </button>
                ) : null}
              </div>
              <p className="text-sm leading-relaxed text-slate-700">{detail.description}</p>
              <div className="grid gap-3 text-xs sm:grid-cols-2">
                <div>
                  <div className="text-slate-500">同义词</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(detail.synonyms ?? []).length
                      ? detail.synonyms.map((item) => (
                          <span key={item} className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
                            {item}
                          </span>
                        ))
                      : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">映射表 / 列</div>
                  <div className="mt-1 font-mono text-slate-700">
                    {detail.mapped_table ?? "—"}
                    {detail.mapped_columns?.length ? ` · ${detail.mapped_columns.join(", ")}` : ""}
                  </div>
                </div>
              </div>

              {detail.columns?.length ? (
                <div>
                  <div className="mb-2 text-xs font-medium text-slate-600">列映射</div>
                  <DataTable
                    columns={[
                      { key: "table_name", label: "表" },
                      { key: "column_name", label: "列" },
                      { key: "display_name", label: "显示名" },
                      {
                        key: "is_sensitive",
                        label: "敏感",
                        align: "center",
                        render: (_row, value) => (value ? "是" : "—"),
                      },
                      { key: "classification", label: "分类" },
                      { key: "sample_masked", label: "掩码样本" },
                    ]}
                    rows={detail.columns.map((column) => ({ ...column }))}
                  />
                </div>
              ) : null}

              {detail.sql_attributes?.length ? (
                <div>
                  <div className="mb-2 text-xs font-medium text-slate-600">SQL 属性</div>
                  <div className="space-y-2">
                    {detail.sql_attributes.map((attribute) => (
                      <div key={attribute.code} className="rounded-lg border border-slate-200 p-3">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="font-medium text-slate-800">{attribute.name}</span>
                          <span className="font-mono text-slate-500">{attribute.code}</span>
                          <span className="text-slate-400">{attribute.unit}</span>
                          <StatusBadge status={attribute.certified ? "certified" : "uncertified"} />
                        </div>
                        <p className="mt-1 text-xs text-slate-500">{attribute.description}</p>
                        <div className="mt-2">
                          <SqlBlock sql={attribute.formula_sql} title="formula_sql" />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              <div>
                <div className="mb-2 text-xs font-medium text-slate-600">变更日志（最近 20 条）</div>
                <DataTable
                  columns={[
                    { key: "field_name", label: "字段" },
                    { key: "old_value", label: "旧值" },
                    { key: "new_value", label: "新值" },
                    { key: "changed_by_role", label: "角色" },
                    { key: "changed_at", label: "时间", render: (_row, value) => formatDate(value) },
                  ]}
                  rows={(detail.change_log ?? []).map((item) => ({ ...item }))}
                  emptyText="暂无变更记录；编辑或认证术语后会在此展示"
                />
              </div>
            </div>
          ) : null}

          {detail && editing ? (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs text-slate-600">
                  显示名
                  <input
                    value={form.display_name}
                    onChange={(event) => setForm((prev) => ({ ...prev, display_name: event.target.value }))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  映射表
                  <input
                    value={form.mapped_table}
                    onChange={(event) => setForm((prev) => ({ ...prev, mapped_table: event.target.value }))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 font-mono text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
                <label className="text-xs text-slate-600 sm:col-span-2">
                  描述
                  <textarea
                    value={form.description}
                    onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                    rows={3}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  同义词（逗号分隔）
                  <input
                    value={form.synonyms}
                    onChange={(event) => setForm((prev) => ({ ...prev, synonyms: event.target.value }))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  映射列（逗号分隔）
                  <input
                    value={form.mapped_columns}
                    onChange={(event) => setForm((prev) => ({ ...prev, mapped_columns: event.target.value }))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 font-mono text-sm text-slate-800 outline-none focus:border-slate-500"
                  />
                </label>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setEditing(false)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={saveEdit}
                  disabled={saveLoading || !isAdmin}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  {saveLoading ? <RefreshCw size={12} className="animate-spin" aria-hidden /> : null}
                  保存修改
                </button>
              </div>
            </div>
          ) : null}
        </SectionCard>
      ) : null}

      <SectionCard title="关系" description="外键 + 财务语义关系">
        {relationError ? <ErrorAlert message={relationError} onRetry={loadRelations} /> : null}
        <DataTable
          columns={[
            {
              key: "from_term",
              label: "From",
              render: (_row) => {
                const name = String(_row.from_term ?? "");
                const code = String(_row.from_term_code ?? "");
                return code ? `${name} (${code})` : name;
              },
            },
            {
              key: "to_term",
              label: "To",
              render: (_row) => {
                const name = String(_row.to_term ?? "");
                const code = String(_row.to_term_code ?? "");
                return code ? `${name} (${code})` : name;
              },
            },
            { key: "relation_type", label: "类型" },
            { key: "cardinality", label: "基数" },
            { key: "join_hint", label: "Join Hint" },
            { key: "description", label: "说明" },
          ]}
          rows={relationList.map((relation) => ({ ...relation }))}
          emptyText="尚未生成关系"
          maxHeight={360}
        />
      </SectionCard>

      <SectionCard title="SQL 属性" description="所有 SQL 属性均通过只读守卫，使用命名参数">
        <DataTable
          columns={[
            { key: "code", label: "编码" },
            { key: "name", label: "名称" },
            { key: "unit", label: "单位" },
            { key: "description", label: "口径" },
            {
              key: "formula_sql",
              label: "SQL",
              render: (_row, value) => (
                <code className="block max-w-xl truncate font-mono text-[10px] text-slate-500" title={String(value ?? "")}>
                  {String(value ?? "—")}
                </code>
              ),
            },
          ]}
          rows={sqlAttributes.map((attribute) => ({ ...attribute }))}
          emptyText="尚未生成 SQL 属性"
          maxHeight={420}
        />
      </SectionCard>
    </div>
  );
}
