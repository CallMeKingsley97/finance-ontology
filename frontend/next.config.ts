import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  agentRules: false,
  // 允许通过 127.0.0.1 访问开发资源；Next 16 默认只认 localhost，
  // 跨源访问会拦截 /_next/hmr 等资源，导致页面不 hydrate。
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
