"use client";

import { KV } from "@/components/ui/KV";
import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import { useStats, useStatus } from "@/hooks/queries";
import { DASH, num, pct, pnlClass, signedR, signedUsd, upper } from "@/lib/format";

export function StatsPanel({ className }: { className?: string }) {
  const stats = useStats();
  const mode = useStatus().data?.mode;
  return (
    <Panel className={className} title="PERFORMANCE" right={<span>all-time, every closed {mode ?? "current-mode"} trade</span>} bodyClassName="overflow-y-auto">
      <QueryBody query={stats} what="stats">
        {(s) => {
          const reasons = Object.entries(s.by_exit_reason).sort((a, b) => b[1].count - a[1].count);
          return (
            <div className="space-y-3 p-3">
              <div className="grid grid-cols-3 gap-3">
                <KV k="trades">
                  {s.trades} <span className="text-dim">({s.wins} won)</span>
                </KV>
                <KV k="win rate">{s.win_rate === null ? DASH : pct(s.win_rate * 100)}</KV>
                <KV k="total p&l">
                  <span className={`font-bold ${pnlClass(s.total_pnl)}`}>{signedUsd(s.total_pnl)}</span>
                </KV>
                <KV k="avg R">
                  <span className={pnlClass(s.avg_r)}>{signedR(s.avg_r)}</span>
                </KV>
                <KV k="profit factor">{s.profit_factor === null ? <span title="needs at least one losing trade">{DASH}</span> : num(s.profit_factor)}</KV>
              </div>
              <div>
                <div className="label mb-1">p&l by exit reason</div>
                {reasons.length === 0 ? (
                  <div className="text-sm text-faint">no closed trades yet</div>
                ) : (
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>exit reason</th>
                        <th className="r">count</th>
                        <th className="r">p&l</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reasons.map(([reason, v]) => (
                        <tr key={reason}>
                          <td>{upper(reason)}</td>
                          <td className="r">{v.count}</td>
                          <td className={`r ${pnlClass(v.pnl)}`}>{signedUsd(v.pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          );
        }}
      </QueryBody>
    </Panel>
  );
}
