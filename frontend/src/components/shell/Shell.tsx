"use client";

import { useEffect, useState, type ReactNode } from "react";
import { apiUrl, isUnreachable } from "@/lib/api";
import { useStatus } from "@/hooks/queries";
import { EngineBanner } from "./EngineBanner";
import { Header } from "./Header";
import { KillBanner } from "./KillBanner";
import { NoticeStrip } from "./NoticeStrip";

const SCANLINE_KEY = "ait.scanlines";

function BackendDownBanner() {
  const status = useStatus();
  if (!isUnreachable(status.error)) return null;
  return (
    <div role="alert" className="border-b-2 border-danger bg-danger/20 px-3 py-1.5 text-sm">
      <span className="font-bold tracking-wider text-danger">BACKEND UNREACHABLE</span>
      <span className="ml-3 text-fg">
        No answer from {apiUrl()}. Nothing on this screen is live{status.data ? "; values shown are the last ones received" : ""}. The engine may still be
        trading. Emergency stop from a shell in the backend directory:
      </span>
      <code className="ml-2 select-all whitespace-nowrap border border-line bg-panel px-1.5 text-amber">./ait kill --flatten</code>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [scanlines, setScanlines] = useState(true);

  useEffect(() => {
    setScanlines(window.localStorage.getItem(SCANLINE_KEY) !== "off");
  }, []);

  const toggle = () => {
    setScanlines((cur) => {
      window.localStorage.setItem(SCANLINE_KEY, cur ? "off" : "on");
      return !cur;
    });
  };

  return (
    <div className="flex min-h-screen flex-col">
      <div className="sticky top-0 z-40 bg-bg">
        <Header scanlines={scanlines} onToggleScanlines={toggle} />
        <BackendDownBanner />
        <EngineBanner />
        <KillBanner />
        <NoticeStrip />
      </div>
      <main className="flex min-h-0 flex-1 flex-col p-2">{children}</main>
      {scanlines ? <div className="scanlines" aria-hidden /> : null}
    </div>
  );
}
