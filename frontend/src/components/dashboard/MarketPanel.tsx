"use client";

import { KV } from "@/components/ui/KV";
import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import { useStatus } from "@/hooks/queries";
import { RegimeBlock } from "@/components/RegimeBlock";
import { DASH, clock, num } from "@/lib/format";

export function MarketPanel({ className }: { className?: string }) {
  const status = useStatus();
  return (
    <Panel className={className} title="MARKET" bodyClassName="overflow-y-auto">
      <QueryBody query={status} what="market state">
        {(s) => {
          const c = s.last_cycle;
          return (
            <div className="space-y-2 p-3">
              <RegimeBlock regime={s.regime} />
              <KV k="scout market note">{s.market_note || DASH}</KV>
              <div className="border-t border-line pt-2">
                <div className="label mb-1">last cycle</div>
                {c && c.ts ? (
                  <div className="text-sm">
                    <div className="flex flex-wrap gap-x-4 text-fg">
                      <span>{clock(c.ts)}</span>
                      <span>
                        <span className="text-dim">scanned </span>
                        {c.scanned ?? DASH}
                      </span>
                      <span>
                        <span className="text-dim">entered </span>
                        <span className={c.entered ? "text-phos" : "text-fg"}>{c.entered ?? DASH}</span>
                      </span>
                      <span>
                        <span className="text-dim">took </span>
                        {c.seconds === undefined ? DASH : `${num(c.seconds, 1)}s`}
                      </span>
                    </div>
                    <div className="break-words">
                      <span className="text-dim">picked </span>
                      {c.picked && c.picked.length > 0 ? c.picked.join(" ") : "(none)"}
                    </div>
                  </div>
                ) : (
                  <div className="text-sm text-faint">no scan cycle has completed yet</div>
                )}
              </div>
            </div>
          );
        }}
      </QueryBody>
    </Panel>
  );
}
