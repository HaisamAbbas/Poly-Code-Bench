import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PolyCodeBench",
  description: "A reproducible evaluation framework for coding systems.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
