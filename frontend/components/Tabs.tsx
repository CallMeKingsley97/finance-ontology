"use client";

import { useRef, type KeyboardEvent } from "react";

export interface TabItem {
  value: string;
  label: string;
  badge?: string | number;
  disabled?: boolean;
}

export function tabButtonId(idPrefix: string, value: string): string {
  return `${idPrefix}-tab-${value}`;
}

export function tabPanelId(idPrefix: string, value: string): string {
  return `${idPrefix}-panel-${value}`;
}

/**
 * 共享 Tab 组件：页面级与详情二级复用。
 * 调用方负责渲染 role="tabpanel" 容器，并使用 tabButtonId/tabPanelId 对齐 id。
 */
export default function Tabs({
  items,
  value,
  onChange,
  ariaLabel,
  idPrefix,
  size = "md",
  className = "",
}: {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  idPrefix: string;
  size?: "md" | "sm";
  className?: string;
}) {
  const buttonRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const focusTab = (next: string) => {
    onChange(next);
    requestAnimationFrame(() => buttonRefs.current[next]?.focus());
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: string) => {
    const enabled = items.filter((item) => !item.disabled);
    if (enabled.length === 0) return;
    const lastIndex = enabled.length - 1;
    const found = enabled.findIndex((item) => item.value === current);
    const currentIndex = found >= 0 ? found : 0;
    let nextIndex: number | null = null;
    switch (event.key) {
      case "ArrowRight":
        nextIndex = currentIndex >= lastIndex ? 0 : currentIndex + 1;
        break;
      case "ArrowLeft":
        nextIndex = currentIndex <= 0 ? lastIndex : currentIndex - 1;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = lastIndex;
        break;
      default:
        return;
    }
    event.preventDefault();
    const target = enabled[nextIndex];
    if (!target) return;
    if (target.value !== current) {
      focusTab(target.value);
    } else {
      buttonRefs.current[target.value]?.focus();
    }
  };

  const sizeClass = size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm";

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
      className={`flex flex-wrap gap-1 border-b border-slate-200 ${className}`}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            ref={(node) => {
              buttonRefs.current[item.value] = node;
            }}
            type="button"
            role="tab"
            id={tabButtonId(idPrefix, item.value)}
            aria-selected={active}
            aria-controls={tabPanelId(idPrefix, item.value)}
            aria-disabled={item.disabled || undefined}
            tabIndex={active ? 0 : -1}
            disabled={item.disabled}
            onClick={() => {
              if (item.disabled || item.value === value) return;
              onChange(item.value);
            }}
            onKeyDown={(event) => handleKeyDown(event, item.value)}
            className={[
              "-mb-px inline-flex cursor-pointer items-center border-b-2 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50",
              sizeClass,
              active
                ? "border-slate-900 text-slate-900"
                : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800",
            ].join(" ")}
          >
            {item.label}
            {item.badge !== undefined && item.badge !== null && item.badge !== "" ? (
              <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                {item.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
