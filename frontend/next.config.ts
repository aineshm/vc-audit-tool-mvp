import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow LAN access from other devices (phones, tablets) during development
  allowedDevOrigins: ["192.168.4.58", "192.168.4.*"],
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
    return [
      // Map frontend /api/* calls to backend endpoints (no /api/ prefix on backend)
      { source: "/api/research", destination: `${apiBase}/research` },
      { source: "/api/reconcile", destination: `${apiBase}/reconcile` },
      { source: "/api/value", destination: `${apiBase}/value` },
      // Backend endpoints that DO live under /api/
      { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
      // Direct proxies (for non-page routes)
      { source: "/health", destination: `${apiBase}/health` },
    ];
  },
};

export default nextConfig;
