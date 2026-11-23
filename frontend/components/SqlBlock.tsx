"use client";

import { Check, ChevronDown, Copy } from "lucide-react";
import { useState } from "react";

export default function SqlBlock({ sql, title = "SQL" }: { sql: string | null | undefined; title?: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!sql) return;
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  if (!sql) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-xs text-slate-500">
        未生成 SQL
      </div>
    );
  }

  return (
    <details className="group rounded-lg border border-slate-200 bg-slate-950/95 text-slate-100" open>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs font-medium text-slate-300">
        <span className="inline-flex items-center gap-1.5">
          <ChevronDown size={13} className="transition-transform group-open:rotate-0 group-[&:not([open])]:-rotate-90" aria-hidden />
          {title}
        </span>
        <button
          type="button"
          onClick={(event) => {
            event.preventDefault();
            copy();
          }}
          className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
        >
          {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
          {copied ? "已复制" : "复制"}
        </button>
      </summary>
      <pre className="overflow-x-auto border-t border-slate-800 px-3 py-3 text-[11px] leading-relaxed text-emerald-200">
        <code>{sql}</code>
      </pre>
    </details>
  );
}
