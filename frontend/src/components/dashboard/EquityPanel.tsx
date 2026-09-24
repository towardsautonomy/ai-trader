"use client";

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Panel } from "@/components/ui/Panel";
import { Empty, QueryBody } from "@/components/ui/States";
import { useEquity, useStatus } from "@/hooks/queries";
import { clock, dateTime, pnlClass, signedUsd, usd } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

const RANGES = [
  { hours: 6, label: "6H" },
  { hours: 24, label: "24H" },
  { hours: 72, label: "3D" },
  { hours: 168, label: "7D" },
] as const;

const AXIS = { fontSize: 10, fill: "#6f8f7c", fontFamily: "var(--font-mono)" };

interface Pt {
  t: number;
  equity: number;
  day_pnl: number;
  ts: string;
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Pt }[] }) {
  const p = payload?.[0]?.payload;
  if (!active || !p) return null;
  return (
    <div className="border border-line-hi bg-bg px-2 py-1 text-xs">
      <div className="text-dim">{dateTime(p.ts)}</div>
      <div className="text-fg">equity {usd(p.equity)}</div>
      <div className={pnlClass(p.day_pnl)}>day {signedUsd(p.day_pnl)}</div>
    </div>
  );
}

function Chart({ points, hours, dayStart }: { points: EquityPoint[]; hours: number; dayStart: number | undefined }) {
  const data: Pt[] = points.map((p) => ({ t: new Date(p.ts).getTime(), equity: p.equity, day_pnl: p.day_pnl, ts: p.ts }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 10, right: 28, bottom: 0, left: 8 }}>
        <CartesianGrid stroke="#1c2a22" strokeDasharray="2 4" vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(t: number) => (hours > 24 ? dateTime(new Date(t).toISOString()).slice(5, 16) : clock(new Date(t).toISOString()).slice(0, 5))}
          tick={AXIS}
          stroke="#1c2a22"
          tickLine={false}
          minTickGap={48}
        />
        <YAxis
          dataKey="equity"
          domain={["auto", "auto"]}
          tickFormatter={(v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 })}
          tick={AXIS}
          stroke="#1c2a22"
          tickLine={false}
          width={64}
        />
        {dayStart !== undefined ? (
          <ReferenceLine
            y={dayStart}
            stroke="#6f8f7c"
            strokeDasharray="4 4"
            ifOverflow="extendDomain"
            label={{ value: "day start", position: "insideBottomRight", ...AXIS }}
          />
        ) : null}
        <Tooltip content={<Tip />} cursor={{ stroke: "#2f4a3b", strokeWidth: 1 }} isAnimationActive={false} />
        <Line type="stepAfter" dataKey="equity" stroke="#3dff8b" strokeWidth={1.5} dot={false} activeDot={{ r: 3, fill: "#3dff8b", stroke: "#050807", strokeWidth: 2 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function EquityPanel({ className }: { className?: string }) {
  const [hours, setHours] = useState<number>(24);
  const equity = useEquity(hours);
  const status = useStatus();
  const last = equity.data?.[equity.data.length - 1];

  return (
    <Panel className={className}
      title="EQUITY"
      right={
        <>
          {last ? <span className="text-fg">{usd(last.equity)}</span> : null}
          <span className="flex gap-1">
            {RANGES.map((r) => (
              <button
                key={r.hours}
                type="button"
                aria-pressed={hours === r.hours}
                onClick={() => setHours(r.hours)}
                className={`border px-1 ${hours === r.hours ? "border-phos text-phos" : "border-line text-dim hover:text-fg"}`}
              >
                {r.label}
              </button>
            ))}
          </span>
        </>
      }
    >
      <QueryBody query={equity} what="equity curve">
        {(points) =>
          points.length < 2 ? (
            <Empty>
              {points.length === 0 ? "no equity snapshots in this window yet" : "one equity snapshot so far; the curve draws from the second"}
            </Empty>
          ) : (
            <div className="h-full min-h-[180px] w-full">
              <Chart points={points} hours={hours} dayStart={status.data?.account.day_start_equity} />
            </div>
          )
        }
      </QueryBody>
    </Panel>
  );
}
