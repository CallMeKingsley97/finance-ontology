"use client";

import type { ReactNode } from "react";

import { formatCell } from "@/lib/format";

import SensitiveBadge from "./SensitiveBadge";

export interface DataTableColumn {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
  render?: (row: Record<string, unknown>, value: unknown) => ReactNode;
}

export default function DataTable({
  columns,
  rows,
  emptyText = "暂无数据",
  sensitiveColumns = [],
  maxHeight,
  onRowClick,
  rowClassName,
}: {
  columns: DataTableColumn[];
  rows: Record<string, unknown>[];
  emptyText?: string;
  sensitiveColumns?: string[];
  maxHeight?: number;
  onRowClick?: (row: Record<string, unknown>, index: number) => void;
  rowClassName?: (row: Record<string, unknown>, index: number) => string;
}) {
  if (!rows || rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-xs text-slate-500">
        {emptyText}
      </div>
    );
  }

  const alignClass = (align?: string) =>
    align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left";

  return (
    <div
      className="table-scroll rounded-lg border border-slate-200"
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table className="min-w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-slate-50 text-slate-600">
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`whitespace-nowrap border-b border-slate-200 px-3 py-2 font-medium ${alignClass(column.align)}`}
              >
                <span className="inline-flex items-center gap-1">
                  {column.label}
                  {sensitiveColumns.includes(column.key) ? <SensitiveBadge /> : null}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              onClick={onRowClick ? () => onRowClick(row, rowIndex) : undefined}
              className={[
                "odd:bg-white even:bg-slate-50/60 hover:bg-slate-100/60",
                onRowClick ? "cursor-pointer" : "",
                rowClassName ? rowClassName(row, rowIndex) : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              {columns.map((column) => {
                const value = row[column.key];
                const rendered = column.render ? column.render(row, value) : formatCell(value);
                return (
                  <td
                    key={column.key}
                    className={`border-b border-slate-100 px-3 py-2 align-top text-slate-700 ${alignClass(column.align)}`}
                  >
                    {rendered}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
