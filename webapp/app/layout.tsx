import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAG Files UI",
  description: "Secure interface for the kcgmhrt RAG backend",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
