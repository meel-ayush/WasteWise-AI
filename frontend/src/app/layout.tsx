import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WasteWise AI — Reduce Food Waste",
  description: "AI-powered food waste reduction for indian SME restaurants",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
