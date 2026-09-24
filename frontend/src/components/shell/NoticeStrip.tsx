"use client";

import { clock } from "@/lib/format";
import { useNotices } from "@/hooks/useNotices";

/** Explicit outcome of every mutation. Failures stay until dismissed. */
export function NoticeStrip() {
  const { notices, dismiss } = useNotices();
  if (notices.length === 0) return null;
  return (
    <div aria-live="assertive">
      {notices.map((n) => (
        <div
          key={n.id}
          className={`flex items-start gap-3 border-b px-3 py-1 text-sm ${
            n.tone === "ok" ? "border-phos/40 bg-phos/10 text-phos" : "border-danger/60 bg-danger/15 text-danger"
          }`}
        >
          <span className="shrink-0 text-dim">{clock(new Date(n.at).toISOString())}</span>
          <span className="shrink-0 font-bold">{n.tone === "ok" ? "OK" : "FAILED"}</span>
          <span className="min-w-0 flex-1 break-words text-fg">{n.text}</span>
          <button type="button" className="shrink-0 text-xs text-dim hover:text-fg" onClick={() => dismiss(n.id)}>
            [dismiss]
          </button>
        </div>
      ))}
    </div>
  );
}
