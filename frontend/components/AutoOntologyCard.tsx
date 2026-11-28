"use client";

import { Check, Loader2, Play, RefreshCw, Settings2, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import ErrorAlert from "@/components/ErrorAlert";
import SectionCard from "@/components/SectionCard";
import StatusBadge from "@/components/StatusBadge";
import {
  clearOntologyService,
  errorMessage,
  ontologyService,
  saveOntologyService,
  testOntologyService,
} from "@/lib/api";
import { formatDate, formatDuration } from "@/lib/format";
import type {
  AutoOntologyServiceBody,
  AutoOntologyServiceState,
  AutoOntologyTestBody,
  AutoOntologyTestResult,
  Role,
} from "@/lib/types";

const DEFAULT_AUTH_HEADER = "Authorization";
const DEFAULT_PROBE_PATH = "/api/terms?limit=1";
const DEFAULT_TIMEOUT_SECONDS = "5";

const STATUS_LABELS: Record<string, string> = {
  unconfigured: "未绑定",
  disabled: "已停用",
  healthy: "正常",
  error: "异常",
};

const TEST_MESSAGE_CLASSES: Record<"success" | "error", string> = {
  success: "border-emerald-200 bg-emerald-50 text-emerald-700",
  error: "border-rose-200 bg-rose-50 text-rose-700",
};

interface FormState {
  base_url: string;
  auth_header: string;
  token: string;
  clear_token: boolean;
  probe_path: string;
  timeout_seconds: string;
  verify_tls: boolean;
  enabled: boolean;
}

function formFromState(state: AutoOntologyServiceState | null): FormState {
  return {
    base_url: state?.base_url ?? "",
    auth_header: state?.auth_header ?? DEFAULT_AUTH_HEADER,
    token: "",
    clear_token: false,
    probe_path: state?.probe_path ?? DEFAULT_PROBE_PATH,
    timeout_seconds: String(state?.timeout_seconds ?? DEFAULT_TIMEOUT_SECONDS),
    verify_tls: state?.verify_tls ?? true,
    enabled: state?.configured ? state.enabled : true,
  };
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className={`mt-0.5 break-all text-slate-800 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

const INPUT_CLASS =
  "mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-slate-500";

export default function AutoOntologyCard({ role }: { role: Role }) {
  const isAdmin = role === "admin";

  const [state, setState] = useState<AutoOntologyServiceState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testMessage, setTestMessage] = useState<string | null>(null);
  const [testMessageTone, setTestMessageTone] = useState<"success" | "error">("success");

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(formFromState(null));
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<AutoOntologyTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const [clearOpen, setClearOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setState(await ontologyService());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!isAdmin) setOpen(false);
  }, [isAdmin]);

  const openConfig = () => {
    setForm(formFromState(state));
    setFormError(null);
    setTestResult(null);
    setTestError(null);
    setClearError(null);
    setOpen(true);
  };

  const buildTestBody = (): AutoOntologyTestBody => {
    const body: AutoOntologyTestBody = {
      base_url: form.base_url.trim(),
      auth_header: form.auth_header.trim() || DEFAULT_AUTH_HEADER,
      probe_path: form.probe_path.trim() || DEFAULT_PROBE_PATH,
      verify_tls: form.verify_tls,
    };
    const timeout = Number(form.timeout_seconds);
    if (Number.isFinite(timeout) && timeout > 0) body.timeout_seconds = timeout;
    if (form.token.trim()) body.token = form.token.trim();
    return body;
  };

  const handleSavedTest = async () => {
    setTesting(true);
    setError(null);
    setTestMessage(null);
    try {
      const result = await testOntologyService({});
      setTestMessageTone("success");
      setTestMessage(
        `${result.message || "连接正常"} · ${formatDuration(result.latency_ms)} · HTTP ${result.status_code}`,
      );
      await load();
    } catch (err) {
      await load();
      setError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  };

  const handleFormTest = async () => {
    if (!form.base_url.trim()) {
      setFormError("请填写服务地址");
      return;
    }
    setTesting(true);
    setFormError(null);
    setTestError(null);
    setTestResult(null);
    try {
      setTestResult(await testOntologyService(buildTestBody()));
    } catch (err) {
      setTestError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    const baseUrl = form.base_url.trim();
    if (!baseUrl) {
      setFormError("请填写服务地址");
      return;
    }
    const timeout = Number(form.timeout_seconds);
    if (!Number.isFinite(timeout) || timeout <= 0) {
      setFormError("超时时间必须大于 0");
      return;
    }
    const body: AutoOntologyServiceBody = {
      base_url: baseUrl,
      auth_header: form.auth_header.trim() || DEFAULT_AUTH_HEADER,
      probe_path: form.probe_path.trim() || DEFAULT_PROBE_PATH,
      timeout_seconds: timeout,
      verify_tls: form.verify_tls,
      enabled: form.enabled,
      clear_token: form.clear_token,
    };
    const token = form.token.trim();
    if (token) {
      body.token = token;
      body.clear_token = false;
    }
    setSaving(true);
    setFormError(null);
    try {
      const next = await saveOntologyService(body);
      setState(next);
      if (next.status === "healthy") {
        setTestMessageTone("success");
        setTestMessage("配置已保存，连接正常");
      } else if (next.status === "error") {
        setTestMessageTone("error");
        setTestMessage("配置已保存，但连接测试失败");
      } else {
        setTestMessageTone("success");
        setTestMessage("配置已保存");
      }
      setOpen(false);
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setClearing(true);
    setClearError(null);
    try {
      await clearOntologyService();
      setClearOpen(false);
      setOpen(false);
      setTestResult(null);
      setTestMessage(null);
      setTestMessageTone("success");
      await load();
    } catch (err) {
      setClearError(errorMessage(err));
    } finally {
      setClearing(false);
    }
  };

  const status = state?.status ?? "unconfigured";
  const statusLabel = STATUS_LABELS[status] ?? status;
  const busy = testing || saving;

  return (
    <>
      <SectionCard
        title="外部本体服务"
        description="绑定已部署的 auto-ontology 服务；关闭或不可达不影响本地规则本体。"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={status} label={statusLabel} />
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw size={12} className={loading ? "animate-spin" : ""} aria-hidden />
              刷新
            </button>
            {isAdmin ? (
              <button
                type="button"
                onClick={() => void handleSavedTest()}
                disabled={testing || !state?.configured}
                title={!state?.configured ? "请先保存绑定配置" : undefined}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
              >
                {testing ? (
                  <Loader2 size={12} className="animate-spin" aria-hidden />
                ) : (
                  <Play size={12} aria-hidden />
                )}
                测试连接
              </button>
            ) : null}
            {isAdmin ? (
              <button
                type="button"
                onClick={openConfig}
                className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-800"
              >
                <Settings2 size={12} aria-hidden />
                配置
              </button>
            ) : null}
          </div>
        }
      >
        {loading && !state ? <div className="text-xs text-slate-500">正在读取绑定状态…</div> : null}
        {error ? <ErrorAlert message={error} onRetry={() => void load()} /> : null}

        {state ? (
          <div className="space-y-3">
            {state.configured ? (
              <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
                <Info label="服务地址" value={state.base_url ?? "—"} mono />
                <Info label="认证 Header" value={`${state.auth_header} · Bearer`} mono />
                <Info label="探测路径" value={state.probe_path} mono />
                <Info label="令牌" value={state.has_token ? "已设置（加密保存）" : "未设置"} />
                <Info label="最近测试" value={formatDate(state.last_tested_at)} />
                <Info label="延迟" value={formatDuration(state.last_latency_ms)} />
                <Info
                  label="上游状态码"
                  value={state.last_status_code === null ? "—" : String(state.last_status_code)}
                />
                <Info label="TLS 校验" value={state.verify_tls ? "开启" : "关闭"} />
              </dl>
            ) : (
              <p className="text-xs leading-relaxed text-slate-500">
                尚未绑定外部本体服务，本地规则本体生成与问数不受影响。
                {isAdmin ? "点击右上角“配置”填写地址、令牌并测试连接。" : "仅 admin 可配置。"}
              </p>
            )}

            {state.status === "disabled" ? (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                绑定已停用，不会主动探测上游；本地本体与问数保持正常。
              </div>
            ) : null}

            {state.last_error ? (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                最近错误：{state.last_error}
              </div>
            ) : null}

            {testMessage ? (
              <div
                className={`rounded-lg border px-3 py-2 text-xs ${TEST_MESSAGE_CLASSES[testMessageTone]}`}
              >
                {testMessage}
              </div>
            ) : null}

            {!isAdmin ? (
              <div className="text-[11px] text-slate-500">
                当前角色为 {role}，绑定配置、连接测试与清除仅 admin 可用。
              </div>
            ) : null}
          </div>
        ) : null}
      </SectionCard>

      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
          role="presentation"
        >
          <div
            className="card max-h-[92vh] w-full max-w-2xl overflow-auto p-5"
            role="dialog"
            aria-modal="true"
            aria-labelledby="auto-ontology-config-title"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="auto-ontology-config-title" className="text-base font-semibold text-slate-900">
                  配置外部本体服务
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  保存时若启用绑定，会先执行一次探测，再一次性写入配置与测试结果。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                aria-label="关闭"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-slate-600 sm:col-span-2">
                服务地址
                <input
                  value={form.base_url}
                  onChange={(event) => setForm((prev) => ({ ...prev, base_url: event.target.value }))}
                  placeholder="http://auto-ontology-frontend:3000"
                  className={`${INPUT_CLASS} font-mono`}
                />
              </label>
              <label className="text-xs text-slate-600">
                认证 Header 名
                <input
                  value={form.auth_header}
                  onChange={(event) => setForm((prev) => ({ ...prev, auth_header: event.target.value }))}
                  placeholder={DEFAULT_AUTH_HEADER}
                  className={`${INPUT_CLASS} font-mono`}
                />
              </label>
              <label className="text-xs text-slate-600">
                探测路径
                <input
                  value={form.probe_path}
                  onChange={(event) => setForm((prev) => ({ ...prev, probe_path: event.target.value }))}
                  placeholder={DEFAULT_PROBE_PATH}
                  className={`${INPUT_CLASS} font-mono`}
                />
              </label>
              <label className="text-xs text-slate-600 sm:col-span-2">
                令牌
                <input
                  type="password"
                  autoComplete="new-password"
                  value={form.token}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      token: event.target.value,
                      clear_token: event.target.value ? false : prev.clear_token,
                    }))
                  }
                  placeholder={
                    state?.has_token ? "已保存令牌，留空保持不变" : "未设置令牌（上游无鉴权可留空）"
                  }
                  className={INPUT_CLASS}
                />
              </label>
              <label className="text-xs text-slate-600">
                超时秒数
                <input
                  type="number"
                  min="0.1"
                  max="10"
                  step="0.5"
                  value={form.timeout_seconds}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, timeout_seconds: event.target.value }))
                  }
                  className={INPUT_CLASS}
                />
              </label>
              <div className="flex flex-col justify-end gap-2 text-xs text-slate-600">
                <label className="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.verify_tls}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, verify_tls: event.target.checked }))
                    }
                  />
                  校验上游 TLS 证书（内网自签名可关闭）
                </label>
                <label className="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, enabled: event.target.checked }))
                    }
                  />
                  启用绑定（保存时立即探测）
                </label>
                {state?.has_token ? (
                  <label className="inline-flex items-center gap-2 text-rose-700">
                    <input
                      type="checkbox"
                      checked={form.clear_token}
                      onChange={(event) =>
                        setForm((prev) => ({
                          ...prev,
                          clear_token: event.target.checked,
                          token: event.target.checked ? "" : prev.token,
                        }))
                      }
                    />
                    清除已保存令牌
                  </label>
                ) : null}
              </div>
            </div>

            {formError ? (
              <div className="mt-3">
                <ErrorAlert message={formError} />
              </div>
            ) : null}
            {testError ? (
              <div className="mt-3">
                <ErrorAlert message={testError} />
              </div>
            ) : null}
            {testResult ? (
              <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                <div className="flex items-center gap-1 font-medium">
                  <Check size={13} aria-hidden />
                  {testResult.message || "连接正常"} · {formatDuration(testResult.latency_ms)} · HTTP{" "}
                  {testResult.status_code}
                </div>
                <div className="mt-1 text-emerald-600">
                  {testResult.persisted ? "结果已写入绑定状态" : "表单测试，未落库"} · 响应{" "}
                  {testResult.summary.bytes_read} B
                  {testResult.summary.truncated ? "（已截断）" : ""} · {testResult.summary.json_type}
                  {typeof testResult.summary.item_count === "number"
                    ? ` / ${testResult.summary.item_count} 项`
                    : ""}
                </div>
              </div>
            ) : null}
            {clearError ? (
              <div className="mt-3">
                <ErrorAlert message={clearError} />
              </div>
            ) : null}

            <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
              {state?.configured ? (
                <button
                  type="button"
                  onClick={() => {
                    setClearError(null);
                    setClearOpen(true);
                  }}
                  disabled={busy || clearing}
                  className="inline-flex items-center gap-1 rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"
                >
                  <Trash2 size={12} aria-hidden />
                  清除绑定
                </button>
              ) : (
                <span />
              )}
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  disabled={busy}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void handleFormTest()}
                  disabled={busy}
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {testing ? (
                    <Loader2 size={12} className="animate-spin" aria-hidden />
                  ) : (
                    <Play size={12} aria-hidden />
                  )}
                  测试连接
                </button>
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={busy}
                  className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  {saving ? <Loader2 size={12} className="animate-spin" aria-hidden /> : null}
                  保存
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <ConfirmDialog
        open={clearOpen}
        title="清除外部本体服务绑定"
        description="将删除已保存的服务地址、探测配置与加密令牌；本地规则本体与问数不受影响。"
        confirmText={clearing ? "清除中…" : "清除绑定"}
        danger
        loading={clearing}
        onConfirm={() => void handleClear()}
        onCancel={() => {
          if (!clearing) setClearOpen(false);
        }}
      />
    </>
  );
}
