export default function PublicAuditReportLoading() {
  return (
    <section className="audit-unavailable" aria-live="polite" aria-busy="true">
      <span className="state-kicker">Loading public report</span>
      <h2>Retrieving the reviewed aggregate</h2>
      <p>Scope counts and evidence limitations will appear when the public projection responds.</p>
    </section>
  );
}
