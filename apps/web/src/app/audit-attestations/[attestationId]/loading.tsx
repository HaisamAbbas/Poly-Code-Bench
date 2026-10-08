export default function AuditAttestationLoading() {
  return (
    <section className="audit-unavailable" aria-live="polite" aria-busy="true">
      <span className="state-kicker">Loading signed attestation</span>
      <h2>Checking signature and available revocation status</h2>
      <p>Scoped claims will appear only after the public signature-verification response arrives.</p>
    </section>
  );
}
