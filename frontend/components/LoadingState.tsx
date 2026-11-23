"use client";

import { Loader2 } from "lucide-react";

export default function LoadingState({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 rounded-xl border border-slate-200 bg-white px-6 py-12 text-sm text-slate-500">
      <Loader2 size={18} className="animate-spin" aria-hidden />
      <span>{text}</span>
    </div>
  );
}
