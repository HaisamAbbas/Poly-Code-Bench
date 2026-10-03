import type { NextConfig } from "next";

const apiBase = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8000/v1").replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/v1/:path*",
        destination: `${apiBase}/:path*`,
      },
    ];
  },
};

export default nextConfig;
