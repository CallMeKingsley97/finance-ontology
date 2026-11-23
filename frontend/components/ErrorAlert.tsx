"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

export default function ErrorAlert({
  message,
  details,
  onRetry,
}: {
  message: string;
  details?: unknown;
  onRetry?: () => void;
}) {
  return (
    <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
      <div className="flex items-start gap-2">
        <AlertTriangle size={17} className="mt-0.5 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="font-medium">请求失败</div>
          <div className="mt-1 break-words text-rose-700">{message}</div>
          {details ? (
            <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-rose-100/70 p-2 text-[11px] text-rose-700">
              {typeof details === "string" ? details : JSON.stringify(details, null, 2)}
            </pre>
          ) : null}
        </div>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-rose-300 bg-white px-2.5 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100"
          >
            <RefreshCw size={13} aria-hidden />
            重试
          </button>
        ) : null}
      </div>
    </div>
  );
}
