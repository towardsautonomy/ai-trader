"use client";

import Link from "next/link";
import { useState } from "react";
import { LEVEL_CLASS, LEVEL_TAG, clock, dateTime } from "@/lib/format";
import type { TraderEvent } from "@/lib/types";

/** One log line. Events tied to a position or decision link into the matching trace. */
const LONG = 160;

export function EventRow({ e, fullDate = false }: { e: TraderEvent; fullDate?: boolean }) {
  const tone = LEVEL_CLASS[e.level] ?? "text-fg";
  const [open, setOpen] = useState(false);
  const long = e.message.length > LONG;
  const primary = e.position_id ? `/positions/${e.position_id}` : e.decision_id ? `/decisions/${e.decision_id}` : null;
  return (
    <div className="flex gap-2 px-2 py-px text-sm leading-[18px] hover:bg-raised">
      <span className="shrink-0 text-faint">{fullDate ? dateTime(e.ts) : clock(e.ts)}</span>
      <span className={`shrink-0 whitespace-pre ${tone}`}>{LEVEL_TAG[e.level] ?? e.level}</span>
      <span className="w-36 shrink-0 truncate text-dim" title={e.kind}>
        {e.kind}
      </span>
      <span className={`min-w-0 flex-1 break-words ${tone} ${long && !open ? "line-clamp-2" : ""}`}>
        {primary ? (
          <Link href={primary} className="hover:underline">
            {e.message}
          </Link>
        ) : (
          e.message
        )}
      </span>
      {long ? (
        <button type="button" className="shrink-0 self-start text-xs text-faint hover:text-fg" onClick={() => setOpen(!open)} title={open ? "collapse" : "show the whole message"}>
          {open ? "less" : "more"}
        </button>
      ) : null}
      <span className="flex shrink-0 gap-2 text-xs">
        {e.decision_id ? (
          <Link href={`/decisions/${e.decision_id}`} className="link" title={`decision ${e.decision_id}`}>
            dec
          </Link>
        ) : null}
        {e.position_id ? (
          <Link href={`/positions/${e.position_id}`} className="link" title={`position ${e.position_id}`}>
            pos
          </Link>
        ) : null}
      </span>
    </div>
  );
}
