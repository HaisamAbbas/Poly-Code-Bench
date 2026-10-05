"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

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
