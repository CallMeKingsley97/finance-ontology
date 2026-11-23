"use client";

import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

export default function EmptyState({
  title = "暂无数据",
  description,
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <Inbox size={22} className="text-slate-400" aria-hidden />
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {description ? <p className="max-w-md text-xs leading-relaxed text-slate-500">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
