import type { components, paths } from "./generated-public-api";

type ApiSchema<Name extends keyof components["schemas"]> = components["schemas"][Name];
type PublicPath = Exclude<
  Extract<keyof paths, `/v1/${string}`>,
  `/v1/admin/${string}` | `/v1/benchmark-audit/${string}` | `/v1/model-submissions${string}`
>;
type GetOperation<Path extends keyof paths> = Path extends keyof paths
  ? paths[Path] extends { readonly get: infer Operation }
    ? Operation
    : never
  : never;
type SuccessEnvelope<Operation> = Operation extends { readonly responses: infer Responses }
  ? Responses extends { readonly 200: infer Response }
    ? Response extends { readonly content: { readonly "application/json": infer Body } }
      ? Body
      : never
    : never
  : never;
type PublicReadEnvelope = SuccessEnvelope<GetOperation<PublicPath>>;

export type MetricStatus = ApiSchema<"PublicMetric">["status"];
export type PublicMetric = ApiSchema<"PublicMetric">;
export type Coverage = ApiSchema<"Coverage">;
export type DimensionBreakdown = ApiSchema<"DimensionBreakdown">;
export type LanguageEntryProfile = ApiSchema<"LanguageEntryProfile">;
export type MetricDefinition = ApiSchema<"MetricDefinition">;
export type ReleaseSummary = ApiSchema<"ReleaseSummary">;
export type LeaderboardEntry = ApiSchema<"LeaderboardEntry">;
export type LeaderboardData = {
  readonly release: ReleaseSummary;
  readonly entries: readonly LeaderboardEntry[];
  readonly languages: readonly string[];
  readonly metric_definitions: readonly MetricDefinition[];
};
export type LanguageProfile = ApiSchema<"LanguageProfile">;
export type ModelProfile = ApiSchema<"ModelProfile">;
export type LanguageData = {
  readonly release: ReleaseSummary;
  readonly profile: LanguageProfile;
  readonly entries: readonly LeaderboardEntry[];
  readonly metric_definitions: readonly MetricDefinition[];
};
export type ModelData = {
  readonly release: ReleaseSummary;
  readonly profile: ModelProfile;
  readonly metric_definitions: readonly MetricDefinition[];
};
export type ApiEnvelope<T> = Omit<ApiSchema<"ApiEnvelope_ReleaseSummary_">, "data"> & {
  readonly data: T;
};
export type Incompatibility = ApiSchema<"Incompatibility">;
export type ComparisonTaskRef = ApiSchema<"ComparisonTaskRef">;
export type PairedTaskDelta = ApiSchema<"PairedTaskDelta">;
export type PairedDelta = ApiSchema<"PairedDelta">;
export type ComparisonResult = ApiSchema<"ComparisonResult">;
export type TaskSummary = ApiSchema<"TaskSummary">;
export type PublicTaskContent = ApiSchema<"PublicTaskContent">;
export type PublicScorecard = ApiSchema<"PublicScorecard">;
export type Methodology = ApiSchema<"Methodology">;
export type ReleaseContext = {
  readonly releaseId: string;
  readonly summary: ReleaseSummary;
  readonly releases: readonly ReleaseSummary[];
};
export type PublicAuditDocumentResult = ApiSchema<"AuditDocumentResult">;
export type PublicBenchmarkHealth = ApiSchema<"PublicHealthView">;
export type PublicAuditAttestationResult = ApiSchema<"PublicAuditAttestationResult">;
export type PublicAuditAttestationView = ApiSchema<"PublicAuditAttestationView">;
export async function loadReleaseContext(requestedRelease?: string): Promise<Resource<ReleaseContext>> {
  const index = await publicApi<ApiEnvelope<readonly ReleaseSummary[]>>("/releases?limit=200");
  if (index.state !== "ready") return index;
  const rows = index.value.data;
  const selected = requestedRelease ?? index.value.meta.current_release_id ?? rows[0]?.release_id;
  if (!selected) {
    return {
      state: "empty",
      title: "No published releases are available",
      message: "The public API has no release-backed data for this workspace yet.",
    };
  }
  let summary = rows.find((release) => release.release_id === selected);
  if (!summary) {
    const result = await publicApi<ApiEnvelope<ReleaseSummary>>(`/releases/${encodeURIComponent(selected)}`);
    if (result.state !== "ready") return result;
    summary = result.value.data;
  }
  const releases = rows.some((release) => release.release_id === selected) ? rows : [...rows, summary];
  return { state: "ready", value: { releaseId: selected, summary, releases } };
}

export type Resource<T> =
  | { readonly state: "loading" }
  | { readonly state: "ready"; readonly value: T }
  | { readonly state: "empty"; readonly title: string; readonly message: string }
  | {
      readonly state: "error";
      readonly title: string;
      readonly message: string;
      readonly requestId?: string;
    };

type ErrorEnvelope = {
  readonly error?: {
    readonly code?: string;
    readonly message?: string;
    readonly request_id?: string;
  };
};

export function combineResources<A, B>(first: Resource<A>, second: Resource<B>): Resource<readonly [A, B]> {
  if (first.state !== "ready") return first;
  if (second.state !== "ready") return second;
  return { state: "ready", value: [first.value, second.value] };
}

const apiBase = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8010/v1").replace(/\/$/, "");

export async function publicApi<T extends PublicReadEnvelope>(path: string): Promise<Resource<T>> {
  try {
    const response = await fetch(`${apiBase}${path}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(5000),
    });
    const body: unknown = await response.json();
    if (!response.ok) {
      const error = body as ErrorEnvelope;
      return {
        state: "error",
        title: response.status === 404 ? "Release data not found" : "Public API request failed",
        message: error.error?.message ?? `The public API returned HTTP ${response.status}.`,
        requestId: error.error?.request_id,
      };
    }
    if (typeof body !== "object" || body === null || !("data" in body)) {
      return {
        state: "error",
        title: "Unexpected API response",
        message: "The public API response did not match the expected envelope.",
      };
    }
    return { state: "ready", value: body as T };
  } catch (error) {
    const timeout = error instanceof DOMException && error.name === "TimeoutError";
    return {
      state: "error",
      title: timeout ? "Public API timed out" : "Public API unavailable",
      message: timeout
        ? "The release projection took too long to respond. Try again."
        : "Could not connect to the public release API. Check that the API service is running and retry.",
    };
  }
}

export function asUrlQuery(query: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value) params.set(key, value);
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

export function scoreEvidenceHref(sourceUrl: string, releaseId: string): string {
  try {
    const source = new URL(sourceUrl, "https://polycodebench.invalid");
    if (
      source.origin !== "https://polycodebench.invalid" ||
      !source.pathname.startsWith("/v1/scorecards/") ||
      source.pathname.includes("..")
    ) {
      return "/leaderboard";
    }
    source.searchParams.set("release", releaseId);
    return `${source.pathname}${source.search}`;
  } catch {
    return "/leaderboard";
  }
}

export function scorecardPageHref(sourceUrl: string, releaseId: string): string {
  try {
    const source = new URL(sourceUrl, "https://polycodebench.invalid");
    const match = source.pathname.match(/^\/v1\/scorecards\/([A-Za-z0-9][A-Za-z0-9._-]{0,119})$/);
    if (source.origin !== "https://polycodebench.invalid" || !match) return "/leaderboard";
    return `/scorecards/${encodeURIComponent(match[1])}?release=${encodeURIComponent(releaseId)}`;
  } catch {
    return "/leaderboard";
  }
}

export function metricFor(entry: LeaderboardEntry, metricId: string): PublicMetric | undefined {
  return entry.metrics.find((metric) => metric.metric_id === metricId);
}

export function orderedEntries(
  entries: readonly LeaderboardEntry[],
  metricId: string | undefined,
  direction: "asc" | "desc",
): readonly LeaderboardEntry[] {
  if (!metricId) return entries;
  const factor = direction === "asc" ? 1 : -1;
  return [...entries].sort((left, right) => {
    const leftMetric = metricFor(left, metricId);
    const rightMetric = metricFor(right, metricId);
    const leftValue = numericMetric(leftMetric);
    const rightValue = numericMetric(rightMetric);
    if (leftValue === null && rightValue === null) return left.label.localeCompare(right.label);
    if (leftValue === null) return 1;
    if (rightValue === null) return -1;
    return factor * (leftValue - rightValue) || left.label.localeCompare(right.label);
  });
}

export function orderedLanguageEntries(
  entries: readonly LanguageEntryProfile[],
  metricId: string | undefined,
  direction: "asc" | "desc",
): readonly LanguageEntryProfile[] {
  if (!metricId) return entries;
  const factor = direction === "asc" ? 1 : -1;
  return [...entries].sort((left, right) => {
    const leftMetric = left.dimensions.find((row) => row.metric.metric_id === metricId)?.metric;
    const rightMetric = right.dimensions.find((row) => row.metric.metric_id === metricId)?.metric;
    const leftValue = numericMetric(leftMetric);
    const rightValue = numericMetric(rightMetric);
    if (leftValue === null && rightValue === null) return left.label.localeCompare(right.label);
    if (leftValue === null) return 1;
    if (rightValue === null) return -1;
    return factor * (leftValue - rightValue) || left.label.localeCompare(right.label);
  });
}

function numericMetric(metric: PublicMetric | undefined): number | null {
  if (!metric) return null;
  if (metric.status === "gated_zero") return 0;
  if (metric.status !== "measured" || metric.value === null) return null;
  const value = Number(metric.value);
  return Number.isFinite(value) ? value : null;
}
