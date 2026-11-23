"use client";

import { UserCog } from "lucide-react";

import { useDemoRole } from "@/lib/api";
import type { Role } from "@/lib/types";

const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: "admin", label: "管理员 admin" },
  { value: "analyst", label: "分析师 analyst" },
  { value: "viewer", label: "只读 viewer" },
];

export default function RoleSwitcher() {
  const [role, setRole] = useDemoRole();

  return (
    <label className="flex items-center gap-2 text-xs text-slate-500">
      <UserCog size={16} aria-hidden className="text-slate-400" />
      <span className="sr-only">演示角色</span>
      <select
        value={role}
        onChange={(event) => setRole(event.target.value as Role)}
        className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 shadow-sm outline-none focus:border-slate-500"
        title="角色切换仅用于演示脱敏，不是生产安全边界"
      >
        {ROLE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
