"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorText } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { keys, useStatus } from "@/hooks/queries";
import { useNotices } from "@/hooks/useNotices";
import type { Status } from "@/lib/types";

const REARM_PHRASE = "REARM";

const MEANING = {
  halted: "NO NEW ENTRIES. Open positions are still managed by their stops and targets.",
  flatten: "NO NEW ENTRIES. Every open position is being sold.",
} as const;

/** Persistent red banner on every page while the kill switch is engaged, with the typed re-arm control. */
export function KillBanner() {
  const status = useStatus();
  const qc = useQueryClient();
  const { notify } = useNotices();
  const [phrase, setPhrase] = useState("");

  const rearm = useMutation({
    mutationFn: () => api.rearm(phrase),
    onSuccess: (res) => {
      qc.setQueryData<Status>(keys.status, (prev) => (prev ? { ...prev, kill: res.kill } : prev));
      void qc.invalidateQueries({ queryKey: keys.status });
      notify("ok", `RE-ARM ACCEPTED: kill switch state is now ${res.kill.state.toUpperCase()}`);
      setPhrase("");
    },
    onError: (e) => notify("fail", `RE-ARM FAILED: ${errorText(e)}`),
  });

  const kill = status.data?.kill;
  if (!kill || kill.state === "armed") return null;

  const ready = phrase === REARM_PHRASE && !rearm.isPending;

  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b-2 border-danger bg-danger/20 px-3 py-1.5 text-sm">
      <div className="font-bold tracking-wider text-danger">
        <span className="animate-blink">!!</span> KILL SWITCH ENGAGED: {kill.state.toUpperCase()}
      </div>
      <div className="text-fg">{MEANING[kill.state]}</div>
      <div className="flex flex-wrap gap-x-4 text-xs text-fg">
        <span>
          <span className="text-dim">reason </span>
          {kill.reason || "(none given)"}
        </span>
        <span>
          <span className="text-dim">source </span>
          {kill.source || "?"}
        </span>
        <span>
          <span className="text-dim">since </span>
          {dateTime(kill.since)}
        </span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <span className="text-2xs uppercase tracking-wider text-dim">re-arm</span>
        <input
          className="input w-32"
          placeholder={`type ${REARM_PHRASE}`}
          value={phrase}
          onChange={(e) => setPhrase(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && ready) rearm.mutate();
          }}
          autoComplete="off"
          spellCheck={false}
          aria-label="Type REARM to re-enable trading"
        />
        <button type="button" className="btn" disabled={!ready} onClick={() => rearm.mutate()}>
          {rearm.isPending ? "SENDING..." : "RE-ARM"}
        </button>
      </div>
    </div>
  );
}
