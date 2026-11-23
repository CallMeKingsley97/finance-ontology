import { ApiError, getRole } from "./api";
import type { ErrorEnvelope, ReportPackageRequestBody } from "./types";

/**
 * 报表包 Excel 下载：二进制响应不能用 apiFetch 解析 JSON。
 * 非 2xx 时解析错误信封并抛出 ApiError，成功则触发浏览器下载。
 */
export async function downloadReportPackage(body: ReportPackageRequestBody): Promise<void> {
  const response = await fetch("/api/report-packages/export", {
    method: "POST",
    headers: {
      "X-Demo-Role": getRole(),
      "Content-Type": "application/json",
      Accept:
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    let envelope: Partial<ErrorEnvelope> = {};
    try {
      envelope = (await response.json()) as Partial<ErrorEnvelope>;
    } catch {
      envelope = {};
    }
    throw new ApiError(
      response.status,
      envelope.code ?? "HTTP_ERROR",
      envelope.message ?? `下载失败（HTTP ${response.status}）`,
      envelope.details,
    );
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const filename = match?.[1] || `report-package-${body.period}.xlsx`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
