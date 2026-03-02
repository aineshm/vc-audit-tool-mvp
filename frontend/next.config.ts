import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow LAN access from other devices (phones, tablets) during development
  allowedDevOrigins: ["192.168.4.58", "192.168.4.*"],
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
    return [
      { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
      { source: "/research", destination: `${apiBase}/research` },
      { source: "/reconcile", destination: `${apiBase}/reconcile` },
      { source: "/value", destination: `${apiBase}/value` },
      { source: "/health", destination: `${apiBase}/health` },
    ];
  },
};

export default nextConfig;
