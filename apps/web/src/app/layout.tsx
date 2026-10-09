import type { Metadata } from "next";
import "./globals.css";
import { AppHeader } from "@/components/public-ui";

export const metadata: Metadata = {
  title: {
    default: "PolyCodeBench · Public results",
    template: "%s · PolyCodeBench",
  },
  description: "Explore reviewed, release-backed PolyCodeBench projections.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">Skip to content</a>
        <AppHeader />
        <main id="main-content" className="page-shell" tabIndex={-1}>{children}</main>
        <footer className="site-footer">
          <span>PolyCodeBench public release explorer</span>
          <span>Every result is tied to a versioned release.</span>
        </footer>
      </body>
    </html>
  );
}
