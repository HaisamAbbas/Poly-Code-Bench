import type { PublicAuditAttestationResult, PublicAuditAttestationView, Resource } from "@/lib/public-api";
import { publicApi } from "@/lib/public-api";

const ATTESTATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isPublicAttestationId(value: string): boolean {
  return ATTESTATION_ID.test(value);
}

export async function loadPublicAttestation(
  attestationId: string,
): Promise<Resource<PublicAuditAttestationView>> {
  if (!isPublicAttestationId(attestationId)) {
    return {
      state: "error",
      title: "Attestation ID is invalid",
      message: "Enter one complete public attestation ID in UUID format.",
    };
  }
  const result = await publicApi<PublicAuditAttestationResult>(
    `/public/audit-attestations/${encodeURIComponent(attestationId)}`,
  );
  if (result.state === "error") {
    return {
      ...result,
      title: result.title === "Release data not found"
        ? "Public attestation not found"
        : "Unable to verify public attestation",
      message: result.title === "Release data not found"
        ? "No public attestation with this ID is available."
        : result.title === "Public API timed out"
          ? "The public attestation took too long to respond. Try again."
          : result.title === "Public API unavailable"
            ? "Could not connect to public attestation verification. Try again later."
            : result.message,
    };
  }
  if (result.state !== "ready") return result;
  if (result.value.data.attestation.claims.attestation_id.toLowerCase() !== attestationId.toLowerCase()) {
    return {
      state: "error",
      title: "Unexpected public attestation response",
      message: "The API response did not match the requested attestation ID.",
    };
  }
  return { state: "ready", value: result.value.data };
}
