"use client";

import {
  Activity,
  Database,
  FileText,
  Gauge,
  LayoutDashboard,
  MessageSquare,
  Network,
  Shield,
  ShieldCheck,
  Table2,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { errorMessage, health } from "@/lib/api";
import type { Health } from "@/lib/types";

import RoleSwitcher from "./RoleSwitcher";

const NAV_ITEMS = [
  { href: "/", label: "仪表盘", icon: LayoutDashboard },
  { href: "/sources", label: "数据源", icon: Database },
  { href: "/metadata", label: "元数据", icon: Table2 },
  { href: "/governance", label: "治理", icon: ShieldCheck },
  { href: "/metrics", label: "指标", icon: Gauge },
  { href: "/ontology", label: "本体", icon: Network },
  { href: "/query", label: "问数", icon: MessageSquare },
  { href: "/reports", label: "报告", icon: FileText },
  { href: "/security", label: "安全", icon: Shield },
] as const;

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLinks({ pathname, compact = false }: { pathname: string; compact?: boolean }) {
  return (
    <nav className={compact ? "flex gap-1 overflow-x-auto" : "space-y-1"}>
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={[
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              compact ? "whitespace-nowrap" : "",
              active
                ? "bg-slate-900 text-white"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
            ].join(" ")}
          >
            <Icon size={17} aria-hidden />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  const [healthInfo, setHealthInfo] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const info = await health();
        if (!cancelled) {
          setHealthInfo(info);
          setHealthError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setHealthInfo(null);
          setHealthError(errorMessage(error));
        }
      }
    };
    load();
    const timer = window.setInterval(load, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const healthy = healthInfo?.status === "ok" && healthInfo?.db_exists;
  const statusText = healthError
    ? "后端不可用"
    : healthInfo
      ? healthInfo.db_exists
        ? `后端 ${healthInfo.status}`
        : "数据库未播种"
      : "检查中…";

  return (
    <div className="min-h-screen lg:flex">
      <aside className="no-print hidden w-64 shrink-0 border-r border-slate-200 bg-white lg:flex lg:flex-col">
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-white">
            <Activity size={18} aria-hidden />
          </span>
          <div>
            <div className="text-sm font-semibold text-slate-900">财务 Ontology</div>
            <div className="text-[11px] text-slate-500">Agent MVP</div>
          </div>
        </div>
        <div className="flex-1 p-3">
          <NavLinks pathname={pathname} />
        </div>
        <div className="border-t border-slate-200 p-4 text-[11px] leading-relaxed text-slate-500">
          演示版本：角色切换仅用于展示脱敏效果，不是生产安全边界。
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
          <div className="flex h-16 items-center justify-between gap-3 px-4 sm:px-6">
            <div className="flex items-center gap-3">
              <span className="text-sm font-semibold text-slate-900 lg:hidden">财务 Ontology</span>
              <span
                className={[
                  "hidden items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium sm:flex",
                  healthy
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : healthInfo?.db_exists === false
                      ? "border-amber-200 bg-amber-50 text-amber-700"
                      : "border-rose-200 bg-rose-50 text-rose-700",
                ].join(" ")}
                title={healthInfo?.db_path ?? healthError ?? undefined}
              >
                <span
                  className={[
                    "h-2 w-2 rounded-full",
                    healthy ? "bg-emerald-500" : healthInfo?.db_exists === false ? "bg-amber-500" : "bg-rose-500",
                  ].join(" ")}
                />
                {statusText}
                {healthInfo?.latest_period ? <span className="text-slate-500">· {healthInfo.latest_period}</span> : null}
              </span>
            </div>
            <RoleSwitcher />
          </div>
          <div className="border-t border-slate-100 px-3 py-2 lg:hidden">
            <NavLinks pathname={pathname} compact />
          </div>
        </header>

        <main className="print-area mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>

        <footer className="no-print border-t border-slate-200 px-6 py-4 text-center text-xs text-slate-400">
          企业财务 Ontology Agent MVP · 本地 SQLite · 规则驱动本体与问数 · LLM 可选
        </footer>
      </div>
    </div>
  );
}
