"use client";

import { Database, FileSpreadsheet, FileText, HardDrive, Loader2, Server, X } from "lucide-react";
import { useMemo, useState } from "react";

import {
  ApiError,
  createSource,
  errorMessage,
  testSourceById,
  uploadSource,
} from "@/lib/api";
import type { SourceItem, SourceKind, SourceTestResult } from "@/lib/types";

const KIND_OPTIONS: {
  kind: SourceKind;
  label: string;
  description: string;
  icon: typeof Database;
}[] = [
  { kind: "postgresql", label: "PostgreSQL", description: "连接外部只读 PostgreSQL", icon: Server },
  { kind: "mysql", label: "MySQL", description: "连接外部只读 MySQL", icon: Database },
  { kind: "csv", label: "CSV 文件", description: "浏览器上传 CSV", icon: FileText },
  { kind: "excel", label: "Excel 文件", description: "上传 .xlsx / .xlsm", icon: FileSpreadsheet },
  { kind: "sqlite", label: "SQLite 文件", description: "上传 .db / .sqlite", icon: HardDrive },
];

const FILE_ACCEPT: Record<string, string> = {
  csv: ".csv,text/csv",
  excel: ".xlsx,.xlsm",
  sqlite: ".db,.sqlite,.sqlite3",
};

function insertDetail(error: unknown): string {
  if (error instanceof ApiError && error.details && typeof error.details === "object") {
    const detail = (error.details as { error?: unknown }).error;
    if (typeof detail === "string" && detail) return `${error.message}：${detail}`;
  }
  return errorMessage(error);
}

export default function SourceWizard({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void | Promise<void>;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [kind, setKind] = useState<SourceKind>("postgresql");
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("5432");
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [schema, setSchema] = useState("");
  const [password, setpassword] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<SourceItem | null>(null);
  const [testResult, setTestResult] = useState<SourceTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const isFileKind = kind === "csv" || kind === "excel" || kind === "sqlite";
  const canSubmit = useMemo(() => {
    if (submitting) return false;
    if (isFileKind) return file !== null;
    return Boolean(
      host.trim() && database.trim() && username.trim() && password,
    );
  }, [database, file, host, isFileKind, submitting, username, password]);

  const reset = () => {
    setStep(1);
    setKind("postgresql");
    setName("");
    setHost("");
    setPort("5432");
    setDatabase("");
    setUsername("");
    setSchema("");
    setpassword("");
    setFile(null);
    setSubmitting(false);
    setError(null);
    setCreated(null);
    setTestResult(null);
    setTestError(null);
  };

  const close = () => {
    if (submitting) return;
    reset();
    onClose();
  };

  const chooseKind = (next: SourceKind) => {
    setKind(next);
    setPort(next === "mysql" ? "3306" : "5432");
    setFile(null);
    setStep(2);
  };

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    setTestResult(null);
    setTestError(null);
    try {
      let source: SourceItem;
      if (isFileKind) {
        const form = new FormData();
        form.append("name", name.trim());
        form.append("kind", kind);
        if (file) form.append("file", file);
        source = await uploadSource(form);
      } else {
        source = await createSource({
          name: name.trim() || `${kind} 数据源`,
          kind: kind === "mysql" ? "mysql" : "postgresql",
          config: {
            host: host.trim(),
            port: Number(port) || (kind === "mysql" ? 3306 : 5432),
            database: database.trim(),
            username: username.trim(),
            schema: schema.trim() || undefined,
          },
          secret: password,
        });
      }
      setCreated(source);
      setStep(3);
      await onCreated();
      try {
        setTestResult(await testSourceById(source.id));
      } catch (err) {
        setTestError(insertDetail(err));
      }
    } catch (err) {
      setError(insertDetail(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="presentation">
      <div className="card w-full max-w-2xl p-5" role="dialog" aria-modal="true" aria-labelledby="source-wizard-title">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="source-wizard-title" className="text-base font-semibold text-slate-900">
              新增数据源
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              首期仅接入与浏览元数据，不做数据物化与跨源查询。
            </p>
          </div>
          <button
            type="button"
            onClick={close}
            disabled={submitting}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>

        <div className="mt-4 flex items-center gap-2 text-[11px] text-slate-500">
          {["选择类型", "填写连接", "测试完成"].map((label, index) => (
            <span
              key={label}
              className={`rounded-full px-2 py-0.5 ${
                step === index + 1 ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-500"
              }`}
            >
              {index + 1}. {label}
            </span>
          ))}
        </div>

        {step === 1 ? (
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {KIND_OPTIONS.map((option) => {
              const Icon = option.icon;
              return (
                <button
                  key={option.kind}
                  type="button"
                  onClick={() => chooseKind(option.kind)}
                  className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3 text-left hover:border-slate-400 hover:bg-slate-50"
                >
                  <Icon size={18} className="mt-0.5 text-slate-500" aria-hidden />
                  <span>
                    <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                    <span className="mt-0.5 block text-xs text-slate-500">{option.description}</span>
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="mt-4 space-y-3">
            <label className="block text-xs text-slate-600">
              源名称
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={80}
                placeholder={isFileKind ? "留空时使用文件名" : "例如：生产 PostgreSQL"}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
              />
            </label>

            {isFileKind ? (
              <label className="block text-xs text-slate-600">
                文件（{kind === "excel" ? ".xlsx/.xlsm，≤20MB" : kind === "csv" ? ".csv，≤50MB" : ".db/.sqlite，≤100MB"}）
                <input
                  type="file"
                  accept={FILE_ACCEPT[kind]}
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
                />
              </label>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-xs text-slate-600">
                  主机
                  <input
                    value={host}
                    onChange={(event) => setHost(event.target.value)}
                    placeholder="pg.internal"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                </label>
                <label className="block text-xs text-slate-600">
                  端口
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={port}
                    onChange={(event) => setPort(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                </label>
                <label className="block text-xs text-slate-600">
                  数据库
                  <input
                    value={database}
                    onChange={(event) => setDatabase(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                </label>
                <label className="block text-xs text-slate-600">
                  用户名
                  <input
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                </label>
                <label className="block text-xs text-slate-600">
                  schema（可选）
                  <input
                    value={schema}
                    onChange={(event) => setSchema(event.target.value)}
                    placeholder={kind === "mysql" ? "默认同数据库名" : "public"}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                </label>
                <label className="block text-xs text-slate-600">
                  密码（仅保存在本次表单内存）
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setpassword(event.target.value)}
                    autoComplete="new-password"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                </label>
              </div>
            )}

            {error ? (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {error}
              </div>
            ) : null}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setStep(1)}
                disabled={submitting}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                上一步
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={!canSubmit}
                className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {submitting ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
                {submitting ? "提交并测试…" : "提交并测试"}
              </button>
            </div>
          </div>
        ) : null}

        {step === 3 && created ? (
          <div className="mt-4 space-y-3">
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
              <div className="font-medium text-slate-900">{created.name}</div>
              <div className="mt-1 text-xs text-slate-500">
                类型 {created.kind} · 状态 {created.status} · 表数 {created.table_count}
              </div>
            </div>
            {testResult ? (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                连接测试通过：{testResult.table_count} 张表，延迟 {testResult.latency_ms} ms
                {testResult.message ? ` · ${testResult.message}` : ""}
              </div>
            ) : null}
            {testError ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                源已创建，但连接测试失败：{testError}
              </div>
            ) : null}
            {!testResult && !testError ? (
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <Loader2 size={14} className="animate-spin" aria-hidden />
                正在自动测试连接…
              </div>
            ) : null}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={close}
                className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
              >
                完成
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
