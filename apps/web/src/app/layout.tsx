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
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <AppHeader />
        <main id="main-content" className="page-shell">{children}</main>
        <footer className="site-footer">
          <span>PolyCodeBench public release explorer</span>
          <span>Every result is tied to a versioned release.</span>
        </footer>
      </body>
    </html>
  );
}
