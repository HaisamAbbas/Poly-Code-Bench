import type { Metadata } from "next";
import { PublicAuditAttestationState } from "@/components/audit-attestation";
import { isPublicAttestationId, loadPublicAttestation } from "@/lib/audit-attestations";

export const metadata: Metadata = {
  title: "Benchmark Audit Attestation",
  description: "Signed benchmark audit scope and qualified signature verification status.",
};

export default async function AuditAttestationPage({
  params,
}: {
  params: Promise<{ attestationId: string }>;
}) {
  const { attestationId } = await params;
  const resource = isPublicAttestationId(attestationId)
    ? await loadPublicAttestation(attestationId)
    : {
        state: "error" as const,
        title: "Attestation ID is invalid",
        message: "Enter one complete public attestation ID in UUID format.",
      };
  return <PublicAuditAttestationState resource={resource} />;
}
