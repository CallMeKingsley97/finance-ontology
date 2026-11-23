"use client";

const STYLES: Record<string, string> = {
  ok: "border-emerald-200 bg-emerald-50 text-emerald-700",
  succeeded: "border-emerald-200 bg-emerald-50 text-emerald-700",
  passed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  certified: "border-emerald-200 bg-emerald-50 text-emerald-700",
  healthy: "border-emerald-200 bg-emerald-50 text-emerald-700",
  closed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  up: "border-emerald-200 bg-emerald-50 text-emerald-700",
  running: "border-sky-200 bg-sky-50 text-sky-700",
  queued: "border-sky-200 bg-sky-50 text-sky-700",
  active: "border-emerald-200 bg-emerald-50 text-emerald-700",
  degraded: "border-amber-200 bg-amber-50 text-amber-700",
  unanswered: "border-amber-200 bg-amber-50 text-amber-700",
  pending: "border-amber-200 bg-amber-50 text-amber-700",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  uncertified: "border-amber-200 bg-amber-50 text-amber-700",
  partial: "border-amber-200 bg-amber-50 text-amber-700",
  draft: "border-slate-200 bg-slate-50 text-slate-600",
  deprecated: "border-slate-200 bg-slate-50 text-slate-600",
  skipped: "border-slate-200 bg-slate-50 text-slate-600",
  blocker: "border-rose-200 bg-rose-50 text-rose-700",
  stale: "border-slate-200 bg-slate-50 text-slate-600",
  missing: "border-slate-200 bg-slate-50 text-slate-600",
  interrupted: "border-slate-200 bg-slate-50 text-slate-600",
  never: "border-slate-200 bg-slate-50 text-slate-600",
  not_run: "border-slate-200 bg-slate-50 text-slate-600",
  disabled: "border-slate-200 bg-slate-50 text-slate-600",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  error: "border-rose-200 bg-rose-50 text-rose-700",
  rejected: "border-rose-200 bg-rose-50 text-rose-700",
  dead_letter: "border-rose-200 bg-rose-50 text-rose-700",
  down: "border-rose-200 bg-rose-50 text-rose-700",
};

const LABELS: Record<string, string> = {
  ok: "正常",
  succeeded: "成功",
  passed: "通过",
  certified: "已认证",
  healthy: "健康",
  closed: "已关闭",
  degraded: "降级",
  unanswered: "不可回答",
  pending: "待处理",
  warning: "警告",
  uncertified: "未认证",
  failed: "失败",
  error: "错误",
  rejected: "已拒绝",
  up: "上升",
  down: "下降",
  running: "运行中",
  queued: "排队中",
  active: "正常",
  partial: "部分成功",
  draft: "草稿",
  deprecated: "已废弃",
  skipped: "跳过",
  blocker: "阻断",
  stale: "已过期",
  missing: "缺失",
  interrupted: "已中断",
  never: "未同步",
  not_run: "未执行",
  disabled: "已停用",
  dead_letter: "死信",
};

export default function StatusBadge({ status, label }: { status: string; label?: string }) {
  const key = String(status ?? "").toLowerCase();
  const style = STYLES[key] ?? "border-slate-200 bg-slate-50 text-slate-600";
  const text = label ?? LABELS[key] ?? status ?? "—";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {text}
    </span>
  );
}
