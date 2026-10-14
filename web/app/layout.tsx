import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Floodline",
  description: "One road closure, one reroute, explained from a FalkorDB road graph.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
