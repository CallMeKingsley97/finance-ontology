"use client";

import { Check, ChevronDown, Eye, LineChart, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useDemoRole } from "@/lib/api";
import type { Role } from "@/lib/types";

const ROLE_OPTIONS: {
  value: Role;
  label: string;
  code: string;
  description: string;
  icon: LucideIcon;
}[] = [
  {
    value: "admin",
    label: "管理员",
    code: "admin",
    description: "可播种、编辑与认证",
    icon: ShieldCheck,
  },
  {
    value: "analyst",
    label: "分析师",
    code: "analyst",
    description: "部分掩码，可编辑目录",
    icon: LineChart,
  },
  {
    value: "viewer",
    label: "只读",
    code: "viewer",
    description: "敏感字段全部掩码",
    icon: Eye,
  },
];

export default function RoleSwitcher() {
  const [role, setRole] = useDemoRole();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const current = ROLE_OPTIONS.find((option) => option.value === role) ?? ROLE_OPTIONS[0];

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const select = (next: Role) => {
    setRole(next);
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`演示角色：${current.label}，点击切换`}
        title="角色切换仅用于演示脱敏，不是生产安全边界"
        className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-2.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-1"
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white">
          <current.icon size={13} aria-hidden />
        </span>
        <span className="hidden sm:inline">{current.label}</span>
        <ChevronDown
          size={13}
          aria-hidden
          className={`text-slate-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="选择演示角色"
          className="absolute right-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white p-1 shadow-lg shadow-slate-900/5"
        >
          {ROLE_OPTIONS.map((option) => {
            const Icon = option.icon;
            const active = option.value === role;
            return (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => select(option.value)}
                className={[
                  "flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 text-left transition-colors",
                  active ? "bg-slate-50" : "hover:bg-slate-50",
                ].join(" ")}
              >
                <span
                  className={[
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                    active ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600",
                  ].join(" ")}
                >
                  <Icon size={14} aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-medium text-slate-900">
                    {option.label}
                    <span className="ml-1.5 font-normal text-slate-400">{option.code}</span>
                  </span>
                  <span className="block truncate text-[11px] text-slate-500">{option.description}</span>
                </span>
                {active ? <Check size={14} aria-hidden className="shrink-0 text-slate-900" /> : null}
              </button>
            );
          })}
          <div className="mt-1 border-t border-slate-100 px-2 py-1.5 text-[11px] leading-relaxed text-slate-400">
            仅用于演示脱敏效果，不是生产安全边界。
          </div>
        </div>
      ) : null}
    </div>
  );
}
