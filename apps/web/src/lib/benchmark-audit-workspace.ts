import type { Resource } from "@/lib/public-api";
import type { components } from "@/lib/generated-public-api";

type ApiSchema<Name extends keyof components["schemas"]> = components["schemas"][Name];
export type BenchmarkScopeRow = ApiSchema<"BenchmarkScopeEvidence">;
export type AuditSourceGroup = ApiSchema<"SourceGroupPolicy">;
export type AuditPlanningLimits = ApiSchema<"ResourceLimits">;
export type BenchmarkAuditWorkspaceData = ApiSchema<"PublicAuditWorkspaceData">;
export type BenchmarkAuditWorkspaceResult = ApiSchema<"PublicAuditWorkspaceResult">;
export type AuditResourcePlan = ApiSchema<"AuditResourcePlan">;
export type AuditResourcePlanResult = ApiSchema<"PublicAuditResourcePlanResult">;

export type AuditResourceRequest = {
  readonly task_counts: Readonly<Record<string, number>>;
  readonly source_groups: readonly string[];
  readonly stages: readonly string[];
  readonly average_item_bytes: number | null;
};

export async function loadBenchmarkAuditWorkspace(): Promise<Resource<BenchmarkAuditWorkspaceResult>> {
  try {
    const response = await fetch("/api/benchmark-audit/scope-preview", {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(9000),
    });
    const body: unknown = await response.json();
    if (!response.ok) {
      const code = isObject(body) && isObject(body.error) && typeof body.error.code === "string"
        ? body.error.code
        : "";
      const message = isObject(body) && isObject(body.error) && typeof body.error.message === "string"
        ? body.error.message
        : `The API returned HTTP ${response.status}.`;
      return {
        state: "error",
        title: code === "UNAUTHENTICATED" ? "Verified sign-in required"
          : code === "FORBIDDEN" ? "Tenant-scoped operator access required"
            : "Benchmark audit workspace unavailable",
        message,
      };
    }
    if (!isObject(body) || !isObject(body.data) || !isObject(body.meta)) {
      return { state: "error", title: "Unexpected API response", message: "The scope response did not match its typed contract." };
    }
    return { state: "ready", value: body as BenchmarkAuditWorkspaceResult };
  } catch (error) {
    const timeout = error instanceof DOMException && error.name === "TimeoutError";
    return {
      state: "error",
      title: timeout ? "Benchmark catalog timed out" : "Benchmark audit API unavailable",
      message: timeout ? "The private catalog took too long to respond. Try again." : "Could not reach the private benchmark audit service.",
    };
  }
}

export async function requestAuditResourcePlan(
  input: AuditResourceRequest,
): Promise<Resource<AuditResourcePlanResult>> {
  try {
    const response = await fetch("/api/benchmark-audit/resource-plan", {
      method: "POST",
      cache: "no-store",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify(input),
      signal: AbortSignal.timeout(9000),
    });
    const body: unknown = await response.json();
    if (!response.ok) {
      const message = isObject(body) && isObject(body.error) && typeof body.error.message === "string"
        ? body.error.message
        : `The planner returned HTTP ${response.status}.`;
      return { state: "error", title: "Resource preflight failed", message };
    }
    if (!isObject(body) || !isObject(body.data) || !isObject(body.meta)) {
      return { state: "error", title: "Unexpected planner response", message: "The response did not match the resource preflight contract." };
    }
    return { state: "ready", value: body as AuditResourcePlanResult };
  } catch (error) {
    const timeout = error instanceof DOMException && error.name === "TimeoutError";
    return {
      state: "error",
      title: timeout ? "Resource preflight timed out" : "Resource preflight unavailable",
      message: timeout ? "The no-dispatch estimate took too long. Try again." : "Could not reach the benchmark audit planner.",
    };
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
