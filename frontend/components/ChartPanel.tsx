"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const COLORS = ["#0f172a", "#2563eb", "#0d9488", "#d97706", "#7c3aed", "#e11d48", "#64748b", "#0891b2"];

function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(1)}亿`;
  if (abs >= 10_000) return `${(value / 10_000).toFixed(0)}万`;
  return String(Math.round(value * 100) / 100);
}

export default function ChartPanel({
  type,
  labels = [],
  series = [],
  height = 280,
  emptyText = "暂无可视化数据",
}: {
  type?: string;
  labels?: string[];
  series?: { name: string; data: number[] }[];
  height?: number;
  emptyText?: string;
}) {
  const usableSeries = (series ?? []).filter((item) => item && Array.isArray(item.data));
  const chartType = !type || type === "none" ? "none" : type;

  if (chartType === "none" || labels.length === 0 || usableSeries.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 text-xs text-slate-500"
        style={{ height }}
      >
        {emptyText}
      </div>
    );
  }

  if (chartType === "pie") {
    const pieData = labels.map((name, index) => ({
      name,
      value: Number(usableSeries[0].data[index] ?? 0),
    }));
    return (
      <div style={{ width: "100%", height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={pieData} dataKey="value" nameKey="name" outerRadius="72%" innerRadius="38%">
              {pieData.map((entry, index) => (
                <Cell key={`${entry.name}-${index}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 11 }} />
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const chartData = labels.map((label, index) => {
    const row: Record<string, string | number> = { label };
    usableSeries.forEach((item) => {
      row[item.name] = Number(item.data[index] ?? 0);
    });
    return row;
  });

  if (chartType === "bar") {
    return (
      <div style={{ width: "100%", height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} stroke="#94a3b8" />
            <YAxis tickFormatter={compact} tick={{ fontSize: 10 }} stroke="#94a3b8" width={64} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {usableSeries.map((item, index) => (
              <Bar key={item.name} dataKey={item.name} fill={COLORS[index % COLORS.length]} radius={[4, 4, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} stroke="#94a3b8" />
          <YAxis tickFormatter={compact} tick={{ fontSize: 10 }} stroke="#94a3b8" width={64} />
          <Tooltip />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {usableSeries.map((item, index) => (
            <Line
              key={item.name}
              type="monotone"
              dataKey={item.name}
              stroke={COLORS[index % COLORS.length]}
              strokeWidth={2}
              dot={{ r: 2 }}
              activeDot={{ r: 4 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
