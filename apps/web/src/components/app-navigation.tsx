"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState, type KeyboardEvent } from "react";

const primaryItems = [
  { href: "/leaderboard", label: "Leaderboard", paths: ["/leaderboard", "/models"] },
  { href: "/languages", label: "Languages", paths: ["/languages"] },
  { href: "/compare", label: "Compare", paths: ["/compare"] },
  { href: "/tasks", label: "Tasks", paths: ["/tasks", "/scorecards"] },
  { href: "/audit-reports", label: "Audit reports", paths: ["/audit-reports"] },
  { href: "/audit-attestations", label: "Verify attestation", paths: ["/audit-attestations"] },
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
        {primaryItems.map((item) => {
          const active = item.paths.some((path) => pathname === path || pathname.startsWith(`${path}/`));
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              onClick={() => setOpen(false)}
              onKeyDown={closeFromKeyboard}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
