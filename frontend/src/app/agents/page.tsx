"use client";

import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { Empty, QueryBody } from "@/components/ui/States";
import { useAgents, useLessons } from "@/hooks/queries";
import { DASH, dateTime, num, pct, pnlClass, signedR, upper, usd } from "@/lib/format";
import type { AgentRow, Lesson } from "@/lib/types";

const OFFLINE_MODEL = "offline-heuristic";

function AgentsTable({ rows }: { rows: AgentRow[] }) {
  const totalCost = rows.reduce((sum, a) => sum + a.cost_today, 0);
  const totalCalls = rows.reduce((sum, a) => sum + a.calls_today, 0);
  const totalErrors = rows.reduce((sum, a) => sum + a.errors_today, 0);
  return (
    <div className="overflow-x-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th>agent</th>
            <th>model</th>
            <th className="r" title="Closed trades this agent took a side on">
              scored calls
            </th>
            <th className="r">right</th>
            <th className="r">hit rate</th>
            <th className="r">calls 18h</th>
            <th className="r">cost 18h</th>
            <th className="r">avg latency 18h</th>
            <th className="r">errors 18h</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.agent}>
              <td className="font-bold text-fg">{upper(a.agent)}</td>
              <td className={a.model === OFFLINE_MODEL ? "text-amber" : "text-fg"}>{a.model}</td>
              <td className="r">{a.calls}</td>
              <td className="r">{a.right}</td>
              <td className="r">{a.hit_rate === null ? <span className="text-faint">{DASH}</span> : pct(a.hit_rate * 100)}</td>
              <td className="r">{a.calls_today}</td>
              <td className="r">{usd(a.cost_today, 4)}</td>
              <td className="r">{num(a.avg_latency_ms, 0)} ms</td>
              <td className={`r ${a.errors_today > 0 ? "font-bold text-danger" : "text-dim"}`}>{a.errors_today}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td className="text-dim" colSpan={5}>
              TOTAL
            </td>
            <td className="r">{totalCalls}</td>
            <td className="r">{usd(totalCost, 4)}</td>
            <td />
            <td className={`r ${totalErrors > 0 ? "font-bold text-danger" : "text-dim"}`}>{totalErrors}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function LessonRow({ l }: { l: Lesson }) {
  return (
    <div className="border-b border-line/60 px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-4 text-sm">
        <span className="text-faint">{dateTime(l.ts)}</span>
        <Link className="link font-bold" href={`/positions/${l.position_id}`}>
          {l.symbol}
        </Link>
        <span className="text-dim">{upper(l.vehicle)}</span>
        <span className={`font-bold ${pnlClass(l.outcome_r)}`}>{signedR(l.outcome_r)}</span>
        <span className="text-cyan">{l.tags.map((t) => `#${t}`).join(" ")}</span>
        <span className="ml-auto text-xs text-dim">
          {l.model} | {usd(l.cost_usd, 4)}
        </span>
      </div>
      <div className="mt-1 grid gap-x-6 gap-y-1 text-sm lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)]">
        <div>
          <div className="label">situation</div>
          <div className="break-words text-fg">{l.situation || DASH}</div>
        </div>
        <div>
          <div className="label">what happened</div>
          <div className="break-words text-fg">{l.what_happened || DASH}</div>
        </div>
        <div>
          <div className="label">lesson</div>
          <div className="break-words text-phos">{l.lesson || DASH}</div>
        </div>
      </div>
    </div>
  );
}

export default function AgentsPage() {
  const agents = useAgents();
  const lessons = useLessons();
  return (
    <div className="space-y-2">
      <PageHeader title="agents" />
      <Panel title="AGENT SWARM" right={<span>track record is all-time over closed trades; usage columns are a rolling 18h window, not a calendar day</span>}>
        <QueryBody query={agents} what="agents">
          {(rows) => (rows.length === 0 ? <Empty>no agents configured</Empty> : <AgentsTable rows={rows} />)}
        </QueryBody>
      </Panel>
      <Panel title="LESSONS" right={lessons.data ? <span>{lessons.data.length} most recent, fed back to the agents as worked examples</span> : null}>
        <QueryBody query={lessons} what="lessons">
          {(rows) => (rows.length === 0 ? <Empty>no lessons yet: one is written after each closed trade</Empty> : rows.map((l) => <LessonRow key={l.id} l={l} />))}
        </QueryBody>
      </Panel>
    </div>
  );
}
