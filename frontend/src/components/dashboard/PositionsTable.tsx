"use client";

import { useRouter } from "next/navigation";
import { CloseControl } from "@/components/CloseControl";
import { Panel } from "@/components/ui/Panel";
import { Empty, QueryBody } from "@/components/ui/States";
import { useOpenPositions } from "@/hooks/queries";
import { useNow } from "@/hooks/useNow";
import { DASH, instrumentLabel, minutesBetween, num, pnlClass, signedPct, signedR, signedUsd, truncate } from "@/lib/format";
import type { Position } from "@/lib/types";

function Row({ p, now }: { p: Position; now: number | null }) {
  const router = useRouter();
  // Every position is long its own instrument (shares, calls or puts), so on the instrument's price the stop is below and the target above.
  const toStop = p.last_price ? ((p.stop_price - p.last_price) / p.last_price) * 100 : null;
  const toTarget = p.last_price ? ((p.target_price - p.last_price) / p.last_price) * 100 : null;
  const held = now === null ? null : minutesBetween(p.entry_ts, now);
  const holdTone = held !== null && held >= p.max_hold_minutes * 0.85 ? "text-amber" : "text-fg";

  return (
    <tr className="click" onClick={() => router.push(`/positions/${p.id}`)}>
      <td className="font-bold text-fg">{instrumentLabel(p.instrument, p.instrument_key)}</td>
      <td className={p.direction > 0 ? "text-phos" : "text-danger"}>{p.direction > 0 ? "LONG" : "SHORT"}</td>
      <td className="r">{num(p.qty, 0)}</td>
      <td className="r">{num(p.entry_price)}</td>
      <td className="r font-bold">{num(p.last_price)}</td>
      <td className="r text-danger">{num(p.stop_price)}</td>
      <td className="r text-dim">{signedPct(toStop)}</td>
      <td className="r text-phos">{num(p.target_price)}</td>
      <td className="r text-dim">{signedPct(toTarget)}</td>
      <td className={`r font-bold ${pnlClass(p.unrealized_pnl)}`}>{signedUsd(p.unrealized_pnl)}</td>
      <td className={`r ${pnlClass(p.pnl_r)}`}>{signedR(p.pnl_r)}</td>
      <td className={`r ${holdTone}`}>
        {held ?? DASH}
        <span className="text-dim"> / {p.max_hold_minutes}m</span>
      </td>
      <td className="r" onClick={(e) => e.stopPropagation()}>
        <CloseControl p={p} />
      </td>
      <td className="max-w-[280px] truncate text-dim" title={p.thesis}>
        {truncate(p.thesis, 80)}
      </td>
    </tr>
  );
}

export function PositionsTable({ className }: { className?: string }) {
  const positions = useOpenPositions();
  const now = useNow(15000);
  const total = positions.data?.reduce((sum, p) => sum + p.unrealized_pnl, 0);

  return (
    <Panel className={className}
      title="OPEN POSITIONS"
      right={
        positions.data ? (
          <>
            <span>{positions.data.length} open</span>
            <span>
              unrealized <span className={pnlClass(total)}>{signedUsd(total)}</span>
            </span>
          </>
        ) : null
      }
      bodyClassName="overflow-auto"
    >
      <QueryBody query={positions} what="positions">
        {(rows) =>
          rows.length === 0 ? (
            <Empty>no open positions</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>instrument</th>
                  <th title="Direction of the thesis on the underlying">dir</th>
                  <th className="r">qty</th>
                  <th className="r">entry</th>
                  <th className="r">last</th>
                  <th className="r">stop</th>
                  <th className="r">to stop</th>
                  <th className="r">target</th>
                  <th className="r">to tgt</th>
                  <th className="r">unrl $</th>
                  <th className="r">R</th>
                  <th className="r">held / max</th>
                  <th className="r">manual</th>
                  <th>thesis</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <Row key={p.id} p={p} now={now} />
                ))}
              </tbody>
            </table>
          )
        }
      </QueryBody>
    </Panel>
  );
}
