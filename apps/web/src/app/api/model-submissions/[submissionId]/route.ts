const API_BASE = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8000/v1").replace(/\/$/, "");
const BEARER = /^Bearer [A-Za-z0-9._~+/=-]{16,4096}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function GET(
  request: Request,
  { params }: { params: Promise<{ submissionId: string }> },
): Promise<Response> {
  const authorization = request.headers.get("authorization") ?? "";
  if (!BEARER.test(authorization)) {
    return Response.json(
      { error: { code: "UNAUTHENTICATED", message: "authentication is required", request_id: crypto.randomUUID() } },
      { status: 401, headers: { "cache-control": "private, no-store" } },
    );
  }
  const { submissionId } = await params;
  if (!UUID_V4.test(submissionId)) {
    return Response.json(
      { error: { code: "NOT_FOUND", message: "resource is not available", request_id: crypto.randomUUID() } },
      { status: 404, headers: { "cache-control": "private, no-store" } },
    );
  }
  try {
    const upstream = await fetch(`${API_BASE}/model-submissions/${encodeURIComponent(submissionId)}`, {
      headers: { accept: "application/json", authorization },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "cache-control": "private, no-store",
        "content-type": "application/json; charset=utf-8",
        ...(upstream.headers.get("x-request-id") ? { "x-request-id": upstream.headers.get("x-request-id")! } : {}),
      },
    });
  } catch {
    return Response.json(
      { error: { code: "DEPENDENCY_UNAVAILABLE", message: "dependency is unavailable", request_id: crypto.randomUUID() } },
      { status: 503, headers: { "cache-control": "private, no-store" } },
    );
  }
}
