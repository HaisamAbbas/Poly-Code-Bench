import { proxyBenchmarkAudit, proxyError } from "@/lib/benchmark-audit-proxy";

const MAX_BODY_BYTES = 64 * 1024;

async function readBoundedBody(request: Request): Promise<string | null> {
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) return null;
  if (!request.body) return null;

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }

  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(body);
}

export async function POST(request: Request): Promise<Response> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    return proxyError(415, "SCHEMA_INVALID", "Send a JSON resource-plan request.");
  }

  let body: string;
  try {
    const boundedBody = await readBoundedBody(request);
    if (boundedBody === null) return proxyError(413, "SCHEMA_INVALID", "The request is too large.");
    body = boundedBody;
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return proxyError(400, "SCHEMA_INVALID", "The resource-plan request must be an object.");
    }
  } catch {
    return proxyError(400, "SCHEMA_INVALID", "The resource-plan request is invalid JSON.");
  }
  return proxyBenchmarkAudit(request, "resource-plan", "POST", body);
}
