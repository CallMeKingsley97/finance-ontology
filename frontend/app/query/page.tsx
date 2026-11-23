"use client";

import { ArrowUpRight, MessageSquare, RefreshCw, Sparkles } from "lucide-react";
import type { FormEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import ChartPanel from "@/components/ChartPanel";
import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import SqlBlock from "@/components/SqlBlock";
import StatusBadge from "@/components/StatusBadge";
import { ask, errorMessage, useDemoRole } from "@/lib/api";
import type { QueryAnswer } from "@/lib/types";

const EXAMPLES = [
  "2026年10月营业收入",
  "2026年10月营业收入同比",
  "2026Q3 毛利率",
  "2026年10月期间费用构成",
  "2026年10月营业利润",
  "2026年10月净利润",
  "2026年10月资产总额和资产负债率",
  "2026年10月 Top 5 客户",
  "2026年10月收入预算达成率",
  "2026年10月应收账款余额",
  "客户明细",
  "近 6 个月收入趋势",
];

export default function QueryPage() {
  const [role] = useDemoRole();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QueryAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastQuestionRef = useRef<string | null>(null);
  const mountedRef = useRef(false);

  const runAsk = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    lastQuestionRef.current = trimmed;
    setLoading(true);
    setError(null);
    try {
      setAnswer(await ask(trimmed, 20));
    } catch (err) {
      setError(errorMessage(err));
      setAnswer(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    const last = lastQuestionRef.current;
    if (last) void runAsk(last);
  }, [role, runAsk]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void runAsk(question);
  };

  const resultColumns = answer
    ? answer.columns.map((column) => ({
        key: column.name,
        label: column.name,
        align: column.type === "number" ? ("right" as const) : ("left" as const),
      }))
    : [];

  const resultRows = answer
    ? answer.rows.map((row) => {
        if (Array.isArray(row)) {
          const record: Record<string, unknown> = {};
          answer.columns.forEach((column, index) => {
            record[column.name] = row[index];
          });
          return record;
        }
        return row as Record<string, unknown>;
      })
    : [];

  const provenance = answer?.provenance;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">自然语言问数</h1>
        <p className="mt-1 text-sm text-slate-500">
          中文财务问题 → 期间与意图解析 → 参数化 SQL → 只读守卫 → 结果脱敏 → 解释与溯源。未配置 LLM 时完全走规则引擎。
        </p>
      </div>

      <SectionCard title="提问" description={`当前演示角色 ${role}；切换角色后会自动重新执行最近一次提问`}>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <MessageSquare
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                aria-hidden
              />
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="例如：2026年10月营业收入同比"
                className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 outline-none focus:border-slate-500"
              />
            </div>
            <button
              type="submit"
              disabled={loading || !question.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? <RefreshCw size={14} className="animate-spin" aria-hidden /> : <Sparkles size={14} aria-hidden />}
              提问
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => {
                  setQuestion(example);
                  void runAsk(example);
                }}
                className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-600 hover:border-slate-300 hover:bg-white"
              >
                {example}
              </button>
            ))}
          </div>
        </form>
      </SectionCard>

      {loading ? <LoadingState text="正在解析问题并执行查询…" /> : null}
      {error ? <ErrorAlert message={error} onRetry={() => (lastQuestionRef.current ? runAsk(lastQuestionRef.current) : undefined)} /> : null}

      {!loading && !error && !answer ? (
        <EmptyState
          title="还没有提问结果"
          description="选择上方示例问题或输入自己的问题。规则引擎未命中时会明确返回不可回答，并给出可问问题示例。"
        />
      ) : null}

      {answer ? (
        <div className="space-y-5">
          <SectionCard
            title="回答"
            actions={
              <div className="flex flex-wrap items-center gap-1.5">
                <StatusBadge status={answer.answerable ? "ok" : "unanswered"} label={answer.answerable ? "可回答" : "不可回答"} />
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">意图 {answer.intent}</span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">引擎 {answer.engine}</span>
              </div>
            }
          >
            <p className="text-sm leading-relaxed text-slate-800">{answer.answer}</p>

            {answer.suggestions.length > 0 ? (
              <div className="mt-4">
                <div className="mb-2 text-xs font-medium text-slate-600">可以这样问</div>
                <div className="flex flex-wrap gap-1.5">
                  {answer.suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => {
                        setQuestion(suggestion);
                        void runAsk(suggestion);
                      }}
                      className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-white"
                    >
                      {suggestion}
                      <ArrowUpRight size={10} aria-hidden />
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            {answer.masked_columns.length > 0 ? (
              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                当前角色 {role} 下已掩码列：{answer.masked_columns.join("、")}。掩码由后端统一处理，前端不做二次脱敏。
              </div>
            ) : null}

            {answer.warnings.length > 0 ? (
              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {answer.warnings.map((warning, index) => (
                  <div key={index}>{warning}</div>
                ))}
              </div>
            ) : null}
          </SectionCard>

          <div className="grid gap-5 xl:grid-cols-2">
            <SectionCard title="生成的 SQL" description="单条 SELECT、命名参数、只读守卫">
              <SqlBlock sql={answer.sql} />
              <p className="mt-3 text-xs leading-relaxed text-slate-500">{answer.explanation}</p>
            </SectionCard>

            <SectionCard title="假设与溯源">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="text-slate-500">期间</div>
                  <div className="mt-1 font-medium text-slate-800">{provenance?.period_code || "—"}</div>
                </div>
                <div>
                  <div className="text-slate-500">比较期</div>
                  <div className="mt-1 font-medium text-slate-800">{provenance?.comparison_period ?? "—"}</div>
                </div>
                <div>
                  <div className="text-slate-500">指标编码</div>
                  <div className="mt-1 font-mono text-[11px] text-slate-700">
                    {(provenance?.metric_codes ?? []).length ? provenance?.metric_codes.join(", ") : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">本体版本</div>
                  <div className="mt-1 font-medium text-slate-800">{provenance?.ontology_version || "—"}</div>
                </div>
                <div>
                  <div className="text-slate-500">耗时</div>
                  <div className="mt-1 font-medium text-slate-800">{provenance?.elapsed_ms ?? 0} ms</div>
                </div>
                <div>
                  <div className="text-slate-500">行数</div>
                  <div className="mt-1 font-medium text-slate-800">{answer.row_count}</div>
                </div>
              </div>
              <div className="mt-4">
                <div className="mb-1 text-xs font-medium text-slate-600">假设</div>
                <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-slate-600">
                  {answer.assumptions.length ? (
                    answer.assumptions.map((item, index) => <li key={index}>{item}</li>)
                  ) : (
                    <li>无额外假设</li>
                  )}
                </ul>
              </div>
            </SectionCard>
          </div>

          {answer.chart && answer.chart.type !== "none" && (answer.chart.spec.series?.length ?? 0) > 0 ? (
            <SectionCard title="结果图表">
              <ChartPanel
                type={answer.chart.type}
                labels={answer.chart.spec.labels ?? []}
                series={answer.chart.spec.series ?? []}
                height={300}
              />
            </SectionCard>
          ) : null}

          <SectionCard title="结果表" description={`${answer.row_count} 行`}>
            <DataTable
              columns={resultColumns}
              rows={resultRows}
              sensitiveColumns={answer.masked_columns}
              emptyText="查询未返回数据行"
              maxHeight={420}
            />
          </SectionCard>
        </div>
      ) : null}
    </div>
  );
}
