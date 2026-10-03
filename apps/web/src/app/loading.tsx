export default function Loading() {
  return (
    <section className="state-card" aria-live="polite" aria-busy="true">
      <span className="state-kicker">Loading release data</span>
      <p>Connecting to the public release API…</p>
    </section>
  );
}
