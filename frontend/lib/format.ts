/** 金额、比率、日期等展示格式化。 */

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function formatNumber(value: unknown, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return n.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 金额：≥1 亿显示亿元，≥1 万显示万元，否则显示元。 */
export function formatCurrency(value: unknown, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const abs = Math.abs(n);
  if (abs >= 100_000_000) return `${(n / 100_000_000).toFixed(digits)} 亿元`;
  if (abs >= 10_000) return `${(n / 10_000).toFixed(digits)} 万元`;
  return `${n.toFixed(digits)} 元`;
}

export function formatWan(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return `${(n / 10_000).toFixed(2)} 万元`;
}

/** 小数比率 → 百分比字符串，0.123 → 12.3% */
export function formatPct(value: unknown, digits = 1): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** 倍数，1.234 → 1.23 倍 */
export function formatRatio(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return `${n.toFixed(2)} 倍`;
}

export function formatBytes(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function formatDate(value: unknown): string {
  if (!value) return "—";
  const text = String(value);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 表格单元格兜底展示。 */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : formatNumber(value, 2);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** 毫秒耗时：1.2 s / 320 ms；空值返回 "—"。 */
export function formatDuration(ms: unknown): string {
  const n = toNumber(ms);
  if (n === null || n < 0) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  if (n < 60_000) return `${(n / 1000).toFixed(1)} s`;
  const minutes = Math.floor(n / 60_000);
  const seconds = Math.round((n % 60_000) / 1000);
  return `${minutes} 分 ${seconds} 秒`;
}

/** ISO 时间戳 → 相对时间；超过 7 天回退为绝对时间，空值返回 "—"。 */
export function formatRelativeTime(value: unknown): string {
  if (!value) return "—";
  const text = String(value);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return "刚刚";
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 7 * 86_400) return `${Math.floor(seconds / 86_400)} 天前`;
  return formatDate(text);
}

export function trendGlyph(trend?: string | null): string {
  if (trend === "up") return "↑";
  if (trend === "down") return "↓";
  return "→";
}
