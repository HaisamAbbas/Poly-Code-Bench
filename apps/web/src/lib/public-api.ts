export type MetricStatus =
  | "measured"
  | "gated_zero"
  | "not_applicable"
  | "insufficient_information"
  | "missing"
  | "needs_review";

export type PublicMetric = {
  readonly kind: "public_metric";
  readonly metric_id: string;
  readonly label: string;
  readonly unit: string;
  readonly direction: "higher" | "lower";
  readonly status: MetricStatus;
  readonly value: string | null;
  readonly interval_low: string | null;
  readonly interval_high: string | null;
  readonly coverage: string | null;
  readonly conditional_on_pass: boolean;
  readonly reason: string | null;
};

export type Coverage = {
  readonly kind: "coverage";
  readonly tasks: number;
  readonly samples: number;
  readonly independent_clusters: number;
};

export type DimensionBreakdown = {
  readonly kind: "dimension_breakdown";
  readonly dimension: string;
  readonly metric: PublicMetric;
  readonly applicable_tasks: number;
  readonly opportunity_count: number;
};

export type LanguageEntryProfile = {
  readonly kind: "language_entry_profile";
  readonly language_id: string;
  readonly model_config_id: string;
  readonly label: string;
  readonly dimensions: readonly DimensionBreakdown[];
  readonly diagnostics: readonly DimensionBreakdown[];
  readonly tool_coverage: readonly (readonly [string, string])[];
  readonly evidence_url: string;
};

export type MetricDefinition = {
  readonly kind: "metric_definition";
  readonly metric_id: string;
  readonly label: string;
  readonly unit: string;
  readonly direction: "higher" | "lower";
  readonly domain: readonly [string, string];
  readonly applicability_rule: string;
  readonly sample_aggregation: "mean";
  readonly task_aggregation: "weighted_mean" | "micro_f1";
  readonly missingness_policy: "block";
  readonly formatter: string;
  readonly uncertainty_method: string;
  readonly source_score_item_ids: readonly string[];
  readonly conditional_on_pass: boolean;
};

export type ReleaseSummary = {
  readonly kind: "release_summary";
  readonly release_id: string;
  readonly version: number;
  readonly state: "published" | "withdrawn";
  readonly scope: "exploratory" | "ranked_eligible";
  readonly fixture_kind: string;
  readonly cohort_digest: string;
  readonly published_at: string | null;
  readonly limitations: readonly string[];
  readonly withdrawal_reason: string | null;
  readonly replacement_release_id: string | null;
  readonly methodology_url: string;
  readonly methodology_version: string;
};

export type LeaderboardEntry = {
  readonly kind: "leaderboard_entry";
  readonly model_config_id: string;
  readonly label: string;
  readonly rank: number | null;
  readonly ranking_label: "exploratory" | "ranked_eligible";
  readonly metrics: readonly PublicMetric[];
  readonly coverage: Coverage;
  readonly languages: readonly string[];
  readonly run_mode: string | null;
  readonly budget_profile_id: string | null;
  readonly generation_cost_micros: string | null;
  readonly latency_ms_p50: number | null;
  readonly latency_ms_p95: number | null;
  readonly evidence_url: string;
};

export type LeaderboardData = {
  readonly release: ReleaseSummary;
  readonly entries: readonly LeaderboardEntry[];
  readonly languages: readonly string[];
  readonly metric_definitions: readonly MetricDefinition[];
};

export type LanguageProfile = {
  readonly kind: "language_profile";
  readonly language_id: string;
  readonly release_id: string;
  readonly dimensions: readonly DimensionBreakdown[];
  readonly diagnostics: readonly DimensionBreakdown[];
  readonly tool_coverage: readonly (readonly [string, string])[];
  readonly metrics: readonly PublicMetric[];
  readonly coverage: Coverage | null;
  readonly entries: readonly LanguageEntryProfile[];
};

export type ModelProfile = {
  readonly kind: "model_profile";
  readonly model_config_id: string;
  readonly label: string;
  readonly release_id: string;
  readonly capabilities: readonly string[];
  readonly dimensions: readonly DimensionBreakdown[];
  readonly language_profiles: readonly LanguageEntryProfile[];
  readonly languages: readonly string[];
  readonly run_mode: string | null;
  readonly budget_profile_id: string | null;
  readonly generation_cost_micros: string | null;
  readonly latency_ms_p50: number | null;
  readonly latency_ms_p95: number | null;
  readonly metrics: readonly PublicMetric[];
  readonly coverage: Coverage | null;
  readonly evidence_url: string;
};

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

export type ApiEnvelope<T> = {
  readonly data: T;
  readonly meta: {
    readonly request_id?: string;
    readonly release_id?: string | null;
    readonly release_digest: string;
    readonly exploratory?: boolean | null;
    readonly total?: number | null;
    readonly returned?: number | null;
    readonly limit?: number | null;
    readonly next_cursor?: string | null;
    readonly sort?: string | null;
    readonly filters?: readonly string[];
    readonly current_release_id?: string | null;
    readonly registry?: {
      readonly policy_digest: string;
      readonly definitions: readonly MetricDefinition[];
    } | null;
  };
};

export type Incompatibility = {
  readonly kind: "incompatibility";
  readonly code: string;
  readonly model_config_id: string | null;
  readonly detail: string;
};

export type ComparisonTaskRef = {
  readonly kind: "comparison_task_ref";
  readonly task_id: string;
  readonly task_version: number;
  readonly language_id: string;
  readonly family: string;
  readonly difficulty: string;
  readonly scorecards: readonly {
    readonly kind: "comparison_scorecard_ref";
    readonly model_config_id: string;
    readonly scorecard_id: string;
    readonly evidence_url: string;
  }[];
};

export type PairedTaskDelta = {
  readonly kind: "paired_task_delta";
  readonly task_id: string;
  readonly task_version: number;
  readonly metric_id: string;
  readonly label: string;
  readonly baseline_model_config_id: string;
  readonly candidate_model_config_id: string;
  readonly baseline_scorecard_id: string;
  readonly candidate_scorecard_id: string;
  readonly baseline_value: string | null;
  readonly candidate_value: string | null;
  readonly delta_value: string | null;
  readonly interval_low: string | null;
  readonly interval_high: string | null;
  readonly interval_method: "reported_interval_difference_bounds" | "unavailable";
  readonly status: MetricStatus;
  readonly reason: string | null;
};

export type PairedDelta = {
  readonly kind: "paired_delta";
  readonly metric_id: string;
  readonly label: string;
  readonly baseline_model_config_id: string;
  readonly candidate_model_config_id: string;
  readonly delta_value: string | null;
  readonly interval_low: string | null;
  readonly interval_high: string | null;
  readonly interval_method: "reported_interval_difference_bounds" | "unavailable";
  readonly status: MetricStatus;
  readonly reason: string | null;
};

export type ComparisonResult = {
  readonly kind: "comparison_result";
  readonly release_id: string;
  readonly cohort_digest: string;
  readonly scope: "exploratory" | "ranked_eligible";
  readonly common_tasks: number;
  readonly common_independent_clusters: number | null;
  readonly entries: readonly LeaderboardEntry[];
  readonly deltas: readonly PairedDelta[];
  readonly common_task_refs: readonly ComparisonTaskRef[];
  readonly paired_task_deltas: readonly PairedTaskDelta[];
  readonly incompatibilities: readonly Incompatibility[];
  readonly limitations: readonly string[];
};

export type TaskSummary = {
  readonly kind: "task_summary";
  readonly task_id: string;
  readonly version: number;
  readonly language_id: string;
  readonly family: string;
  readonly difficulty: string;
  readonly statement_summary: string;
  readonly evidence_url: string;
  readonly source_version_count: number;
  readonly patch_count: number;
  readonly finding_count: number;
};

export type PublicTaskContent = {
  readonly kind: "public_task_content";
  readonly task_id: string;
  readonly task_version: number;
  readonly statement: string;
  readonly source_versions: readonly {
    readonly source_id: string;
    readonly version_label: string;
    readonly path: string;
    readonly language_id: string;
    readonly source_text: string;
  }[];
  readonly submitted_patches: readonly {
    readonly patch_id: string;
    readonly model_config_id: string;
    readonly scorecard_id: string;
    readonly summary: string;
    readonly diff_text: string;
  }[];
  readonly tool_findings: readonly {
    readonly finding_id: string;
    readonly model_config_id: string;
    readonly scorecard_id: string;
    readonly source_id: string;
    readonly tool_id: string;
    readonly rule_id: string;
    readonly severity: "critical" | "high" | "medium" | "low" | "info";
    readonly line: number | null;
    readonly message: string;
  }[];
};

export type PublicScorecard = {
  readonly kind: "public_scorecard";
  readonly scorecard_id: string;
  readonly release_id: string;
  readonly model_config_id: string;
  readonly task_id: string;
  readonly task_version: number;
  readonly formula_version: string;
  readonly policy_digest: string;
  readonly gating_status: "scored" | "gated_zero" | "needs_review";
  readonly metrics: readonly PublicMetric[];
  readonly contributions: readonly {
    readonly item_id: string;
    readonly dimension: string;
    readonly nominal_weight_bp: number;
    readonly effective_weight_bp: number;
    readonly presentation_weight_bp: number;
    readonly arithmetic: string;
    readonly evidence_refs: readonly string[];
    readonly value: string | null;
  }[];
  readonly evidence_url: string;
  readonly redacted_evidence_count: number;
};

export type Methodology = {
  readonly kind: "methodology";
  readonly version: string;
  readonly methods: readonly string[];
  readonly formulas: readonly string[];
  readonly tools: readonly string[];
  readonly deviations: readonly string[];
  readonly native_benchmarks: readonly (readonly [string, string])[];
  readonly correction_history: readonly string[];
  readonly limitations: readonly string[];
};

export type ReleaseContext = {
  readonly releaseId: string;
  readonly summary: ReleaseSummary;
  readonly releases: readonly ReleaseSummary[];
};

export type ModelSubmissionRequest = {
  readonly kind: "model_submission_input";
  readonly model_name: string;
  readonly provider: string;
  readonly organization?: string;
  readonly contact_email: string;
  readonly endpoint_url: string;
  readonly source_url: string;
  readonly source_license: string;
  readonly permission_attested: boolean;
};

export type ModelSubmissionStatus = {
  readonly kind: "model_submission";
  readonly submission_id: string;
  readonly status: "pending" | "rejected" | "approved";
  readonly model_name: string;
  readonly provider: string;
  readonly organization: string | null;
  readonly contact_email: string;
  readonly endpoint_url: string;
  readonly source_url: string;
  readonly source_license: string;
  readonly permission_attested: boolean;
  readonly submitted_at: string;
  readonly row_version: number;
  readonly rejection_reason: string | null;
  readonly resulting_run_id: string | null;
  readonly run_status: string | null;
};

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

export async function sendModelSubmission(
  token: string,
  idempotencyKey: string,
  submission: ModelSubmissionRequest,
): Promise<Resource<ApiEnvelope<ModelSubmissionStatus>>> {
  return authenticatedSubmissionApi<ApiEnvelope<ModelSubmissionStatus>>(
    "/api/model-submissions",
    token,
    {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": idempotencyKey },
      body: JSON.stringify(submission),
    },
  );
}

export async function loadModelSubmissionStatus(
  token: string,
  submissionId: string,
): Promise<Resource<ApiEnvelope<ModelSubmissionStatus>>> {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(submissionId)) {
    return { state: "error", title: "Request ID is invalid", message: "Enter the request ID returned after submission." };
  }
  return authenticatedSubmissionApi<ApiEnvelope<ModelSubmissionStatus>>(
    `/api/model-submissions/${encodeURIComponent(submissionId)}`,
    token,
    { method: "GET" },
  );
}

async function authenticatedSubmissionApi<T>(
  path: string,
  token: string,
  init: RequestInit,
): Promise<Resource<T>> {
  if (!token.trim() || token.length > 4096 || /[\r\n]/.test(token)) {
    return { state: "error", title: "Verified account sign-in required", message: "Provide the short lived access token for your verified account. Provider credentials are never requested." };
  }
  try {
    const headers = new Headers(init.headers);
    headers.set("accept", "application/json");
    headers.set("authorization", `Bearer ${token.trim()}`);
    const response = await fetch(path, {
      ...init,
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(9000),
    });
    const body: unknown = await response.json();
    if (!response.ok) {
      const error = body as ErrorEnvelope;
      const code = error.error?.code;
      return {
        state: "error",
        title: code === "FORBIDDEN" ? "Verified account could not be confirmed" : "Submission request failed",
        message: code === "RATE_LIMITED"
          ? "Too many new requests. Wait for the limit window to pass, then try again."
          : code === "NOT_FOUND"
            ? "No request with that ID belongs to this verified account."
            : error.error?.message ?? `The API returned HTTP ${response.status}.`,
        requestId: error.error?.request_id,
      };
    }
    if (typeof body !== "object" || body === null || !("data" in body)) {
      return { state: "error", title: "Unexpected API response", message: "The model-submission response did not match its contract." };
    }
    return { state: "ready", value: body as T };
  } catch (error) {
    const timeout = error instanceof DOMException && error.name === "TimeoutError";
    return {
      state: "error",
      title: timeout ? "Submission request timed out" : "Submission service unavailable",
      message: timeout ? "Check request status before trying again." : "Could not reach the submission service. Try again later.",
    };
  }
}

export function combineResources<A, B>(first: Resource<A>, second: Resource<B>): Resource<readonly [A, B]> {
  if (first.state !== "ready") return first;
  if (second.state !== "ready") return second;
  return { state: "ready", value: [first.value, second.value] };
}

const apiBase = (process.env.PCB_PUBLIC_API_URL ?? "http://127.0.0.1:8000/v1").replace(/\/$/, "");

export async function publicApi<T>(path: string): Promise<Resource<T>> {
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

function numericMetric(metric: PublicMetric | undefined): number | null {
  if (!metric) return null;
  if (metric.status === "gated_zero") return 0;
  if (metric.status !== "measured" || metric.value === null) return null;
  const value = Number(metric.value);
  return Number.isFinite(value) ? value : null;
}
