import "server-only";
import { createApiBearer, getCurrentSession, webAuditOperatorTenant } from "@/lib/oidc-auth";
import type { WebSession } from "@/lib/oidc-auth";

const API_BASE = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8010/v1").replace(/\/$/, "");

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

export async function proxyBenchmarkAudit(
  request: Request,
  resource: "scope-preview" | "resource-plan",
  method: "GET" | "POST",
  body?: string,
): Promise<Response> {
  let session: WebSession | null;
  try {
    session = await getCurrentSession(request.headers.get("cookie"));
  } catch {
    return errorResponse(503, "DEPENDENCY_UNAVAILABLE", "Sign-in is not configured for this workspace.");
  }
  if (!session) return errorResponse(401, "UNAUTHENTICATED", "Sign in with a verified account to continue.");
  if (!webAuditOperatorTenant(session.subject)) {
    return errorResponse(403, "FORBIDDEN", "A tenant-scoped operator grant is required for private benchmark audit data.");
  }

  try {
    const upstream = await fetch(`${API_BASE}/benchmark-audit/${resource}`, {
      method,
      cache: "no-store",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${createApiBearer(session)}`,
        ...(method === "POST" ? { "content-type": "application/json" } : {}),
      },
      ...(body === undefined ? {} : { body }),
      signal: AbortSignal.timeout(7000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "cache-control": "no-store",
        "content-type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch {
    return errorResponse(503, "DEPENDENCY_UNAVAILABLE", "The private benchmark audit API is unavailable.");
  }
}

export function proxyError(status: number, code: string, message: string): Response {
  return errorResponse(status, code, message);
}
