"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, wsUrl } from "@/lib/api";
import type { TraderEvent } from "@/lib/types";

export type ConnState = "connecting" | "live" | "down";

interface EventStreamValue {
  events: TraderEvent[];
  conn: ConnState;
  /** Consecutive failed connection attempts since the last successful one. */
  retries: number;
}

const MAX_EVENTS = 1500;
const BACKFILL = 300;
const MAX_BACKOFF_MS = 10000;

const Ctx = createContext<EventStreamValue>({ events: [], conn: "connecting", retries: 0 });

function merge(prev: TraderEvent[], incoming: TraderEvent[]): TraderEvent[] {
  if (incoming.length === 0) return prev;
  const byId = new Map<number, TraderEvent>();
  for (const e of prev) byId.set(e.id, e);
  for (const e of incoming) byId.set(e.id, e);
  const out = [...byId.values()].sort((a, b) => a.id - b.id);
  return out.length > MAX_EVENTS ? out.slice(out.length - MAX_EVENTS) : out;
}

function isEvent(v: unknown): v is TraderEvent {
  return typeof v === "object" && v !== null && typeof (v as TraderEvent).id === "number" && typeof (v as TraderEvent).message === "string";
}

/**
 * One WebSocket for the whole app. History is backfilled over REST on every
 * (re)connect and whenever an id gap shows the server dropped frames for a
 * slow consumer, so the feed never silently loses events.
 */
export function EventStreamProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<TraderEvent[]>([]);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [retries, setRetries] = useState(0);
  const buffer = useRef<TraderEvent[]>([]);
  const lastId = useRef(0);

  useEffect(() => {
    let sock: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let disposed = false;

    const accept = (incoming: TraderEvent[]) => {
      if (disposed || incoming.length === 0) return;
      buffer.current = merge(buffer.current, incoming);
      lastId.current = buffer.current[buffer.current.length - 1]?.id ?? 0;
      setEvents(buffer.current);
    };

    /** An id at or below the newest one is either a duplicate of a backfilled row, or proof the backend restarted on a fresh database. */
    const isFromNewDatabase = (e: TraderEvent) => {
      if (e.id > lastId.current) return false;
      const known = buffer.current.find((x) => x.id === e.id);
      return !known || known.ts !== e.ts;
    };

    const backfill = async (after: number) => {
      try {
        accept(await api.events(after > 0 ? { after_id: after, limit: 1000 } : { limit: BACKFILL }));
      } catch {
        // the socket's own close/retry cycle reports the outage
      }
    };

    const connect = () => {
      if (disposed) return;
      setConn("connecting");
      sock = new WebSocket(wsUrl());
      sock.onopen = () => {
        attempts = 0;
        setRetries(0);
        setConn("live");
        void backfill(lastId.current);
      };
      sock.onmessage = (msg) => {
        try {
          const parsed: unknown = JSON.parse(String(msg.data));
          if (!isEvent(parsed)) return;
          const reset = isFromNewDatabase(parsed);
          if (reset) {
            buffer.current = [];
            lastId.current = 0;
          }
          const before = lastId.current;
          accept([parsed]);
          if (reset || (before > 0 && parsed.id > before + 1)) void backfill(before);
        } catch {
          // ignore a malformed frame rather than kill the feed
        }
      };
      sock.onclose = () => {
        if (disposed) return;
        attempts += 1;
        setRetries(attempts);
        setConn("down");
        timer = setTimeout(connect, Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(attempts - 1, 4)));
      };
      sock.onerror = () => sock?.close();
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      if (sock) {
        sock.onclose = null;
        sock.close();
      }
    };
  }, []);

  const value = useMemo(() => ({ events, conn, retries }), [events, conn, retries]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useEventStream(): EventStreamValue {
  return useContext(Ctx);
}
