"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { keys, useReadiness, useStatus } from "@/hooks/queries";
import { useNotices } from "@/hooks/useNotices";
import { api, errorText } from "@/lib/api";
import type { Mode } from "@/lib/types";

const PHRASE = "I-ACCEPT-REAL-MONEY-RISK";

function Current({ mode, demo, source }: { mode: Mode; demo: boolean; source: string }) {
  if (demo) {
    return (
      <div className="border border-amber bg-amber/10 p-3">
        <div className="text-lg font-bold tracking-[0.2em] text-amber">DEMO</div>
        <div className="mt-1 text-sm text-fg">
          Synthetic market, clock ignored, separate demo data. Nothing here is real and no mode switch is possible. For paper trading on real Robinhood
          data run:
        </div>
        <pre className="mt-1 select-all border border-line bg-panel px-2 py-1 text-amber">scripts/trader restart --data robinhood --llm local/qwen3.8:27b-q8-16k</pre>
      </div>
    );
  }
  return mode === "live" ? (
    <div className="border border-danger bg-danger/10 p-3">
      <div className="text-lg font-bold tracking-[0.2em] text-danger">LIVE: REAL MONEY</div>
      <div className="mt-1 text-sm text-fg">Orders go to your Robinhood Agentic account. Chosen {source === "dashboard" ? "on this page" : "in backend/.env"}.</div>
    </div>
  ) : (
    <div className="border border-phos bg-phos/5 p-3">
      <div className="text-lg font-bold tracking-[0.2em] text-phos">PAPER</div>
      <div className="mt-1 text-sm text-fg">Simulated fills against real quotes; no order reaches a broker. Chosen {source === "dashboard" ? "on this page" : "by default / backend/.env"}.</div>
    </div>
  );
}

export function ModePanel({ className = "" }: { className?: string }) {
  const status = useStatus();
  const readiness = useReadiness();
  const qc = useQueryClient();
  const { notify } = useNotices();
  const [phrase, setPhrase] = useState("");
  const [code, setCode] = useState("");
  const [armed, setArmed] = useState(false);
  const [switching, setSwitching] = useState<Mode | null>(null);

  const s = status.data;
  const ready = readiness.data?.ready ?? false;

  const change = useMutation({
    mutationFn: (to: Mode) => api.setMode(to, phrase, code),
    onSuccess: (res) => {
      setSwitching(res.switching_to);
      setPhrase("");
      setCode("");
      setArmed(false);
      notify("ok", `Switching to ${res.switching_to.toUpperCase()}: the engine is restarting. The dashboard reconnects in a few seconds.`);
      const started = Date.now();
      const poll = window.setInterval(() => {
        void qc.invalidateQueries();
        const now = qc.getQueryData<{ mode: Mode }>(keys.status);
        if ((now && now.mode === res.switching_to) || Date.now() - started > 90000) {
          window.clearInterval(poll);
          setSwitching(null);
        }
      }, 2000);
    },
    onError: (e) => notify("fail", `Mode switch refused: ${errorText(e)}`),
  });

  return (
    <Panel title="TRADING MODE" className={className}>
      <div className="space-y-3 p-3 text-sm">
        {!s ? <div className="text-dim">loading status...</div> : <Current mode={s.mode} demo={!!s.demo} source={s.mode_source ?? "env"} />}

        {s?.live_refused ? (
          <div className="border border-danger bg-danger/10 p-2">
            <div className="font-bold tracking-wider text-danger">LAST SWITCH TO LIVE WAS REFUSED AT STARTUP</div>
            <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-fg">{s.live_refused}</pre>
            <div className="mt-1 text-xs text-dim">The engine fell back to paper. Fix the items in LIVE READINESS, then switch again.</div>
          </div>
        ) : null}

        {switching ? (
          <div className="border border-amber p-2 text-amber">Restarting into {switching.toUpperCase()}... every startup gate runs again.</div>
        ) : null}

        {s && !s.demo && s.mode === "paper" ? (
          <div className={`border p-3 ${armed ? "border-danger" : "border-line"}`}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-bold tracking-wider text-danger">SWITCH TO LIVE (REAL MONEY)</span>
              {!armed ? (
                <button type="button" className="btn btn-danger" disabled={!ready} onClick={() => setArmed(true)} title={ready ? "" : "every blocking readiness check must pass first"}>
                  {ready ? "BEGIN" : "NOT READY"}
                </button>
              ) : (
                <button type="button" className="text-xs text-dim hover:text-fg" onClick={() => setArmed(false)}>
                  cancel
                </button>
              )}
            </div>
            {!ready ? <div className="mt-1 text-xs text-dim">Available once every blocking check in LIVE READINESS passes.</div> : null}
            {armed ? (
              <ol className="mt-2 list-decimal space-y-2 pl-5">
                <li>
                  On the server, run <code className="select-all text-amber">scripts/trader live-code</code>. It prints a 6-digit code valid for 10 minutes. This proves a
                  person with shell access is doing this; the dashboard token alone cannot.
                  <input
                    className="input mt-1 block w-32 tracking-[0.3em]"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="000000"
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                    autoComplete="off"
                  />
                </li>
                <li>
                  Type <code className="text-danger">{PHRASE}</code>
                  <input className="input mt-1 block w-72" value={phrase} onChange={(e) => setPhrase(e.target.value)} autoComplete="off" spellCheck={false} />
                </li>
                <li>
                  The engine restarts in live mode. Entries start at the next trading window; the kill switch works as before.
                  <div className="mt-1">
                    <button
                      type="button"
                      className="btn btn-danger"
                      disabled={code.length !== 6 || phrase !== PHRASE || change.isPending}
                      onClick={() => change.mutate("live")}
                    >
                      {change.isPending ? "SWITCHING..." : "GO LIVE"}
                    </button>
                  </div>
                </li>
              </ol>
            ) : null}
          </div>
        ) : null}

        {s && !s.demo && s.mode === "live" ? (
          <div className="border border-line p-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-bold tracking-wider text-phos">SWITCH BACK TO PAPER</span>
              <button type="button" className="btn" disabled={change.isPending} onClick={() => change.mutate("paper")}>
                {change.isPending ? "SWITCHING..." : "GO PAPER"}
              </button>
            </div>
            <div className="mt-1 text-xs text-dim">Refused while live positions are open: close them or FLATTEN first, so nothing real is left unmanaged.</div>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
