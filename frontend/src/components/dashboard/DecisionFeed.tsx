"use client";

import Link from "next/link";
import { StanceChips } from "@/components/StanceChips";
import { Panel } from "@/components/ui/Panel";
import { Empty, QueryBody } from "@/components/ui/States";
import { useDecisions } from "@/hooks/queries";
import { DECISION_STATUS_CLASS, clock, num, upper } from "@/lib/format";
import type { DecisionSummary } from "@/lib/types";

function pmLine(d: DecisionSummary): string {
  if (!d.pm_action) return "PM: no answer";
  if (d.pm_action !== "enter") return `PM: ${upper(d.pm_action)}`;
  return `PM: ${upper(d.pm_direction)} via ${upper(d.pm_vehicle)}`;
}

function Row({ d }: { d: DecisionSummary }) {
  const detail = d.status !== "executed" && d.outcome ? d.outcome : null;
  return (
    <Link href={`/decisions/${d.id}`} className="block border-b border-line/60 px-2 py-1 hover:bg-raised">
      <div className="flex items-baseline gap-3 whitespace-nowrap text-sm">
        <span className="shrink-0 text-faint">{clock(d.ts)}</span>
        <span className="w-12 shrink-0 font-bold text-fg">{d.symbol}</span>
        <span className={`w-36 shrink-0 ${DECISION_STATUS_CLASS[d.status] ?? "text-fg"}`}>{upper(d.status)}</span>
        <span className="shrink-0 text-fg">
          {pmLine(d)}
          {d.pm_confidence !== null ? <span className="text-dim"> conf {num(d.pm_confidence)}</span> : null}
        </span>
      </div>
      <div className="flex items-baseline gap-4 pl-[68px] text-xs">
        <span className="shrink-0">
          <StanceChips stances={d.stances} />
        </span>
        <span className="min-w-0 flex-1 truncate text-dim" title={d.pm_summary ?? ""}>
          {d.pm_summary || "(no summary)"}
        </span>
      </div>
      {detail ? (
        <div className="line-clamp-2 break-words pl-[68px] text-xs text-amber/90" title={detail}>
          {detail}
        </div>
      ) : null}
    </Link>
  );
}

export function DecisionFeed({ className }: { className?: string }) {
  const decisions = useDecisions(40);
  return (
    <Panel className={className} title="DECISIONS" right={decisions.data ? <span>last {decisions.data.length}</span> : null} bodyClassName="overflow-y-auto">
      <QueryBody query={decisions} what="decisions">
        {(rows) => (rows.length === 0 ? <Empty>no deliberations yet</Empty> : rows.map((d) => <Row key={d.id} d={d} />))}
      </QueryBody>
    </Panel>
  );
}
