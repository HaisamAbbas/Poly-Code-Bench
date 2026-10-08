import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { PageIntro } from "@/components/public-ui";
import { isPublicAttestationId } from "@/lib/audit-attestations";

export const metadata: Metadata = {
  title: "Verify a benchmark audit attestation",
  description: "Open a signed public Benchmark Audit Attestation and review its scope and limitations.",
};

export default async function AuditAttestationLookupPage({
  searchParams,
}: {
  searchParams: Promise<{ attestation_id?: string | string[] }>;
}) {
  const value = (await searchParams).attestation_id;
  const attestationId = typeof value === "string" ? value.trim() : "";
  if (attestationId && isPublicAttestationId(attestationId)) {
    redirect(`/audit-attestations/${encodeURIComponent(attestationId)}`);
  }
  const invalid = value !== undefined && !isPublicAttestationId(attestationId);

  return (
    <>
      <PageIntro
        eyebrow="Public signature verification"
        title="Verify an audit attestation"
        description="Review the signed scope, signature status, expiry, and available revocation information. A signature does not guarantee unseen-data status."
      />
      <section className="section-card audit-lookup-card" aria-labelledby="attestation-lookup-heading">
        <h2 id="attestation-lookup-heading">Attestation lookup</h2>
        <p>The public attestation catalog is not available yet. Open an attestation using its public ID from an approved report link.</p>
        <form action="/audit-attestations" method="get" className="audit-lookup-form">
          <label htmlFor="attestation-id">Public attestation ID</label>
          <input
            id="attestation-id"
            name="attestation_id"
            type="text"
            inputMode="text"
            autoComplete="off"
            maxLength={36}
            pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            required
          />
          <p id="attestation-id-help">The verifier displays only signed allowlisted aggregates and validity information.</p>
          {invalid && !attestationId ? <p className="audit-form-error" role="alert">Enter one valid attestation ID in UUID format.</p> : null}
          <button type="submit">Verify attestation</button>
        </form>
      </section>
    </>
  );
}
