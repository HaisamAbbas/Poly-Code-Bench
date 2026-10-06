import type { NextConfig } from "next";
import path from "node:path";

const apiBase = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8000/v1").replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.resolve(process.cwd(), "../.."),
  // Keep isolated browser-test servers from contending with a developer's `.next/dev` lock.
  distDir:
    process.env.NODE_ENV === "production"
      ? ".next"
      : (process.env.PCB_NEXT_DIST_DIR ?? ".next"),
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
