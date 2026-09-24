"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, errorText, isUnreachable } from "@/lib/api";
import { keys, useStatus } from "@/hooks/queries";
import { useNotices } from "@/hooks/useNotices";
import type { KillLevel, KillState, Status } from "@/lib/types";

const FLATTEN_PHRASE = "FLATTEN";
const DEFAULT_REASON = "manual from terminal";

const STATE_TEXT: Record<KillState, { label: string; tone: string }> = {
  armed: { label: "ARMED", tone: "text-phos" },
  halted: { label: "HALTED", tone: "text-danger" },
  flatten: { label: "FLATTEN", tone: "text-danger" },
};

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
}

/**
 * Always-visible kill switch. HALT needs a click in the header and a second
 * click in the panel; FLATTEN additionally needs the word FLATTEN typed.
 * Shift+K opens the panel, Escape closes it.
 */
export function KillSwitch() {
  const status = useStatus();
  const qc = useQueryClient();
  const { notify } = useNotices();
  const [open, setOpen] = useState<KillLevel | null>(null);
  const [reason, setReason] = useState(DEFAULT_REASON);
  const [phrase, setPhrase] = useState("");
  const phraseRef = useRef<HTMLInputElement>(null);
  const haltRef = useRef<HTMLButtonElement>(null);

  const state = status.data?.kill.state;
  const unreachable = isUnreachable(status.error);

  const close = () => {
    setOpen(null);
    setPhrase("");
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(null);
        setPhrase("");
      } else if (e.shiftKey && e.key.toLowerCase() === "k" && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(e.target)) {
        e.preventDefault();
        setOpen((cur) => cur ?? "halt");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open === "flatten") phraseRef.current?.focus();
    if (open === "halt") haltRef.current?.focus();
  }, [open]);

  const trip = useMutation({
    mutationFn: (level: KillLevel) => api.kill(level, reason.trim() || DEFAULT_REASON),
    onSuccess: (res, level) => {
      qc.setQueryData<Status>(keys.status, (prev) => (prev ? { ...prev, kill: res.kill } : prev));
      void qc.invalidateQueries({ queryKey: keys.status });
      const now = res.kill.state.toUpperCase();
      notify(
        "ok",
        res.changed
          ? `KILL SWITCH ${level.toUpperCase()} ACCEPTED: state is now ${now}`
          : `KILL SWITCH ${level.toUpperCase()}: no change, state was already ${now}`,
      );
      close();
    },
    onError: (e, level) => {
      notify(
        "fail",
        `KILL SWITCH ${level.toUpperCase()} FAILED: ${errorText(e)}. Fallback: run \`./ait kill${level === "flatten" ? " --flatten" : ""}\` in the backend directory.`,
      );
    },
  });

  const haltDone = state === "halted" || state === "flatten";
  const flattenDone = state === "flatten";
  const stateText = state ? STATE_TEXT[state] : null;

  return (
    <div className="relative flex items-center gap-2">
      <span className="hidden whitespace-nowrap text-2xs uppercase tracking-wider text-dim xl:inline" title="Shift+K opens the kill switch">
        kill switch
      </span>
      <span className={`whitespace-nowrap text-xs font-bold tracking-wider ${stateText && !status.error ? stateText.tone : "text-amber"}`}>
        {stateText ? `${stateText.label}${status.error ? " (STALE)" : ""}` : unreachable ? "UNKNOWN" : "..."}
      </span>
      <button type="button" className="btn btn-amber" onClick={() => setOpen(open === "halt" ? null : "halt")} aria-expanded={open === "halt"}>
        HALT
      </button>
      <button type="button" className="btn btn-danger" onClick={() => setOpen(open === "flatten" ? null : "flatten")} aria-expanded={open === "flatten"}>
        FLATTEN
      </button>

      {open ? (
        <div role="dialog" aria-label="Kill switch" className="absolute right-0 top-full z-50 mt-2 w-[440px] border border-danger bg-bg shadow-[0_0_0_4px_rgba(5,8,7,0.9)]">
          <div className="flex items-center justify-between border-b border-danger/60 px-2 py-1">
            <span className="text-xs uppercase tracking-[0.18em] text-danger">[ KILL SWITCH ]</span>
            <button type="button" className="text-xs text-dim hover:text-fg" onClick={close}>
              ESC close
            </button>
          </div>

          <div className="space-y-3 p-3 text-sm">
            {unreachable ? (
              <div className="border border-danger bg-danger/10 p-2 text-danger">
                <div className="font-bold tracking-wider">BACKEND UNREACHABLE</div>
                <div className="mt-1 text-fg">
                  The buttons below will most likely fail. Use the fallback, which works even when the server is down. In the backend directory run:
                </div>
                <pre className="mt-1 select-all border border-line bg-panel px-2 py-1 text-amber">./ait kill --flatten</pre>
              </div>
            ) : null}

            <label className="block">
              <span className="label">reason (recorded with the event)</span>
              <input className="input mt-0.5 w-full" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} />
            </label>

            <div className={`border p-2 ${open === "halt" ? "border-amber" : "border-line"}`}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-bold tracking-wider text-amber">HALT</span>
                <span className="text-xs text-dim">stop new entries; open positions keep their stops and targets</span>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <button ref={haltRef} type="button" className="btn btn-amber" disabled={trip.isPending || haltDone} onClick={() => trip.mutate("halt")}>
                  {trip.isPending && trip.variables === "halt" ? "SENDING..." : "CONFIRM HALT"}
                </button>
                {haltDone ? <span className="text-xs text-dim">already {state?.toUpperCase()}</span> : null}
              </div>
            </div>

            <div className={`border p-2 ${open === "flatten" ? "border-danger" : "border-line"}`}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-bold tracking-wider text-danger">FLATTEN</span>
                <span className="text-xs text-dim">halt and sell every open position now</span>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <input
                  ref={phraseRef}
                  className="input w-36"
                  placeholder={`type ${FLATTEN_PHRASE}`}
                  value={phrase}
                  onChange={(e) => setPhrase(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && phrase === FLATTEN_PHRASE && !trip.isPending && !flattenDone) trip.mutate("flatten");
                  }}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={flattenDone}
                />
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={phrase !== FLATTEN_PHRASE || trip.isPending || flattenDone}
                  onClick={() => trip.mutate("flatten")}
                >
                  {trip.isPending && trip.variables === "flatten" ? "SENDING..." : "EXECUTE FLATTEN"}
                </button>
                {flattenDone ? <span className="text-xs text-dim">already FLATTEN</span> : null}
              </div>
            </div>

            {trip.isError ? <div className="break-words text-xs text-danger">last attempt failed: {errorText(trip.error)}</div> : null}
            <div className="text-2xs text-faint">Shift+K opens this panel. Re-arming is done from the red banner and requires typing REARM.</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
