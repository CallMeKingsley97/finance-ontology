"use client";

import { Lock } from "lucide-react";

export default function SensitiveBadge({ classification }: { classification?: string | null }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700"
      title={classification ? `敏感级别：${classification}` : "敏感字段"}
    >
      <Lock size={11} aria-hidden />
      {classification ? classification : "敏感"}
    </span>
  );
}
