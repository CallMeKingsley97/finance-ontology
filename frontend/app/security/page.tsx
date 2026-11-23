"use client";

import { Shield } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import DataTable from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import LoadingState from "@/components/LoadingState";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import {
  errorMessage,
  metadataTable,
  securityAudit,
  securityPolicies,
  securityRoles,
  useDemoRole,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import type {
  AuditItem,
  Role,
  SecurityPolicy,
  SecurityRole,
  TableDetail,
} from "@/lib/types";

const PREVIEW_ROLES: Role[] = ["admin", "analyst", "viewer"];

function maskingText(masking: Record<string, string> | string | undefined): string {
  if (!masking) return "—";
  if (typeof masking === "string") return masking;
  return PREVIEW_ROLES.map((role) => `${role}: ${masking[role] ?? "—"}`).join("；");
}

export default function SecurityPage() {
  const [role] = useDemoRole();

  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [rolesLoading, setRolesLoading] = useState(true);
  const [rolesError, setRolesError] = useState<string | null>(null);

  const [policy, setPolicy] = useState<SecurityPolicy | null>(null);
  const [policyLoading, setPolicyLoading] = useState(true);
  const [policyError, setPolicyError] = useState<string | null>(null);

  const [auditRole, setAuditRole] = useState("all");
  const [auditLimit, setAuditLimit] = useState(20);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState<string | null>(null);

  const [preview, setPreview] = useState<Partial<Record<Role, TableDetail | null>>>({});
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const loadRoles = useCallback(async () => {
    setRolesLoading(true);
    setRolesError(null);
    try {
      const data = await securityRoles();
      setRoles(data.items);
    } catch (err) {
      setRolesError(errorMessage(err));
      setRoles([]);
    } finally {
      setRolesLoading(false);
    }
  }, [role]);

  const loadPolicies = useCallback(async () => {
    setPolicyLoading(true);
    setPolicyError(null);
    try {
      setPolicy(await securityPolicies());
    } catch (err) {
      setPolicyError(errorMessage(err));
      setPolicy(null);
    } finally {
      setPolicyLoading(false);
    }
  }, [role]);

  const loadAudit = useCallback(async () => {
    setAuditLoading(true);
    setAuditError(null);
    try {
      const data = await securityAudit({
        role: auditRole === "all" ? undefined : auditRole,
        limit: auditLimit,
      });
      setAudit(data.items);
    } catch (err) {
      setAuditError(errorMessage(err));
      setAudit([]);
    } finally {
      setAuditLoading(false);
    }
  }, [role, auditRole, auditLimit]);

  const loadPreview = useCallback(async () => {
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const results = await Promise.all(
        PREVIEW_ROLES.map(async (previewRole) => {
          const detail = await metadataTable("dim_customer", previewRole);
          return [previewRole, detail] as const;
        }),
      );
      const next: Partial<Record<Role, TableDetail | null>> = {};
      results.forEach(([previewRole, detail]) => {
        next[previewRole] = detail;
      });
      setPreview(next);
    } catch (err) {
      setPreviewError(errorMessage(err));
      setPreview({});
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRoles();
    void loadPolicies();
  }, [loadRoles, loadPolicies]);

  useEffect(() => {
    void loadAudit();
  }, [loadAudit]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview, role]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">安全与脱敏</h1>
        <p className="mt-1 text-sm text-slate-500">
          X-Demo-Role 演示三角色差异：admin 原值、analyst 部分掩码、viewer 完全掩码；所有问数写入审计日志。
        </p>
      </div>

      <div className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
        <Shield size={18} className="mt-0.5 shrink-0" aria-hidden />
        <div>
          <div className="font-semibold">演示用，不是生产安全边界</div>
          <p className="mt-1 text-xs leading-relaxed text-amber-700">
            角色通过请求头 X-Demo-Role 传递，没有认证与授权校验；直接访问本地 SQLite 文件可绕过掩码。生产环境需要正式认证、RBAC
            与数据库访问控制。
          </p>
        </div>
      </div>

      <SectionCard title="角色矩阵" description="能力与脱敏策略概览">
        {rolesLoading ? <LoadingState text="正在读取角色…" /> : null}
        {rolesError ? <ErrorAlert message={rolesError} onRetry={loadRoles} /> : null}
        {!rolesLoading && !rolesError && roles.length === 0 ? <EmptyState title="暂无角色定义" /> : null}
        {roles.length > 0 ? (
          <DataTable
            columns={[
              { key: "code", label: "角色" },
              { key: "name", label: "名称" },
              { key: "description", label: "说明" },
              {
                key: "permissions",
                label: "能力",
                render: (_row, value) => (Array.isArray(value) ? value.join("、") : "—"),
              },
              { key: "masking", label: "脱敏策略" },
            ]}
            rows={roles.map((item) => ({ ...item }))}
          />
        ) : null}
      </SectionCard>

      <SectionCard title="敏感列与掩码规则" description="所有敏感列统一由后端 mask_rows 处理">
        {policyLoading ? <LoadingState text="正在读取安全策略…" /> : null}
        {policyError ? <ErrorAlert message={policyError} onRetry={loadPolicies} /> : null}
        {policy ? (
          <>
            <DataTable
              columns={[
                { key: "table", label: "表" },
                { key: "column", label: "列" },
                { key: "classification", label: "分类" },
                { key: "description", label: "说明" },
                {
                  key: "masking",
                  label: "admin / analyst / viewer",
                  render: (_row, value) => maskingText(value as Record<string, string>),
                },
              ]}
              rows={policy.sensitive_columns.map((item) => ({ ...item }))}
              emptyText="没有配置敏感列"
            />
            {policy.audit_fields.length > 0 ? (
              <p className="mt-3 text-[11px] text-slate-500">审计字段：{policy.audit_fields.join("、")}</p>
            ) : null}
          </>
        ) : null}
      </SectionCard>

      <SectionCard
        title="实时脱敏预览"
        description="对同一张敏感表 dim_customer 分别以三种角色请求，观察 phone / bank_account 差异"
      >
        {previewLoading ? <LoadingState text="正在获取三角色样本…" /> : null}
        {previewError ? <ErrorAlert message={previewError} onRetry={loadPreview} /> : null}
        {!previewLoading && !previewError ? (
          <div className="grid gap-4 lg:grid-cols-3">
            {PREVIEW_ROLES.map((previewRole) => {
              const detail = preview[previewRole];
              return (
                <div key={previewRole} className="rounded-xl border border-slate-200 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-700">{previewRole}</span>
                    <span className="text-[11px] text-slate-400">
                      {detail ? `${detail.sample_rows.length} 行样本` : "无数据"}
                    </span>
                  </div>
                  {detail ? (
                    <DataTable
                      columns={[
                        { key: "customer_name", label: "客户" },
                        { key: "phone", label: "手机号" },
                        { key: "bank_account", label: "银行账号" },
                      ]}
                      rows={detail.sample_rows.slice(0, 5)}
                      sensitiveColumns={["phone", "bank_account"]}
                      emptyText="无样本行"
                    />
                  ) : (
                    <div className="rounded-lg border border-dashed border-slate-300 px-3 py-6 text-center text-xs text-slate-500">
                      数据库未播种或无样本
                    </div>
                  )}
                  {detail?.masked_columns?.length ? (
                    <p className="mt-2 text-[11px] text-slate-500">
                      已掩码：{(detail.masked_columns ?? []).join("、")}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}
      </SectionCard>

      <SectionCard
        title="查询审计"
        description="每次问数（含不可回答与失败）均留痕，不记录敏感原值"
        actions={
          <div className="flex items-center gap-2">
            <select
              value={auditRole}
              onChange={(event) => setAuditRole(event.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-slate-500"
            >
              <option value="all">全部角色</option>
              <option value="admin">admin</option>
              <option value="analyst">analyst</option>
              <option value="viewer">viewer</option>
            </select>
            <select
              value={auditLimit}
              onChange={(event) => setAuditLimit(Number(event.target.value))}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-slate-500"
            >
              <option value={20}>20 条</option>
              <option value={50}>50 条</option>
              <option value={100}>100 条</option>
            </select>
          </div>
        }
      >
        {auditLoading ? <LoadingState text="正在读取审计日志…" /> : null}
        {auditError ? <ErrorAlert message={auditError} onRetry={loadAudit} /> : null}
        {!auditLoading && !auditError ? (
          <DataTable
            columns={[
              { key: "created_at", label: "时间", render: (_row, value) => formatDate(value) },
              { key: "question", label: "问题" },
              { key: "intent", label: "意图" },
              { key: "role", label: "角色" },
              {
                key: "status",
                label: "状态",
                align: "center",
                render: (_row, value) => <StatusBadge status={String(value)} />,
              },
              { key: "row_count", label: "行数", align: "right" },
              {
                key: "masked_columns",
                label: "掩码列",
                render: (_row, value) => (Array.isArray(value) && value.length ? value.join("、") : "—"),
              },
              { key: "duration_ms", label: "耗时(ms)", align: "right" },
              {
                key: "sql_preview",
                label: "SQL 预览",
                render: (_row, value) => (
                  <code
                    className="block max-w-xs truncate font-mono text-[10px] text-slate-500"
                    title={String(value ?? "")}
                  >
                    {String(value ?? "—")}
                  </code>
                ),
              },
            ]}
            rows={audit.map((item) => ({ ...item }))}
            emptyText="暂无审计记录；在问数页发起提问后会生成"
            maxHeight={420}
          />
        ) : null}
      </SectionCard>
    </div>
  );
}
