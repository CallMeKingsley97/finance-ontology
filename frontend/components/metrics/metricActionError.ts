import { ApiError, errorMessage } from "@/lib/api";

/** 指标操作统一错误文案；`METRIC_TEST_FAILED` 追加失败用例名。 */
export function actionErrorText(error: unknown): { message: string; details?: unknown } {
  if (error instanceof ApiError) {
    let message = error.message;
    if (error.code === "METRIC_TEST_FAILED") {
      const details = error.details as { failed?: unknown[] } | undefined;
      const failed = Array.isArray(details?.failed) ? details.failed : [];
      const names = failed
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const record = item as { name?: unknown; message?: unknown };
            return String(record.name ?? record.message ?? "");
          }
          return "";
        })
        .filter(Boolean);
      if (names.length) message += `：${names.join("、")}`;
    }
    return { message, details: error.details };
  }
  return { message: errorMessage(error) };
}
