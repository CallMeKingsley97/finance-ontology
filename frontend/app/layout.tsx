import type { Metadata } from "next";
import type { ReactNode } from "react";

import AppShell from "@/components/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: "企业财务 Ontology Agent MVP",
  description: "数据源、元数据、本体、自然语言问数、财务报告与敏感脱敏的一体化演示工作台",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
