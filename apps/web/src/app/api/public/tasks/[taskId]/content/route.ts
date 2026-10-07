import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const apiBase = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8010/v1").replace(/\/$/, "");
const MAX_RESPONSE_BYTES = 700_000;

function error(status: number, message: string) {
  return NextResponse.json(
    { error: { code: status === 404 ? "NOT_FOUND" : "PUBLIC_CONTENT_UNAVAILABLE", message } },
    { status, headers: { "Cache-Control": "private, no-store" } },
  );
}

async function boundedText(response: Response): Promise<string | null> {
  const announced = Number(response.headers.get("content-length"));
  if (Number.isFinite(announced) && announced > MAX_RESPONSE_BYTES) return null;
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ taskId: string }> },
) {
  const { taskId } = await params;
  const requestUrl = new URL(request.url);
  const release = requestUrl.searchParams.get("release") ?? "";
  const download = requestUrl.searchParams.get("download") === "1";
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(taskId) || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(release)) {
    return error(400, "Invalid public task reference.");
  }
  try {
    const url = `${apiBase}/tasks/${encodeURIComponent(taskId)}/content?release=${encodeURIComponent(release)}`;
    const upstream = await fetch(url, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(5000),
    });
    if (!upstream.ok) {
      return error(upstream.status === 404 ? 404 : 502, upstream.status === 404 ? "Resource is not available." : "Public task content could not be loaded.");
    }
    const body = await boundedText(upstream);
    if (body === null) return error(413, "Public task content exceeds the response size limit.");
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed !== "object" || parsed === null || !("data" in parsed)) {
      return error(502, "Public task content response is invalid.");
    }
    const data = (parsed as { data?: { task_id?: unknown; task_version?: unknown } }).data;
    if (!data || data.task_id !== taskId || typeof data.task_version !== "number") {
      return error(502, "Public task content identity did not match the request.");
    }
    const headers: Record<string, string> = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
      };
    if (download) {
      headers["Content-Disposition"] = `attachment; filename="polycodebench-task-${taskId}-v${data.task_version}.json"`;
    }
    return new NextResponse(body, { headers });
  } catch {
    return error(503, "Public task content is temporarily unavailable.");
  }
}
