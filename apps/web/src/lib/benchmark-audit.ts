import type { PublicAuditDocumentResult, PublicBenchmarkHealth, Resource } from "@/lib/public-api";
import { publicApi } from "@/lib/public-api";

const REPORT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isPublicReportId(value: string): boolean {
  return REPORT_ID.test(value);
}

export async function loadPublicBenchmarkHealth(
  reportId: string,
): Promise<Resource<PublicBenchmarkHealth>> {
  if (!isPublicReportId(reportId)) {
    return {
      state: "error",
      title: "Report ID is invalid",
      message: "Enter one complete public report ID in UUID format.",
    };
  }

  const result = await publicApi<PublicAuditDocumentResult>(
    `/public/audit-reports/${encodeURIComponent(reportId)}`,
  );
  if (result.state === "error") {
    if (result.title === "Release data not found") {
      return {
        ...result,
        title: "Public audit report not found",
        message: "No public aggregate report with this ID is available.",
      };
    }
    return {
      ...result,
      title: "Unable to load public audit report",
      message: result.title === "Public API timed out"
        ? "The public audit report took too long to respond. Try again."
        : result.title === "Public API unavailable"
          ? "Could not connect to public audit reports. Try again later."
          : result.message,
    };
  }
  if (result.state !== "ready") return result;

  const data = result.value.data;
  if (
    !("kind" in data) ||
    data.kind !== "public_benchmark_health" ||
    data.report_id.toLowerCase() !== reportId.toLowerCase()
  ) {
    return {
      state: "error",
      title: "Unexpected public report response",
      message: "The API response did not match the reviewed public health contract.",
    };
  }

  return { state: "ready", value: data };
}
