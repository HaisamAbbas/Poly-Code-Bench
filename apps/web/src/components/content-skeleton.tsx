export function ContentSkeleton() {
  return (
    <div className="content-skeleton" aria-hidden="true">
      <div className="skeleton-line skeleton-line-short" />
      <div className="skeleton-line" />
      <div className="skeleton-grid">
        <div className="skeleton-cell" />
        <div className="skeleton-cell" />
        <div className="skeleton-cell" />
      </div>
    </div>
  );
}
