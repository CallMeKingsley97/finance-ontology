"use client";

import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { formatCurrency, formatPct } from "@/lib/format";

export default function KpiCard({
  label,
  value,
  unit,
  formatted,
  changePct,
  trend,
}: {
  label: string;
  value?: number;
  unit?: string;
  formatted?: string;
  changePct?: number | null;
  trend?: string | null;
}) {
  const display = formatted ?? (value === undefined ? "—" : formatCurrency(value));
  const showChange = changePct !== undefined && changePct !== null && Number.isFinite(changePct);
  const direction = trend ?? (showChange ? (changePct > 0 ? "up" : changePct < 0 ? "down" : "flat") : "flat");
  const TrendIcon = direction === "up" ? ArrowUp : direction === "down" ? ArrowDown : Minus;
  const trendClass =
    direction === "up" ? "text-emerald-600" : direction === "down" ? "text-rose-600" : "text-slate-400";

  return (
    <div className="card p-4">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="text-xl font-semibold tracking-tight text-slate-900">{display}</span>
        {!formatted && unit ? <span className="text-[11px] text-slate-400">{unit}</span> : null}
      </div>
      {showChange ? (
        <div className={`mt-2 flex items-center gap-1 text-xs font-medium ${trendClass}`}>
          <TrendIcon size={13} aria-hidden />
          <span>同比 {formatPct(changePct)}</span>
        </div>
      ) : null}
    </div>
  );
}
