import type { Metadata } from "next";
import { JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";
import { Providers } from "@/components/Providers";
import { Shell } from "@/components/shell/Shell";
import "./globals.css";

const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "ai_trader terminal",
  description: "Monitoring and control console for the AI trading engine",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={mono.variable}>
      <body className="font-mono text-base">
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
