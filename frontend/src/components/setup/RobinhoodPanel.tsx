"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import { keys, useRobinhood } from "@/hooks/queries";
import { api } from "@/lib/api";
import { dateTime, usd } from "@/lib/format";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 border-t border-line/60 px-3 py-1">
      <span className="label w-32 shrink-0 pt-0.5">{label}</span>
      <span className="min-w-0 flex-1 break-words text-sm">{children}</span>
    </div>
  );
}

const yes = (ok: boolean, y: string, n: string) => <span className={ok ? "text-phos" : "text-amber"}>{ok ? y : n}</span>;

export function RobinhoodPanel({ className = "" }: { className?: string }) {
  const q = useRobinhood();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    setBusy(true);
    try {
      qc.setQueryData(keys.robinhood, await api.robinhood(true));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title="ROBINHOOD"
      className={className}
      right={
        <button type="button" className="btn" disabled={busy} onClick={() => void refresh()}>
          {busy ? "CHECKING..." : "REFRESH"}
        </button>
      }
    >
      <QueryBody query={q} what="Robinhood">
        {(r) => (
          <div className="pb-1">
            <Row label="login">{yes(r.logged_in, "logged in", "not logged in: scripts/trader robinhood login")}</Row>
            <Row label="adapter">{r.verified_at ? <span className="text-phos">verified {dateTime(r.verified_at)}</span> : <span className="text-amber">not verified: scripts/trader robinhood verify</span>}</Row>
            <Row label="tool schemas">
              {r.schemas_current === null ? <span className="text-dim">not checked</span> : yes(r.schemas_current, "unchanged since verify", "CHANGED since verify: re-run verify")}
            </Row>
            <Row label="market data">
              {r.in_use ? <span className="font-bold text-phos">FROM ROBINHOOD</span> : <span className="text-amber">not from Robinhood (see status bar)</span>}
            </Row>
            <Row label="orders go to">
              {r.broker_in_use ? <span className="font-bold text-danger">ROBINHOOD (REAL MONEY)</span> : <span className="text-phos">paper broker (simulated fills)</span>}
            </Row>
            <Row label="agentic account">
              {r.account ? (
                <span>
                  ...{r.account.last4} <span className="text-dim">{r.account.type}</span>{" "}
                  <span className={r.account.options_ok ? "text-phos" : "text-amber"}>{r.account.option_level || "no options"}</span>
                </span>
              ) : (
                <span className="text-dim">{r.logged_in ? "unknown" : "--"}</span>
              )}
            </Row>
            <Row label="balance">
              {r.balance ? (
                <span>
                  equity <span className="text-fg">{usd(r.balance.equity)}</span> <span className="text-dim">cash</span> {usd(r.balance.cash)}{" "}
                  <span className="text-dim">buying power</span> {usd(r.balance.buying_power)}
                  {r.balance.equity <= 0 ? <span className="text-amber"> (unfunded)</span> : null}
                </span>
              ) : (
                <span className="text-dim">--</span>
              )}
            </Row>
            <Row label="held there">
              {r.positions === null ? <span className="text-dim">--</span> : r.positions.length === 0 ? <span className="text-dim">nothing</span> : r.positions.map((p) => `${p.key} x${p.qty}`).join(", ")}
            </Row>
            {r.error ? (
              <Row label="error">
                <span className="text-danger">{r.error}</span>
              </Row>
            ) : null}
            <div className="px-3 pt-1 text-2xs text-faint">checked {new Date(r.checked_at * 1000).toLocaleTimeString()}; refreshes every 60 s</div>
          </div>
        )}
      </QueryBody>
    </Panel>
  );
}
