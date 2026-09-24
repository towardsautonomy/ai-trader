"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type NoticeTone = "ok" | "fail";

export interface Notice {
  id: number;
  tone: NoticeTone;
  text: string;
  at: number;
}

interface NoticeValue {
  notices: Notice[];
  notify: (tone: NoticeTone, text: string) => void;
  dismiss: (id: number) => void;
}

const OK_TTL_MS = 20000;
const MAX_NOTICES = 4;

const Ctx = createContext<NoticeValue>({ notices: [], notify: () => undefined, dismiss: () => undefined });

/** Outcome of every mutation (kill, re-arm, manual close). Failures stay until dismissed; successes expire. */
export function NoticeProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);

  const notify = useCallback((tone: NoticeTone, text: string) => {
    setNotices((prev) => [...prev, { id: Date.now() + Math.random(), tone, text, at: Date.now() }].slice(-MAX_NOTICES));
  }, []);

  const dismiss = useCallback((id: number) => setNotices((prev) => prev.filter((n) => n.id !== id)), []);

  useEffect(() => {
    if (!notices.some((n) => n.tone === "ok")) return;
    const t = setInterval(() => {
      const cutoff = Date.now() - OK_TTL_MS;
      setNotices((prev) => prev.filter((n) => n.tone === "fail" || n.at > cutoff));
    }, 1000);
    return () => clearInterval(t);
  }, [notices]);

  const value = useMemo(() => ({ notices, notify, dismiss }), [notices, notify, dismiss]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useNotices(): NoticeValue {
  return useContext(Ctx);
}
