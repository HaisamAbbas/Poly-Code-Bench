"use client";

import { useLayoutEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

export function KeyboardScrollRegion({
  label,
  className,
  children,
}: {
  label: string;
  className: string;
  children: ReactNode;
}) {
  const region = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const element = region.current;
    const table = element?.querySelector<HTMLTableElement>(".data-table");
    if (!element || !table) return;

    // Derive presentation labels from the rendered headers, preserving data and column order.
    const headers = Array.from(table.tHead?.rows[0]?.cells ?? []).map((cell) => {
      const copy = cell.cloneNode(true) as HTMLElement;
      copy.querySelectorAll(".sr-only, [aria-hidden='true']").forEach((node) => node.remove());
      return copy.textContent?.replace(/\s+/g, " ").trim() ?? "";
    });
    table.setAttribute("role", "table");
    for (const group of table.querySelectorAll("thead, tbody")) group.setAttribute("role", "rowgroup");
    for (const row of Array.from(table.rows)) {
      row.setAttribute("role", "row");
      let column = 0;
      for (const cell of Array.from(row.cells)) {
        cell.setAttribute("role", cell.tagName === "TD" ? "cell" : cell.scope === "row" ? "rowheader" : "columnheader");
        if (row.parentElement?.tagName === "TBODY") {
          cell.dataset.label = headers.slice(column, column + cell.colSpan).join(" · ");
        }
        column += cell.colSpan;
      }
    }

    // Measure the intrinsic table once per content change; observing only container width
    // avoids a resize loop when the card layout changes the table's height.
    element.dataset.layout = "table";
    const configuredMinimum = parseFloat(getComputedStyle(table).minWidth) || 0;
    const originalWidth = table.style.width;
    const originalMinimum = table.style.minWidth;
    table.style.width = "min-content";
    table.style.minWidth = "0";
    const minimum = Math.max(configuredMinimum, table.getBoundingClientRect().width);
    table.style.width = originalWidth;
    table.style.minWidth = originalMinimum;

    let lastWidth = element.clientWidth;
    function arrange(width: number) {
      element!.dataset.layout = width + 1 < minimum ? "cards" : "table";
    }
    arrange(lastWidth);
    const observer = new ResizeObserver(() => {
      const width = element.clientWidth;
      if (width !== lastWidth) {
        lastWidth = width;
        arrange(width);
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [children]);

  function scrollWithArrowKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const element = region.current;
    if (!element || element.scrollWidth <= element.clientWidth) return;

    const direction = event.key === "ArrowRight" ? 1 : -1;
    element.scrollBy({ left: direction * Math.max(80, element.clientWidth * 0.7) });
    event.preventDefault();
  }

  return (
    <div
      ref={region}
      className={className}
      role="region"
      aria-label={label}
      tabIndex={0}
      onKeyDown={scrollWithArrowKey}
    >
      {children}
    </div>
  );
}
