"use client";

import Link from "next/link";
import { Panel } from "@/components/ui/Panel";
import { Empty, QueryBody } from "@/components/ui/States";
import { useHistory } from "@/hooks/queries";
import { DASH, dateTime, num, pnlClass, signedR, signedUsd, upper } from "@/lib/format";
import type { HistoryTrade } from "@/lib/types";

/** Options fill at fractions of a cent in paper; show enough digits that entry x exit x qty adds up. */
function px(t: HistoryTrade, v: number | null): string {
  if (v === null || v === undefined) return DASH;
  return t.is_option ? v.toFixed(4) : v.toFixed(2);
}

function hold(m: number | null): string {
  if (m === null) return DASH;
  return m < 60 ? `${Math.round(m)}m` : `${Math.floor(m / 60)}h${String(Math.round(m % 60)).padStart(2, "0")}`;
}

export default function HistoryPage() {
  const q = useHistory();
  return (
    <QueryBody query={q} what="trade history">
      {(h) => (
        <div className="grid grid-cols-12 gap-2">
          <Panel title="TOTALS" className="col-span-12" right={<span>{upper(h.mode)} trades, all time</span>}>
            <div className="flex flex-wrap gap-x-10 gap-y-2 px-3 py-2 text-sm">
              <div>
                <div className="label">realized p&l</div>
                <div className={`text-lg font-bold ${pnlClass(h.totals.pnl)}`}>{signedUsd(h.totals.pnl)}</div>
              </div>
              <div>
                <div className="label">trades</div>
                <div className="text-lg">{h.totals.trades}</div>
              </div>
              <div>
                <div className="label">win rate</div>
                <div className="text-lg">{h.totals.trades ? `${num((100 * h.totals.wins) / h.totals.trades, 0)}%` : DASH}</div>
              </div>
              <div>
                <div className="label">sessions traded</div>
                <div className="text-lg">{h.days.length}</div>
              </div>
            </div>
          </Panel>

          <Panel title="BY SESSION" className="col-span-12 xl:col-span-4" bodyClassName="overflow-x-auto">
            {h.days.length === 0 ? (
              <Empty>no closed trades yet</Empty>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="label text-left">
                    <th className="px-3 py-1 font-normal">session</th>
                    <th className="px-2 py-1 text-right font-normal">trades</th>
                    <th className="px-2 py-1 text-right font-normal">won</th>
                    <th className="px-2 py-1 text-right font-normal">p&l</th>
                    <th className="px-3 py-1 text-right font-normal">cumulative</th>
                  </tr>
                </thead>
                <tbody>
                  {h.days.map((d) => (
                    <tr key={d.day} className="border-t border-line/60">
                      <td className="px-3 py-1 text-fg">{d.day}</td>
                      <td className="px-2 py-1 text-right">{d.trades}</td>
                      <td className="px-2 py-1 text-right text-dim">{d.wins}</td>
                      <td className={`px-2 py-1 text-right font-bold ${pnlClass(d.pnl)}`}>{signedUsd(d.pnl)}</td>
                      <td className={`px-3 py-1 text-right ${pnlClass(d.cumulative)}`}>{signedUsd(d.cumulative)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="CLOSED TRADES" className="col-span-12 xl:col-span-8" bodyClassName="overflow-x-auto" right={<span>newest first; click a row for its full trace</span>}>
            {h.trades.length === 0 ? (
              <Empty>no closed trades yet</Empty>
            ) : (
              <table className="w-full whitespace-nowrap text-sm">
                <thead>
                  <tr className="label text-left">
                    <th className="px-3 py-1 font-normal">closed</th>
                    <th className="px-2 py-1 font-normal">instrument</th>
                    <th className="px-2 py-1 font-normal">side</th>
                    <th className="px-2 py-1 text-right font-normal">qty</th>
                    <th className="px-2 py-1 text-right font-normal">entry</th>
                    <th className="px-2 py-1 text-right font-normal">exit</th>
                    <th className="px-2 py-1 text-right font-normal">held</th>
                    <th className="px-2 py-1 text-right font-normal">p&l</th>
                    <th className="px-2 py-1 text-right font-normal">R</th>
                    <th className="px-3 py-1 font-normal">why it closed</th>
                  </tr>
                </thead>
                <tbody>
                  {h.trades.map((t) => (
                    <tr key={t.id} className="border-t border-line/60 hover:bg-raised">
                      <td className="px-3 py-1 text-dim">
                        <Link href={`/positions/${t.id}`} className="hover:underline">
                          {dateTime(t.exit_ts)}
                        </Link>
                      </td>
                      <td className="px-2 py-1">
                        <Link href={`/positions/${t.id}`} className="font-bold text-fg hover:underline">
                          {t.instrument_key}
                        </Link>
                      </td>
                      <td className="px-2 py-1 text-dim">{t.is_option ? (t.instrument.right === "put" ? "long put" : "long call") : "long shares"}</td>
                      <td className="px-2 py-1 text-right">{t.qty}</td>
                      <td className="px-2 py-1 text-right">{px(t, t.entry_price)}</td>
                      <td className="px-2 py-1 text-right">{px(t, t.exit_price)}</td>
                      <td className="px-2 py-1 text-right text-dim">{hold(t.hold_minutes)}</td>
                      <td className={`px-2 py-1 text-right font-bold ${pnlClass(t.realized_pnl)}`} title={t.fees ? `after ${signedUsd(-t.fees)} fees` : ""}>
                        {signedUsd(t.realized_pnl)}
                      </td>
                      <td className={`px-2 py-1 text-right ${pnlClass(t.pnl_r)}`}>{signedR(t.pnl_r)}</td>
                      <td className="max-w-[280px] truncate px-3 py-1 text-dim" title={t.exit_detail ?? ""}>
                        {t.exit_reason?.replace(/_/g, " ")}
                        {t.exit_detail ? `: ${t.exit_detail}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>
      )}
    </QueryBody>
  );
}
