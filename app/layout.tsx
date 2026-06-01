import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Clinical Trial AI Reproduction Benchmark — Intake",
  description: "Intake form for trial statisticians assessing AI-reproduced trial designs.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen">
          <header className="border-b border-slate-200 bg-white">
            <div className="mx-auto max-w-4xl px-6 py-4">
              <h1 className="text-lg font-semibold">Clinical Trial AI Reproduction Benchmark</h1>
              <p className="text-xs text-slate-500">Statistician intake & evaluation form</p>
            </div>
          </header>
          <main className="mx-auto max-w-4xl px-6 py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
