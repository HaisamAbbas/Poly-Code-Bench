"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState, type KeyboardEvent } from "react";

const items = [
  { href: "/leaderboard", label: "Leaderboard", paths: ["/leaderboard", "/models"] },
  { href: "/leaderboard#language-filter", label: "Languages", paths: ["/languages"], anchor: true },
  { href: "/compare", label: "Compare", paths: ["/compare"] },
  { href: "/tasks", label: "Tasks", paths: ["/tasks", "/scorecards", "/methodology"] },
  { href: "/audit-reports", label: "Audit reports", paths: ["/audit-reports"] },
  { href: "/audit-attestations", label: "Verify attestation", paths: ["/audit-attestations"] },
  { href: "/benchmark-audit", label: "Curator access", paths: ["/benchmark-audit"] },
  { href: "/model-submissions", label: "Submit model", paths: ["/model-submissions"] },
];

export function AppNavigation() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);

  function closeFromKeyboard(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && open) {
      setOpen(false);
      toggle.current?.focus();
    }
  }

  return (
    <div className="header-navigation">
      <button ref={toggle} className="nav-toggle" type="button" aria-controls="primary-navigation" aria-expanded={open} onKeyDown={closeFromKeyboard} onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
          <path d={open ? "M6 6l12 12M6 18L18 6" : "M4 6h16M4 12h16M4 18h16"} />
        </svg>
        Menu
      </button>
      <nav id="primary-navigation" className="primary-nav" aria-label="Main navigation" data-open={open}>
        {items.map((item) => {
          const active = item.paths.some((path) => pathname === path || pathname.startsWith(`${path}/`));
          const props = {
            href: item.href,
            "aria-current": active ? "page" as const : undefined,
            onClick: () => setOpen(false),
            onKeyDown: closeFromKeyboard,
          };
          return item.anchor
            ? <a key={item.href} {...props}>{item.label}</a>
            : <Link key={item.href} {...props}>{item.label}</Link>;
        })}
      </nav>
    </div>
  );
}
