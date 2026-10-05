import {
  createApiBearer,
  cookieValue,
  isSameOriginMutation,
  readSessionToken,
  sessionCookieName,
} from "@/lib/oidc-auth";
import { readBoundedBody } from "@/lib/request-body";

const API_BASE = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8000/v1").replace(/\/$/, "");
const MAX_BODY_BYTES = 12_000;
const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{1,255}$/;

function jsonHeaders(): HeadersInit {
  return { "cache-control": "private, no-store", "content-type": "application/json; charset=utf-8" };
}

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json(
    { error: { code, message, request_id: crypto.randomUUID() } },
    { status, headers: jsonHeaders() },
  );
}

export async function POST(request: Request): Promise<Response> {
  if (!isSameOriginMutation(request)) return errorResponse(403, "FORBIDDEN", "request origin is not allowed");
  const session = readSessionToken(cookieValue(request.headers.get("cookie"), sessionCookieName()));
  const key = request.headers.get("idempotency-key") ?? "";
  if (!session) return errorResponse(401, "UNAUTHENTICATED", "authentication is required");
  if (!IDEMPOTENCY_KEY.test(key)) return errorResponse(422, "SCHEMA_INVALID", "request is not valid");
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    return errorResponse(415, "SCHEMA_INVALID", "request is not valid");
  }
  const bytes = await readBoundedBody(request, MAX_BODY_BYTES);
  if (bytes === null) return errorResponse(413, "SCHEMA_INVALID", "request is not valid");
  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    return errorResponse(400, "SCHEMA_INVALID", "request is not valid");
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return errorResponse(400, "SCHEMA_INVALID", "request is not valid");
  }
  const body = JSON.stringify({ ...payload, contact_email: session.email });
  if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES) return errorResponse(413, "SCHEMA_INVALID", "request is not valid");
  try {
    const upstream = await fetch(`${API_BASE}/model-submissions`, {
      method: "POST",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${createApiBearer(session)}`,
        "content-type": "application/json",
        "idempotency-key": key,
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        ...jsonHeaders(),
        ...(upstream.headers.get("x-request-id") ? { "x-request-id": upstream.headers.get("x-request-id")! } : {}),
      },
    });
  } catch {
    return errorResponse(503, "DEPENDENCY_UNAVAILABLE", "dependency is unavailable");
  }
}
