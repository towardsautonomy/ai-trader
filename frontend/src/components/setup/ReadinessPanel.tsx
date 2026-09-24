"use client";

import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import { useReadiness } from "@/hooks/queries";
import type { ReadinessCheck } from "@/lib/types";

function Mark({ c }: { c: ReadinessCheck }) {
  if (c.ok === true) return <span className="text-phos">[ OK ]</span>;
  if (c.ok === null) return <span className="text-dim">[ ?? ]</span>;
  return c.blocking ? <span className="font-bold text-danger">[FAIL]</span> : <span className="text-amber">[WARN]</span>;
}

export function ReadinessPanel({ className = "" }: { className?: string }) {
  const q = useReadiness();
  return (
    <Panel
      title="LIVE READINESS"
      className={className}
      right={
        <>
          {q.data ? (
            <span className={q.data.ready ? "font-bold text-phos" : "font-bold text-amber"}>{q.data.ready ? "READY FOR LIVE" : "NOT READY"}</span>
          ) : null}
          <button type="button" className="btn" disabled={q.isFetching} onClick={() => void q.refetch()}>
            {q.isFetching ? "CHECKING..." : "RECHECK"}
          </button>
        </>
      }
    >
      <QueryBody query={q} what="readiness">
        {(r) => (
          <div className="text-sm">
            <p className="px-3 pt-2 text-xs text-dim">
              Checked against the real systems each time. Every blocking check must pass before the switch to live is offered. Options in live: {r.options}.
            </p>
            <ul className="mt-1">
              {r.checks.map((c) => (
                <li key={c.id} className="flex gap-3 border-t border-line/60 px-3 py-1.5">
                  <span className="shrink-0 whitespace-pre">
                    <Mark c={c} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-3">
                      <span className="text-fg">{c.label}</span>
                      <span className="break-words text-dim">{c.detail}</span>
                      {!c.blocking ? <span className="text-2xs uppercase tracking-wider text-faint">advisory</span> : null}
                    </div>
                    {c.fix ? (
                      <div className="mt-0.5 text-xs">
                        <span className="text-faint">fix: </span>
                        <code className="select-all break-words text-amber">{c.fix}</code>
                      </div>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </QueryBody>
    </Panel>
  );
}
