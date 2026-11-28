/**
 * 与后端 FastAPI 契约保持一致的类型定义（snake_case）。
 * 契约来源：02-design/mvp-tech-design.md §3。
 */

export type Role = "admin" | "analyst" | "viewer";

export interface Envelope<T> {
  code: string;
  message: string;
  data: T;
}

export interface ErrorEnvelope {
  code: string;
  message: string;
  details?: unknown;
}

export interface Health {
  status: string;
  version: string;
  db_path: string;
  db_exists: boolean;
  table_count: number;
  latest_period: string | null;
  llm_enabled: boolean;
  time: string;
}

export interface KpiItem {
  key: string;
  label: string;
  value?: number | null;
  unit: string;
  formatted: string;
  change_pct?: number | null;
  trend?: string | null;
}

export interface ReconciliationCheck {
  name: string;
  left: number;
  right: number;
  diff: number;
  passed: boolean;
  /** 仅知识勾稽规则携带。 */
  tolerance?: number;
  /** 仅知识勾稽规则携带。 */
  basis?: string;
  /** 仅知识勾稽规则携带。 */
  source_id?: string;
  /** 仅知识勾稽规则携带。 */
  source_name?: string;
}

export interface Reconciliation {
  passed: boolean;
  checks: ReconciliationCheck[];
  /** 本次执行的知识规则条数（知识不可用时为 0）。 */
  knowledge_checks?: number;
  /** 被跳过的知识规则数（SQL 校验失败或取数为空）。 */
  skipped?: number;
  /** 跳过规则的可读原因，可直接展示。 */
  knowledge_warnings?: string[];
}

export interface DashboardSummary {
  latest_period: string | null;
  kpis: KpiItem[];
  trend: { period: string; revenue: number; net_profit: number }[];
  reconciliation: Reconciliation;
  counts: {
    tables: number;
    terms: number;
    relations: number;
    sql_attributes: number;
    queries: number;
  };
}

export type SourceKind = "sqlite" | "postgresql" | "mysql" | "csv" | "excel";

export interface SourceItem {
  id: string;
  name: string;
  kind: SourceKind | string;
  path: string;
  status: string;
  table_count: number;
  size_bytes: number;
  last_seeded_at: string | null;
  description?: string;
  managed?: boolean;
  has_secret?: boolean;
  config?: Record<string, unknown>;
  original_filename?: string | null;
  last_tested_at?: string | null;
  last_error?: string | null;
  metadata_updated_at?: string | null;
}

export interface SeedResult {
  seeded: boolean;
  tables: string[];
  rows: Record<string, number>;
  duration_ms: number;
  reconciliation: Reconciliation | null;
}

export interface SourceTestResult {
  success: boolean;
  latency_ms: number;
  sqlite_version: string | null;
  server_version?: string | null;
  table_count: number;
  latest_period: string | null;
  message?: string;
}

export interface SourceMetadataTable {
  schema: string;
  name: string;
  table_type: string;
  row_count: number | null;
  row_count_estimated: boolean;
  column_count: number;
  sensitive_columns: string[];
  description: string;
}

export interface SourceMetadataResponse {
  source_id: string;
  kind: string;
  refreshed_at: string;
  cached: boolean;
  truncated: boolean;
  warnings: string[];
  tables: SourceMetadataTable[];
}

export interface SourceTableDetail extends TableDetail {
  source_id: string;
  schema: string;
  table_type: string;
  comment?: string | null;
  primary_key?: string[];
}

export interface TableSummary {
  name: string;
  row_count: number;
  column_count: number;
  sensitive_columns: string[];
  description: string;
}

export interface TableColumn {
  name: string;
  type: string;
  pk: boolean;
  notnull: boolean;
  default: string | null;
  is_sensitive: boolean;
  classification: string | null;
  sample_masked: string | null;
  description: string | null;
}

export interface ForeignKey {
  from_column: string;
  to_table: string;
  to_column: string;
}

export interface IndexInfo {
  name: string;
  unique: boolean;
  columns: string[];
}

export interface TableDetail {
  name: string;
  row_count: number;
  description?: string;
  columns: TableColumn[];
  foreign_keys: ForeignKey[];
  indexes: IndexInfo[];
  sample_rows: Record<string, unknown>[];
  masked_columns?: string[];
}

export interface SqlAttribute {
  attribute_id?: number;
  code: string;
  name: string;
  term_id?: number;
  formula_sql: string;
  description: string;
  unit: string;
  certified: boolean;
}

export interface Term {
  term_id: number;
  term_code: string;
  name: string;
  display_name: string;
  description: string;
  synonyms: string[];
  mapped_table: string | null;
  mapped_columns: string[];
  category: string;
  certified: boolean;
  certified_by: string | null;
  certified_at?: string | null;
  source?: string;
  updated_at: string;
  sql_attributes?: SqlAttribute[];
}

export interface TermColumn {
  column_id: number;
  term_id: number;
  table_name: string;
  column_name: string;
  display_name: string;
  description: string;
  is_sensitive: boolean;
  classification: string | null;
  sample_masked: string | null;
}

export interface ChangeLogItem {
  change_id: number;
  term_id: number;
  field_name: string;
  old_value: string | null;
  new_value: string | null;
  changed_by_role: string;
  changed_at: string;
}

export interface TermDetail extends Term {
  columns: TermColumn[];
  sql_attributes: SqlAttribute[];
  change_log: ChangeLogItem[];
}

export interface Relation {
  relation_id: number;
  relation_code: string;
  from_term: string;
  from_term_code?: string;
  to_term: string;
  to_term_code?: string;
  relation_type: string;
  cardinality: string;
  join_hint: string;
  description: string;
}

export interface ChartSpec {
  x?: string;
  labels?: string[];
  series?: { name: string; data: number[] }[];
}

export interface QueryChart {
  type: "line" | "bar" | "pie" | "none" | string;
  spec: ChartSpec;
}

export interface QueryColumn {
  name: string;
  type: string;
}

export interface Provenance {
  period_code: string;
  comparison_period?: string | null;
  metric_codes: string[];
  ontology_version: string;
  elapsed_ms: number;
  metric_definition_code?: string | null;
  metric_version?: string | null;
  alias_matched?: string | null;
  alias_type?: string | null;
  /** 本次回答引用的知识条目 id；无知识关联时为 []。 */
  knowledge_entry_ids?: string[];
  /** 知识包版本（如 "2026.11.1"）；知识不可用时为 null。 */
  knowledge_version?: string | null;
}

export interface QueryMetricCard {
  code: string;
  name: string;
  definition: string;
  formula_text: string;
  boundary: Record<string, string>;
  unit: string;
  version: string | null;
  certified_by?: string | null;
  certified_at?: string | null;
  /** 知识包提供的准则/管理依据（旧响应可能缺失）。 */
  basis?: string;
  source_ref?: string | null;
  source_name?: string | null;
  source_url?: string | null;
}

export interface QueryCandidate {
  metric_code: string;
  metric_name: string;
  metric_version: string | null;
  alias: string;
  alias_type: string;
  priority: number;
  match_length: number;
}

export interface QueryAnswer {
  answerable: boolean;
  answer: string;
  intent: string;
  engine: string;
  sql: string | null;
  columns: QueryColumn[];
  rows: (unknown[] | Record<string, unknown>)[];
  row_count: number;
  chart: QueryChart;
  explanation: string;
  assumptions: string[];
  provenance?: Provenance | null;
  candidates?: QueryCandidate[];
  metric_card?: QueryMetricCard;
  masked_columns: string[];
  warnings: string[];
  suggestions: string[];
}

export interface ReportSectionData {
  items?: KpiItem[];
  metrics?: ReportMetric[];
  assumptions?: string[];
  ontology_version?: string;
  knowledge_sources?: KnowledgeSource[];
  knowledge_version?: string | null;
}

export interface ReportMetric {
  code: string;
  name: string;
  formula: string;
  sql: string;
  /** 知识包提供的依据与来源（旧响应可能缺失）。 */
  basis?: string;
  source_ref?: string | null;
  source_name?: string | null;
  definition?: string;
  formula_text?: string;
  boundary?: Record<string, string>;
}

export interface ReportTableColumn {
  key: string;
  label: string;
}

export interface ReportTable {
  columns: ReportTableColumn[];
  rows: Record<string, unknown>[];
}

export interface ReportChart {
  type: "line" | "bar" | "pie";
  x?: string;
  labels?: string[];
  series: { name: string; data: number[] }[];
}

export interface ReportSection {
  key: string;
  title: string;
  type: "kpi" | "table" | "chart" | "text" | "appendix";
  data?: ReportSectionData;
  table?: ReportTable;
  chart?: ReportChart;
  text?: string;
}

export interface ReportAppendix {
  metrics: ReportMetric[];
  assumptions: string[];
  ontology_version?: string;
  knowledge_sources?: KnowledgeSource[];
  knowledge_version?: string | null;
}

export interface ReportDocument {
  template_code: string;
  title: string;
  period: { code: string; label: string; comparison: string | null };
  generated_at: string;
  sections: ReportSection[];
  reconciliation: Reconciliation;
  appendix: ReportAppendix;
  meta: { role: string; masked_columns: string[]; duration_ms: number };
  warnings?: string[];
}

export interface ReportTemplate {
  code: string;
  name: string;
  description: string;
  sections: { key: string; title: string; type: string }[];
}

export interface ReportTemplatesResponse {
  items: ReportTemplate[];
  periods?: { code: string; label: string }[];
}

export interface SecurityRole {
  code: string;
  name: string;
  description: string;
  permissions: string[];
  masking: string;
}

export interface SensitiveColumnPolicy {
  table: string;
  column: string;
  classification: string;
  description: string;
  masking: Record<string, string>;
}

export interface SecurityPolicy {
  sensitive_columns: SensitiveColumnPolicy[];
  audit_fields: string[];
}

export interface AuditItem {
  audit_id: number;
  question: string;
  intent: string | null;
  role: string;
  sql_preview: string | null;
  row_count: number;
  status: string;
  masked_columns: string[];
  duration_ms: number;
  created_at: string;
}

export interface AuditResponse {
  items: AuditItem[];
  total: number;
}

export interface TermListResponse {
  total: number;
  items: Term[];
}

export interface RelationListResponse {
  items: Relation[];
}

export interface TableListResponse {
  items: TableSummary[];
}

export interface SourceListResponse {
  items: SourceItem[];
}

export interface SecurityRoleResponse {
  items: SecurityRole[];
}

// ===== 治理：同步监控（tech-design §3.7.1） =====
export type SyncRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "dead_letter"
  | "interrupted";

export interface SyncRunTable {
  id: number;
  run_id: string;
  source_id: string;
  namespace: string;
  table_name: string;
  staging_name: string | null;
  status: string;
  sync_mode: string;
  rows_read: number;
  rows_written: number;
  rows_skipped: number;
  bytes_written: number;
  truncated: boolean;
  duration_ms: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface SyncRun {
  run_id: string;
  batch_id: string | null;
  source_id: string;
  source_name: string;
  source_kind: string;
  trigger_type: string;
  trigger_role: string | null;
  status: SyncRunStatus | string;
  attempts: number;
  retry_count: number;
  tables_total: number;
  tables_succeeded: number;
  tables_failed: number;
  rows_read: number;
  rows_written: number;
  rows_skipped: number;
  bytes_written: number;
  truncated: boolean;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  created_at: string;
  updated_at: string;
  tables?: SyncRunTable[];
}

export interface SyncStartResult {
  run_id: string;
  source_id: string;
  status: string;
  created_at: string;
}

export interface SyncBatchRun {
  source_id: string;
  run_id: string;
  status: string;
}

export interface SyncBatchSkipped {
  source_id: string;
  reason: string;
  run_id: string | null;
}

export interface SyncBatchResult {
  batch_id: string;
  runs: SyncBatchRun[];
  skipped: SyncBatchSkipped[];
}

export interface SyncSourceStatus {
  source_id: string;
  name: string;
  kind: string;
  sync_status: string;
  last_run_id: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  last_success_at: string | null;
  tables_total: number;
  rows_total: number;
}

export interface SyncSourcesResponse {
  items: SyncSourceStatus[];
  last_batch_at: string | null;
}

export interface SyncRunListResponse {
  items: SyncRun[];
  total: number;
}

// ===== 治理：元数据目录（tech-design §3.7.2） =====
export interface CatalogColumn {
  id: number;
  table_id: number;
  column_name: string;
  ordinal: number;
  display_name: string;
  description: string;
  data_type: string;
  semantic_type: string;
  is_nullable: number | boolean;
  is_primary_key: number | boolean;
  is_foreign_key: number | boolean;
  sensitivity_level: string;
  status: string;
  ontology_column_id: number | null;
  updated_by: string | null;
  updated_at: string;
  created_at: string;
}

export interface CatalogTable {
  id: number;
  source_id: string;
  namespace: string;
  table_name: string;
  staging_name: string | null;
  display_name: string;
  description: string;
  owner: string;
  business_domain: string;
  sensitivity_level: string;
  status: string;
  row_count: number;
  last_sync_at: string | null;
  watermark_column: string | null;
  sync_mode: string;
  ontology_term_id: number | null;
  source_kind: string;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
  source_name: string;
  claimed: boolean;
  column_count?: number;
}

export interface CatalogTableDetail extends CatalogTable {
  columns: CatalogColumn[];
  column_count: number;
  staging_available: boolean;
  staging_status: string | null;
  last_success_at: string | null;
}

export interface CatalogTableListResponse {
  items: CatalogTable[];
  total: number;
  page: number;
  page_size: number;
}

export interface CatalogChange {
  id: number;
  entity_type: string;
  entity_id: number;
  source_id: string;
  table_id: number | null;
  column_id: number | null;
  field_name: string;
  old_value: string | null;
  new_value: string | null;
  changed_by_role: string;
  created_at: string;
}

export interface CatalogChangesResponse {
  items: CatalogChange[];
  total: number;
}

export interface CatalogPreview {
  table_id: number;
  columns: string[];
  rows: Record<string, unknown>[];
  masked_columns: string[];
  sensitive_columns: string[];
  limit: number;
}

export interface CatalogSummary {
  tables_total: number;
  tables_pending: number;
  tables_missing: number;
  tables_unclaimed: number;
  columns_total: number;
  columns_pending: number;
  sources_total: number;
  sources_synced: number;
  last_sync_at: string | null;
  last_catalog_change_at: string | null;
}

export interface CatalogTableUpdateBody {
  display_name?: string;
  description?: string;
  owner?: string;
  business_domain?: string;
  sensitivity_level?: "public" | "internal" | "confidential" | "restricted";
  watermark_column?: string | null;
}

export interface CatalogColumnUpdateBody {
  display_name?: string;
  description?: string;
  semantic_type?: string;
  sensitivity_level?: "public" | "internal" | "confidential" | "restricted";
}

// ===== 指标注册表（tech-design §7.1） =====
export type MetricStatus = "draft" | "pending" | "certified" | "deprecated";
export type MetricAliasType = "synonym" | "business" | "exclusion";

export interface MetricDefinition {
  code: string;
  name: string;
  definition: string;
  formula_text: string;
  formula_sql: string;
  boundary: Record<string, string>;
  owner: string;
  business_domain: string;
  unit: string;
  sensitivity_level: string;
  status: MetricStatus | string;
  current_version: string | null;
  term_code: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
  test_total?: number;
  test_passed?: number;
  test_pass_rate?: number | null;
  /** 知识包提供的准则/管理依据（旧响应可能缺失）。 */
  basis?: string;
  source_ref?: string | null;
  source_name?: string | null;
  source_url?: string | null;
  knowledge_version?: string | null;
}

export interface MetricVersion {
  id: number;
  metric_code: string;
  version: string;
  status: string;
  snapshot: Record<string, unknown>;
  change_note: string;
  effective_period: string | null;
  certified_by: string | null;
  certified_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface MetricTestCase {
  id: number;
  metric_code: string;
  version_id: number | null;
  name: string;
  period_code: string;
  expected_value: number;
  tolerance_abs: number;
  tolerance_rel: number;
  last_status: string;
  last_actual: number | null;
  last_diff: number | null;
  last_run_at: string | null;
  last_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface MetricAlias {
  id: number;
  metric_code: string;
  alias: string;
  normalized_alias: string;
  alias_type: MetricAliasType | string;
  priority: number;
  status: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface MetricTestSummary {
  total: number;
  passed: number;
  failed: number;
  error: number;
  not_run: number;
  pass_rate: number | null;
}

export interface MetricDetail extends MetricDefinition {
  versions: MetricVersion[];
  test_cases: MetricTestCase[];
  aliases: MetricAlias[];
  test_summary: MetricTestSummary;
  current_certified: MetricVersion | null;
  draft_version: MetricVersion | null;
}

export interface MetricSummary {
  total: number;
  certified: number;
  draft: number;
  pending: number;
  deprecated: number;
  tests_total: number;
  tests_passed: number;
  test_pass_rate: number | null;
}

export interface MetricListResponse {
  items: MetricDefinition[];
  total: number;
  page: number;
  page_size: number;
}

export interface MetricCreateBody {
  code: string;
  name: string;
  definition?: string;
  formula_text?: string;
  formula_sql?: string;
  boundary?: Record<string, string>;
  owner?: string;
  business_domain?: string;
  unit?: string;
  sensitivity_level?: "public" | "internal" | "confidential" | "restricted";
  term_code?: string | null;
}

export type MetricUpdateBody = Partial<Omit<MetricCreateBody, "code">>;

export interface MetricAliasBody {
  alias: string;
  alias_type: MetricAliasType;
  priority?: number;
  status?: "active" | "disabled";
}

// ===== 质量规则（tech-design §7.2） =====
export type QualityRuleType =
  | "reconciliation"
  | "completeness"
  | "uniqueness"
  | "range"
  | "custom_sql";

export interface QualityRule {
  id: number;
  code: string;
  name: string;
  rule_type: QualityRuleType | string;
  target_db: "business" | "staging" | string;
  target_ref: string;
  expression: Record<string, unknown>;
  expression_json: string;
  tolerance: number;
  severity: "info" | "warning" | "blocker" | string;
  enabled: boolean;
  schedule: "manual" | "sync" | "report" | "sync_report" | string;
  owner: string;
  description: string;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
}

export interface QualityResult {
  id: number;
  run_id: string;
  rule_code: string;
  rule_name: string;
  rule_type: string;
  target_db: string;
  severity: string;
  status: "passed" | "failed" | "skipped" | "error" | string;
  left_value: number | null;
  right_value: number | null;
  diff: number | null;
  tolerance: number | null;
  detail: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
  run?: {
    run_id: string;
    trigger_type: string;
    period_code: string | null;
    started_at: string;
  };
}

export interface QualityRun {
  run_id: string;
  trigger_type: string;
  trigger_role: string | null;
  period_code: string | null;
  source_id: string | null;
  ingest_run_id: string | null;
  status: "running" | "succeeded" | "partial" | "failed" | string;
  rules_total: number;
  rules_passed: number;
  rules_failed: number;
  rules_skipped: number;
  blocker_failed: number;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  results?: QualityResult[];
}

export interface QualitySummary {
  rules_total: number;
  rules_enabled: number;
  pass_rate_7d: number | null;
  runs_7d: number;
  failed_results_7d: number;
  blocker_failed_7d: number;
  last_run_at: string | null;
  last_run_status: string | null;
}

export interface QualityRuleListResponse {
  items: QualityRule[];
}

export interface QualityRunListResponse {
  items: QualityRun[];
  total: number;
}

export interface QualityAlertListResponse {
  items: QualityResult[];
}

export interface QualityRuleBody {
  code?: string;
  name?: string;
  rule_type?: QualityRuleType;
  target_db?: "business" | "staging";
  target_ref?: string;
  expression?: Record<string, unknown>;
  tolerance?: number;
  severity?: "info" | "warning" | "blocker";
  enabled?: boolean;
  schedule?: "manual" | "sync" | "report" | "sync_report";
  owner?: string;
  description?: string;
}

// ===== 报表包（tech-design §7.3） =====
export interface ReportPackageTemplate {
  code: string;
  name: string;
  description: string;
  sections: { key: string; title: string; type: string }[];
}

export interface ReportPackageTemplatesResponse {
  items: ReportPackageTemplate[];
  periods: { code: string; label: string }[];
}

export interface ReportPackageQuality {
  run_id: string | null;
  status: string;
  rules_total: number;
  rules_passed: number;
  rules_failed: number;
  rules_skipped: number;
  blocker_failed: number;
  results: QualityResult[];
}

export interface ReportPackageAppendixMetric {
  code: string;
  name: string;
  definition: string;
  formula_text: string;
  formula_sql: string;
  boundary: Record<string, string>;
  owner: string;
  unit: string;
  version: string | null;
  certified_by: string | null;
  certified_at: string | null;
}

export interface ReportPackageAppendix {
  metrics: ReportPackageAppendixMetric[];
  assumptions: string[];
  ontology_version?: string;
}

export interface ReportPackageTemplateDocument {
  code: string;
  name: string;
  sections: ReportSection[];
}

export interface ReportPackageDocument {
  package_code: string;
  title: string;
  period: { code: string; label: string; comparison: string | null };
  generated_at: string;
  templates: ReportPackageTemplateDocument[];
  quality: ReportPackageQuality;
  reconciliation: Reconciliation;
  appendix: ReportPackageAppendix;
  warnings: string[];
  meta: {
    role: string;
    masked_columns: string[];
    duration_ms: number;
    ontology_version: string;
    engine: string;
  };
}

export interface ReportPackageRequestBody {
  period: string;
  templates?: string[];
  comparison?: "yoy" | "mom" | "none";
}

// ===== 外部 auto-ontology 绑定 =====
export interface AutoOntologyServiceState {
  configured: boolean;
  enabled: boolean;
  status: "unconfigured" | "disabled" | "healthy" | "error";
  base_url: string | null;
  auth_type: "bearer";
  auth_header: string;
  probe_path: string;
  verify_tls: boolean;
  timeout_seconds: number;
  has_token: boolean;
  last_tested_at: string | null;
  last_error: string | null;
  last_latency_ms: number | null;
  last_status_code: number | null;
  updated_at: string | null;
}

export interface AutoOntologyServiceBody {
  base_url: string;
  auth_header: string;
  token?: string;
  clear_token?: boolean;
  enabled: boolean;
  verify_tls: boolean;
  probe_path: string;
  timeout_seconds: number;
}

export interface AutoOntologyTestBody {
  base_url?: string;
  auth_header?: string;
  token?: string;
  probe_path?: string;
  verify_tls?: boolean;
  timeout_seconds?: number;
}

export interface AutoOntologyTestSummary {
  content_type: string;
  bytes_read: number;
  truncated: boolean;
  json_type: "object" | "array" | "other";
  item_count: number | null;
}

export interface AutoOntologyTestResult {
  success: boolean;
  latency_ms: number;
  status_code: number;
  persisted: boolean;
  message: string;
  summary: AutoOntologyTestSummary;
}

// ===== 行业知识包（knowledge pack） =====
export type KnowledgeKind =
  | "term"
  | "process"
  | "metric_basis"
  | "reconciliation_rule"
  | "terminology"
  | "correction";

export type KnowledgeConfidence = "standard" | "opensource" | "practice";

export type KnowledgeSourceType = "standard" | "opensource" | "practice";

export type KnowledgeDomain =
  | "accounting_reconciliation"
  | "business_finance_integration";

export interface KnowledgeDomainCount {
  domain: string;
  entries: number;
}

export interface KnowledgeKindCount {
  kind: string;
  entries: number;
}

export interface KnowledgeCoverage {
  ontology_terms_covered: number;
  metrics_covered: number;
  reconciliation_rules: number;
  terminology_phrases: number;
}

export interface KnowledgeSummary {
  available: boolean;
  pack_version: string;
  entries: number;
  sources: number;
  loaded_at: string | null;
  last_error: string | null;
  domains: KnowledgeDomainCount[];
  kinds: KnowledgeKindCount[];
  coverage: KnowledgeCoverage;
}

export interface KnowledgeSource {
  source_id: string;
  name: string;
  type: KnowledgeSourceType | string;
  license: string;
  url: string;
  notice: string;
  version: string;
  entry_count: number;
}

export interface KnowledgeEntry {
  entry_id: string;
  kind: KnowledgeKind | string;
  domain: KnowledgeDomain | string;
  title: string;
  statement: string;
  basis: string;
  source_id: string;
  source_name: string;
  source_type: KnowledgeSourceType | string;
  version: string;
  effective_from: string;
  confidence: KnowledgeConfidence | string;
}

export interface KnowledgeLink {
  target_kind: string;
  target_code: string;
}

export interface KnowledgeEntryDetail extends KnowledgeEntry {
  payload: Record<string, unknown>;
  links: KnowledgeLink[];
  source: KnowledgeSource;
}

export interface KnowledgeListResponse {
  items: KnowledgeEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface KnowledgeSourcesResponse {
  items: KnowledgeSource[];
}

export interface KnowledgeReloadResponse {
  reloaded: boolean;
  pack_version: string;
  entries: number;
  sources: number;
  /** 领域数（整数），不是领域分布数组。 */
  domains: number;
  /** 本次加载回收的孤儿条目数（JSON 中已删除的条目）。 */
  removed?: number;
  duration_ms: number;
  loaded_at: string | null;
}

export interface KnowledgeViolation {
  file: string;
  entry_id: string;
  field: string;
  message: string;
}

