import { proxyBenchmarkAudit } from "@/lib/benchmark-audit-proxy";

export async function GET(request: Request): Promise<Response> {
  return proxyBenchmarkAudit(request, "scope-preview", "GET");
}
