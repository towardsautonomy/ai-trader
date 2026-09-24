"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { EventRow } from "@/components/EventRow";
import { Panel } from "@/components/ui/Panel";
import { Empty } from "@/components/ui/States";
import { useEventStream } from "@/hooks/useEventStream";
import { apiUrl } from "@/lib/api";
import type { EventLevel } from "@/lib/types";

const LEVELS: EventLevel[] = ["info", "warn", "error", "critical"];
const SHOWN = 400;

const LEVEL_BTN: Record<EventLevel, string> = {
  info: "border-fg/60 text-fg",
  warn: "border-amber text-amber",
  error: "border-danger text-danger",
  critical: "border-danger text-danger",
};

export function EventStream({ className }: { className?: string }) {
  const { events, conn, retries } = useEventStream();
  const [enabled, setEnabled] = useState<Record<EventLevel, boolean>>({ info: true, warn: true, error: true, critical: true });
  const [paused, setPaused] = useState(false);
  const [seenAtPause, setSeenAtPause] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => events.filter((e) => enabled[e.level] ?? true).slice(-SHOWN), [events, enabled]);
  const newestId = shown[shown.length - 1]?.id ?? 0;

  useEffect(() => {
    if (!paused && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [newestId, paused]);

  const missed = paused ? shown.filter((e) => e.id > seenAtPause).length : 0;

  return (
    <Panel className={className}
      title="EVENT STREAM"
      right={
        <>
          {paused ? <span className="text-amber">PAUSED (hover){missed > 0 ? ` +${missed} new` : ""}</span> : <span>auto-scroll</span>}
          <span className="flex gap-1">
            {LEVELS.map((lv) => (
              <button
                key={lv}
                type="button"
                aria-pressed={enabled[lv]}
                onClick={() => setEnabled((cur) => ({ ...cur, [lv]: !cur[lv] }))}
                className={`border px-1 uppercase ${enabled[lv] ? LEVEL_BTN[lv] : "border-line text-faint line-through"}`}
              >
                {lv === "critical" ? "crit" : lv}
              </button>
            ))}
          </span>
          <span className={conn === "live" ? "text-phos" : conn === "connecting" ? "text-amber" : "font-bold text-danger"}>
            {conn === "live" ? "LIVE" : conn === "connecting" ? "CONNECTING" : `DISCONNECTED, retry ${retries}`}
          </span>
        </>
      }
    >
      <div
        ref={box}
        className="h-full overflow-y-auto py-1"
        onMouseEnter={() => {
          setPaused(true);
          setSeenAtPause(newestId);
        }}
        onMouseLeave={() => setPaused(false)}
      >
        {shown.length === 0 ? (
          <Empty>
            {conn === "down"
              ? `event stream disconnected from ${apiUrl()}; reconnecting automatically`
              : events.length === 0
                ? "no events yet"
                : "no events match the level filter"}
          </Empty>
        ) : (
          shown.map((e) => <EventRow key={e.id} e={e} />)
        )}
      </div>
    </Panel>
  );
}
