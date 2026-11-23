"use client";

import { useEffect, useState } from "react";

import type {
  AuditResponse,
  CatalogChangesResponse,
  CatalogColumn,
  CatalogColumnUpdateBody,
  CatalogPreview,
  CatalogSummary,
  CatalogTableDetail,
  CatalogTableListResponse,
  CatalogTableUpdateBody,
  DashboardSummary,
  Envelope,
  ErrorEnvelope,
  Health,
  MetricAlias,
  MetricAliasBody,
  MetricCreateBody,
  MetricDetail,
  MetricListResponse,
  MetricSummary,
  MetricUpdateBody,
  MetricVersion,
  QualityAlertListResponse,
  QualityRule,
  QualityRuleBody,
  QualityRuleListResponse,
  QualityRun,
  QualityRunListResponse,
  QualitySummary,
  QueryAnswer,
  RelationListResponse,
  ReportDocument,
  ReportPackageDocument,
  ReportPackageRequestBody,
  ReportPackageTemplatesResponse,
  ReportTemplatesResponse,
  Role,
  SeedResult,
  SecurityPolicy,
  SecurityRoleResponse,
  SourceListResponse,
  SourceItem,
  SourceMetadataResponse,
  SourceTableDetail,
  SourceTestResult,
  SyncBatchResult,
  SyncRun,
  SyncRunListResponse,
  SyncSourcesResponse,
  SyncStartResult,
  TableDetail,
  TableListResponse,
  Term,
  TermDetail,
  TermListResponse,
} from "./types";

const ROLE_STORAGE_KEY = "demo-role";
const ROLE_EVENT = "demo-role-changed";

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const VALID_ROLES: Role[] = ["admin", "analyst", "viewer"];

export function getRole(): Role {
  if (typeof window === "undefined") return "admin";
  const stored = window.localStorage.getItem(ROLE_STORAGE_KEY);
  if (stored && (VALID_ROLES as string[]).includes(stored)) return stored as Role;
  return "admin";
}

export function setRole(role: Role): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  window.dispatchEvent(new Event(ROLE_EVENT));
}

/** 角色状态 Hook：切换角色后触发重新渲染与数据刷新。 */
export function useDemoRole(): [Role, (role: Role) => void] {
  const [role, setRoleState] = useState<Role>("admin");

  useEffect(() => {
    setRoleState(getRole());
    const handler = () => setRoleState(getRole());
    window.addEventListener(ROLE_EVENT, handler);
    return () => window.removeEventListener(ROLE_EVENT, handler);
  }, []);

  const update = (next: Role) => {
    setRole(next);
    setRoleState(next);
  };

  return [role, update];
}

/**
 * 统一 API 客户端：
 * - 自动注入 X-Demo-Role（显式传入的 header 优先，overrideRole 用于安全页预览）
 * - 解包 {code, message, data}
 * - 非 2xx 或 code != OK 抛 ApiError
 * - 仅访问同源 /api/*，不硬编码后端地址
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  overrideRole?: Role,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("X-Demo-Role")) {
    headers.set("X-Demo-Role", overrideRole ?? getRole());
  }
  headers.set("Accept", "application/json");
  const isFormData =
    typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body !== undefined && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...init,
    headers,
    cache: "no-store",
  });

  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const envelope = (payload ?? {}) as Partial<ErrorEnvelope>;
    throw new ApiError(
      response.status,
      envelope.code ?? "HTTP_ERROR",
      envelope.message ?? `请求失败（HTTP ${response.status}）`,
      envelope.details,
    );
  }

  if (payload && typeof payload === "object" && "code" in payload) {
    const envelope = payload as Envelope<T>;
    if (envelope.code !== "OK") {
      throw new ApiError(response.status, envelope.code, envelope.message || "请求失败", envelope.data);
    }
    return envelope.data;
  }

  return payload as T;
}

function queryString(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

// ===== 1. 健康检查 =====
export const health = () => apiFetch<Health>("/api/health");

// ===== 2. 仪表盘 =====
export const dashboard = () => apiFetch<DashboardSummary>("/api/dashboard/summary");

// ===== 3-5. 数据源 =====
export const sources = () => apiFetch<SourceListResponse>("/api/sources");

export const seedSource = (body: { reset?: boolean } = { reset: true }) =>
  apiFetch<SeedResult>("/api/sources/seed", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const testSource = (body: { path?: string } = {}) =>
  apiFetch<SourceTestResult>("/api/sources/test", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const createSource = (body: {
  name: string;
  kind: "postgresql" | "mysql";
  config: {
    host: string;
    port: number;
    database: string;
    username: string;
    schema?: string;
  };
  secret: string;
}) =>
  apiFetch<SourceItem>("/api/sources", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const sourceDetail = (id: string) =>
  apiFetch<SourceItem>(`/api/sources/${encodeURIComponent(id)}`);

export const deleteSource = (id: string) =>
  apiFetch<{ deleted: boolean; id: string }>(`/api/sources/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });

export const testSourceById = (id: string) =>
  apiFetch<SourceTestResult>(`/api/sources/${encodeURIComponent(id)}/test`, {
    method: "POST",
  });

export const uploadSource = (form: FormData) =>
  apiFetch<SourceItem>("/api/sources/upload", {
    method: "POST",
    body: form,
  });

export const replaceSourceFile = (id: string, form: FormData) =>
  apiFetch<SourceItem>(`/api/sources/${encodeURIComponent(id)}/file`, {
    method: "POST",
    body: form,
  });

export const sourceMetadata = (id: string, refresh = false) =>
  apiFetch<SourceMetadataResponse>(
    `/api/sources/${encodeURIComponent(id)}/metadata${queryString({
      refresh: refresh ? true : undefined,
    })}`,
  );

export const sourceTableDetail = (
  id: string,
  table: string,
  schema?: string,
  limit?: number,
) =>
  apiFetch<SourceTableDetail>(
    `/api/sources/${encodeURIComponent(id)}/metadata/table${queryString({
      table,
      schema,
      limit,
    })}`,
  );

// ===== 6-7. 元数据 =====
export const metadataTables = () => apiFetch<TableListResponse>("/api/metadata/tables");

export const metadataTable = (table: string, role?: Role) =>
  apiFetch<TableDetail>(`/api/metadata/tables/${encodeURIComponent(table)}`, {}, role);

// ===== 8-13. 本体 =====
export const generateOntology = (body: { force?: boolean } = { force: true }) =>
  apiFetch<{
    status?: string;
    generated?: boolean;
    skipped?: boolean;
    counts?: { terms: number; relations: number; sql_attributes: number; columns: number };
    sensitive_columns?: string[];
    version?: string;
    duration_ms?: number;
  }>("/api/ontology/generate", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const ontologyTerms = (
  params: { q?: string; certified?: boolean; category?: string; limit?: number } = {},
) => apiFetch<TermListResponse>(`/api/ontology/terms${queryString(params)}`);

export const ontologyTerm = (id: number, role?: Role) =>
  apiFetch<TermDetail>(`/api/ontology/terms/${id}`, {}, role);

export const updateTerm = (
  id: number,
  body: {
    display_name?: string;
    description?: string;
    synonyms?: string[];
    mapped_table?: string | null;
    mapped_columns?: string[];
  },
) =>
  apiFetch<Term>(`/api/ontology/terms/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const certifyTerm = (id: number, body: { certified?: boolean } = { certified: true }) =>
  apiFetch<Term>(`/api/ontology/terms/${id}/certify`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const relations = () => apiFetch<RelationListResponse>("/api/ontology/relations");

// ===== 14. 问数 =====
export const ask = (question: string, limit = 20) =>
  apiFetch<QueryAnswer>("/api/query/ask", {
    method: "POST",
    body: JSON.stringify({ question, limit }),
  });

// ===== 15-16. 报告 =====
export const reportTemplates = () => apiFetch<ReportTemplatesResponse>("/api/reports/templates");

export const generateReport = (body: {
  template_code: string;
  period: string;
  comparison?: "yoy" | "mom" | "none";
}) =>
  apiFetch<ReportDocument>("/api/reports/generate", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ===== 17-19. 安全 =====
export const securityRoles = () => apiFetch<SecurityRoleResponse>("/api/security/roles");

export const securityPolicies = () => apiFetch<SecurityPolicy>("/api/security/policies");

export const securityAudit = (params: { role?: string; limit?: number } = {}) =>
  apiFetch<AuditResponse>(`/api/security/audit${queryString(params)}`);

// ===== 20. 同步监控 =====
export const syncSource = (sourceId: string) =>
  apiFetch<SyncStartResult>(`/api/sources/${encodeURIComponent(sourceId)}/sync`, {
    method: "POST",
  });

export const syncAll = (body: { source_ids?: string[] } = {}) =>
  apiFetch<SyncBatchResult>("/api/sync/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const syncRuns = (
  params: { source_id?: string; status?: string; limit?: number; offset?: number } = {},
) => apiFetch<SyncRunListResponse>(`/api/sync/runs${queryString(params)}`);

export const syncRun = (runId: string) =>
  apiFetch<SyncRun>(`/api/sync/runs/${encodeURIComponent(runId)}`);

export const syncSources = () => apiFetch<SyncSourcesResponse>("/api/sync/sources");

// ===== 21. 元数据目录 =====
export const catalogSummary = () => apiFetch<CatalogSummary>("/api/catalog/summary");

export const catalogTables = (
  params: {
    source_id?: string;
    status?: string;
    owner?: string;
    business_domain?: string;
    sensitivity_level?: string;
    q?: string;
    page?: number;
    page_size?: number;
  } = {},
) => apiFetch<CatalogTableListResponse>(`/api/catalog/tables${queryString(params)}`);

export const catalogTable = (tableId: number) =>
  apiFetch<CatalogTableDetail>(`/api/catalog/tables/${tableId}`);

export const updateCatalogTable = (tableId: number, body: CatalogTableUpdateBody) =>
  apiFetch<CatalogTableDetail>(`/api/catalog/tables/${tableId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const updateCatalogColumn = (columnId: number, body: CatalogColumnUpdateBody) =>
  apiFetch<CatalogColumn>(`/api/catalog/columns/${columnId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const catalogChanges = (tableId: number, limit = 20) =>
  apiFetch<CatalogChangesResponse>(
    `/api/catalog/tables/${tableId}/changes${queryString({ limit })}`,
  );

export const catalogPreview = (tableId: number, limit = 5) =>
  apiFetch<CatalogPreview>(
    `/api/catalog/tables/${tableId}/preview${queryString({ limit })}`,
  );

// ===== 22. 指标注册表 =====
export const metricsList = (
  params: {
    q?: string;
    status?: string;
    business_domain?: string;
    owner?: string;
    page?: number;
    page_size?: number;
  } = {},
) => apiFetch<MetricListResponse>(`/api/metrics${queryString(params)}`);

export const metricSummary = () => apiFetch<MetricSummary>("/api/metrics/summary");

export const metricDetail = (code: string) =>
  apiFetch<MetricDetail>(`/api/metrics/${encodeURIComponent(code)}`);

export const createMetric = (body: MetricCreateBody) =>
  apiFetch<MetricDetail>("/api/metrics", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateMetric = (code: string, body: MetricUpdateBody) =>
  apiFetch<MetricDetail>(`/api/metrics/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const submitMetricVersion = (code: string, version: string) =>
  apiFetch<MetricVersion>(
    `/api/metrics/${encodeURIComponent(code)}/versions/${encodeURIComponent(version)}/submit`,
    { method: "POST", body: JSON.stringify({}) },
  );

export const certifyMetricVersion = (code: string, version: string) =>
  apiFetch<MetricDetail>(
    `/api/metrics/${encodeURIComponent(code)}/versions/${encodeURIComponent(version)}/certify`,
    { method: "POST", body: JSON.stringify({}) },
  );

export const runMetricTests = (code: string) =>
  apiFetch<{
    code: string;
    version: string;
    items: Record<string, unknown>[];
    passed: number;
    failed: number;
    errors: number;
    total: number;
  }>(`/api/metrics/${encodeURIComponent(code)}/test-cases/run`, { method: "POST" });

export const createMetricAlias = (code: string, body: MetricAliasBody) =>
  apiFetch<MetricAlias>(`/api/metrics/${encodeURIComponent(code)}/aliases`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateMetricAlias = (code: string, aliasId: number, body: Partial<MetricAliasBody>) =>
  apiFetch<MetricAlias>(
    `/api/metrics/${encodeURIComponent(code)}/aliases/${aliasId}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );

export const deleteMetricAlias = (code: string, aliasId: number) =>
  apiFetch<{ deleted: boolean; id: number }>(
    `/api/metrics/${encodeURIComponent(code)}/aliases/${aliasId}`,
    { method: "DELETE" },
  );

// ===== 23. 质量规则 =====
export const qualitySummary = () => apiFetch<QualitySummary>("/api/quality/summary");

export const qualityRules = (
  params: {
    enabled?: boolean;
    rule_type?: string;
    target_db?: string;
    q?: string;
  } = {},
) => apiFetch<QualityRuleListResponse>(`/api/quality/rules${queryString(params)}`);

export const createQualityRule = (body: QualityRuleBody) =>
  apiFetch<QualityRule>("/api/quality/rules", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateQualityRule = (code: string, body: QualityRuleBody) =>
  apiFetch<QualityRule>(`/api/quality/rules/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const runQualityRule = (code: string, body: { period?: string } = {}) =>
  apiFetch<QualityRun>(`/api/quality/rules/${encodeURIComponent(code)}/run`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const runQuality = (body: { period?: string; rule_codes?: string[] } = {}) =>
  apiFetch<QualityRun>("/api/quality/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const qualityRuns = (
  params: { status?: string; trigger_type?: string; limit?: number; offset?: number } = {},
) => apiFetch<QualityRunListResponse>(`/api/quality/runs${queryString(params)}`);

export const qualityRun = (runId: string) =>
  apiFetch<QualityRun>(`/api/quality/runs/${encodeURIComponent(runId)}`);

export const qualityAlerts = (params: { days?: number; limit?: number } = {}) =>
  apiFetch<QualityAlertListResponse>(`/api/quality/alerts${queryString(params)}`);

// ===== 24. 报表包 =====
export const reportPackageTemplates = () =>
  apiFetch<ReportPackageTemplatesResponse>("/api/report-packages/templates");

export const generateReportPackage = (body: ReportPackageRequestBody) =>
  apiFetch<ReportPackageDocument>("/api/report-packages/generate", {
    method: "POST",
    body: JSON.stringify(body),
  });

/** 错误对象转可展示文案。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "未知错误";
}

export { ROLE_EVENT, ROLE_STORAGE_KEY };
