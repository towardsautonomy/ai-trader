import Link from "next/link";
import { EventRow } from "@/components/EventRow";
import { OrdersTable } from "@/components/OrdersTable";
import { RegimeBlock } from "@/components/RegimeBlock";
import { Collapsible } from "@/components/ui/Collapsible";
import { JsonBlock } from "@/components/ui/JsonBlock";
import { KV } from "@/components/ui/KV";
import { Empty } from "@/components/ui/States";
import { DASH, DECISION_STATUS_CLASS, STANCE_GLYPH, clock, dateTime, instrumentLabel, num, pct, stanceClass, upper, usd } from "@/lib/format";
import type { Candidate, DecisionTrace, LLMCallMeta, Opinion, PMPlan, Proposal, RiskVerdict, ShortlistContract } from "@/lib/types";
import { LLMCallViewer } from "./LLMCallViewer";
import { Sparkline } from "./Sparkline";
import { NotReached, Step, type StepTone } from "./Timeline";

const PM_AGENT = "portfolio_manager";

function CandidateBlock({ c }: { c: Candidate }) {
  const signals = Object.entries(c.signals);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3 lg:grid-cols-6">
        <KV k="bid / ask">
          {num(c.quote.bid)} / {num(c.quote.ask)}
        </KV>
        <KV k="last">{num(c.quote.last)}</KV>
        <KV k="quote time">{clock(c.quote.ts, true)}</KV>
        <KV k="atr">{num(c.atr, 3)}</KV>
        <KV k="scanner score">{num(c.hint_score)}</KV>
        <KV k="scanner setups">{c.hint_setups.length > 0 ? c.hint_setups.join(", ") : DASH}</KV>
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_auto]">
        <table className="tbl">
          <thead>
            <tr>
              {signals.map(([k]) => (
                <th key={k} className="r">
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              {signals.map(([k, v]) => (
                <td key={k} className="r">
                  {num(v, 3)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
        <div>
          <div className="label mb-1">
            recent closes | day O {num(c.recent.day_open)} H {num(c.recent.day_high)} L {num(c.recent.day_low)}
          </div>
          <Sparkline values={c.recent.closes} />
          <div className="mt-1 text-xs text-dim">
            rel volume, last {c.recent.rel_volume.length} bars: <span className="text-fg">{c.recent.rel_volume.map((v) => num(v)).join("  ")}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function OpinionCard({ o, call }: { o: Opinion; call: LLMCallMeta | undefined }) {
  return (
    <div className={`border bg-bg p-2 ${o.error ? "border-danger" : "border-line"}`}>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-bold uppercase tracking-wider text-fg">{upper(o.agent)}</span>
        <span className={`font-bold ${stanceClass(o.stance)}`}>
          {STANCE_GLYPH[o.stance] ?? "?"} {upper(o.stance)} {num(o.confidence)}
        </span>
        <span className="ml-auto text-xs text-dim">
          {o.model} | {num(o.latency_ms, 0)} ms | {o.tokens_in}/{o.tokens_out} tok | {usd(o.cost_usd, 4)}
        </span>
      </div>
      {o.error ? <div className="mt-1 break-words text-sm text-danger">ERROR: {o.error}</div> : null}
      <div className="mt-1.5 text-sm text-fg">{o.thesis || DASH}</div>
      {o.evidence.length > 0 ? (
        <ul className="mt-1.5 space-y-0.5 text-sm text-fg">
          {o.evidence.map((ev, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-faint">-</span>
              <span className="min-w-0 break-words">{ev}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-1.5 text-xs text-faint">no evidence listed</div>
      )}
      <div className="mt-1.5 text-sm">
        <span className="text-dim">invalidation: </span>
        {o.invalidation || DASH}
      </div>
      {call ? (
        <div className="mt-1.5">
          <LLMCallViewer callId={call.id} />
        </div>
      ) : null}
    </div>
  );
}

function contractLabel(symbol: string, c: ShortlistContract): string {
  return `${symbol} ${c.expiry} ${num(c.strike, c.strike % 1 === 0 ? 0 : 2)} ${upper(c.type)}`;
}

/** The option contracts the PM was offered. pm.contract_index points at one of these rows. */
function ShortlistTable({ symbol, contracts, chosen }: { symbol: string; contracts: ShortlistContract[]; chosen: number | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th className="r">#</th>
            <th>contract</th>
            <th className="r">dte</th>
            <th className="r">bid</th>
            <th className="r">ask</th>
            <th className="r">mid</th>
            <th className="r">cost / contract</th>
            <th className="r">spread</th>
            <th className="r">delta</th>
            <th className="r">iv</th>
            <th className="r">open int</th>
            <th className="r">volume</th>
            <th>pm choice</th>
          </tr>
        </thead>
        <tbody>
          {contracts.map((c) => {
            const picked = c.index === chosen;
            return (
              <tr key={c.index} className={picked ? "bg-phos/10" : ""}>
                <td className={`r ${picked ? "font-bold text-phos" : "text-dim"}`}>{c.index}</td>
                <td className={picked ? "font-bold text-phos" : "text-fg"}>{contractLabel(symbol, c)}</td>
                <td className="r">{c.dte}</td>
                <td className="r">{num(c.bid)}</td>
                <td className="r">{num(c.ask)}</td>
                <td className="r">{num(c.mid)}</td>
                <td className="r">{c.cost_per_contract_usd === undefined ? "--" : `$${num(c.cost_per_contract_usd, 0)}`}</td>
                <td className="r">{pct(c.spread_pct, 2)}</td>
                <td className="r">{num(c.delta)}</td>
                <td className="r">{pct(c.iv * 100, 0)}</td>
                <td className="r">{num(c.open_interest, 0)}</td>
                <td className="r">{num(c.volume, 0)}</td>
                <td className="font-bold text-phos">{picked ? "<- CHOSEN" : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PlanBlock({ pm, symbol, shortlist }: { pm: PMPlan; symbol: string; shortlist: ShortlistContract[] | undefined }) {
  const enter = pm.action === "enter";
  const contract = pm.contract_index === null ? undefined : shortlist?.find((c) => c.index === pm.contract_index);
  return (
    <div className="space-y-2">
      {pm.contract_index !== null ? (
        <div className="border border-line bg-bg px-2 py-1 text-sm">
          <span className="text-dim">contract </span>
          {contract ? (
            <span className="text-fg">
              #{pm.contract_index} -&gt; <span className="font-bold text-phos">{contractLabel(symbol, contract)}</span>, delta {num(contract.delta)}, spread{" "}
              {pct(contract.spread_pct, 1)}, mid {num(contract.mid)}, {contract.dte} dte
            </span>
          ) : (
            <span className="text-amber">
              #{pm.contract_index}: {shortlist ? "this index is not in the shortlist the PM was shown" : "the shortlist was not recorded for this decision, so the index cannot be resolved"}
            </span>
          )}
        </div>
      ) : null}
      <div className="grid grid-cols-4 gap-3 lg:grid-cols-8">
        <KV k="action">
          <span className={`font-bold ${enter ? "text-phos" : "text-dim"}`}>{upper(pm.action)}</span>
        </KV>
        <KV k="direction">{upper(pm.direction) || DASH}</KV>
        <KV k="vehicle">{upper(pm.vehicle) || DASH}</KV>
        <KV k="contract #">{pm.contract_index ?? DASH}</KV>
        <KV k="stop">
          <span className="text-danger">{num(pm.stop_price)}</span>
        </KV>
        <KV k="target">
          <span className="text-phos">{num(pm.target_price)}</span>
        </KV>
        <KV k="size fraction">{num(pm.size_fraction)}</KV>
        <KV k="max hold">{pm.max_hold_minutes === null ? DASH : `${pm.max_hold_minutes}m`}</KV>
      </div>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-[auto_minmax(0,1fr)] lg:gap-x-6">
        <KV k="confidence">{num(pm.confidence)}</KV>
        <KV k="summary">{pm.summary || DASH}</KV>
      </div>
      <KV k="key risks">
        {pm.key_risks.length > 0 ? (
          <ul>
            {pm.key_risks.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-faint">-</span>
                <span className="min-w-0 break-words">{r}</span>
              </li>
            ))}
          </ul>
        ) : (
          DASH
        )}
      </KV>
      <KV k="invalidation">{pm.invalidation || DASH}</KV>
    </div>
  );
}

function ProposalBlock({ p }: { p: Proposal }) {
  return (
    <div className="space-y-2">
      {p.clamps.length > 0 ? (
        <div className="border border-amber bg-amber/10 p-2">
          <div className="text-xs font-bold uppercase tracking-wider text-amber">code overrode the agent ({p.clamps.length})</div>
          <ul className="mt-1 text-sm text-amber">
            {p.clamps.map((c, i) => (
              <li key={i} className="flex gap-2">
                <span>!</span>
                <span className="min-w-0 break-words">{c}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="text-xs text-dim">no clamps: the plan fit the risk envelope as the PM wrote it</div>
      )}
      <div className="grid grid-cols-4 gap-3 lg:grid-cols-8">
        <KV k="order">
          <span className={p.side === "buy" ? "text-phos" : "text-danger"}>{upper(p.side)}</span> {num(p.qty, 0)}
        </KV>
        <KV k="instrument">{instrumentLabel(p.instrument, p.symbol)}</KV>
        <KV k="vehicle">{upper(p.vehicle)}</KV>
        <KV k="limit">{num(p.limit_price)}</KV>
        <KV k="stop">
          <span className="text-danger">{num(p.stop_price)}</span>
        </KV>
        <KV k="target">
          <span className="text-phos">{num(p.target_price)}</span>
        </KV>
        <KV k="risk at stop">{usd(p.risk_usd)}</KV>
        <KV k="notional">{usd(p.notional_usd)}</KV>
        {p.und_stop || p.und_target ? (
          <>
            <KV k="underlying stop">{num(p.und_stop)}</KV>
            <KV k="underlying target">{num(p.und_target)}</KV>
          </>
        ) : null}
        <KV k="quote age">{num(p.quote_age_seconds, 2)}s</KV>
        <KV k="spread">{pct(p.spread_pct, 3)}</KV>
        <KV k="max hold">{p.max_hold_minutes}m</KV>
      </div>
    </div>
  );
}

function RiskBlock({ risk }: { risk: RiskVerdict }) {
  const failed = risk.checks.filter((c) => !c.passed);
  return (
    <div>
      {failed.length > 0 ? (
        <div className="mb-2 border border-danger bg-danger/10 p-2 text-sm text-danger">
          {failed.length} rule{failed.length > 1 ? "s" : ""} failed: {failed.map((c) => c.rule).join(", ")}
        </div>
      ) : null}
      <table className="tbl">
        <thead>
          <tr>
            <th className="w-16">result</th>
            <th>rule</th>
            <th>detail</th>
          </tr>
        </thead>
        <tbody>
          {risk.checks.map((c) => (
            <tr key={c.rule} className={c.passed ? "" : "bg-danger/10"}>
              <td className={`font-bold ${c.passed ? "text-phos" : "text-danger"}`}>{c.passed ? "[PASS]" : "[FAIL]"}</td>
              <td className={c.passed ? "text-fg" : "font-bold text-danger"}>{c.rule}</td>
              <td className={`!whitespace-normal ${c.passed ? "text-dim" : "text-danger"}`}>{c.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function outcomeTone(status: DecisionTrace["decision"]["status"]): StepTone {
  if (status === "executed") return "ok";
  if (status === "deliberating") return "info";
  if (status === "rejected_by_risk" || status === "failed") return "fail";
  return "warn";
}

/** The full reasoning chain for one deliberation, in the order it happened. Also embedded in the position trace. */
export function DecisionTraceView({ trace, showPositionLink = true }: { trace: DecisionTrace; showPositionLink?: boolean }) {
  const d = trace.decision;
  const callByAgent = new Map(trace.llm_calls.map((c) => [c.agent, c]));
  const pmCall = callByAgent.get(PM_AGENT);
  const shortlist = d.context?.option_shortlist;
  const stoppedBeforePlan = d.pm === null ? "the portfolio manager has not answered" : d.pm.action !== "enter" ? "the PM chose not to enter" : null;
  const opinionErrors = trace.opinions.filter((o) => o.error).length;
  const llmCost = trace.llm_calls.reduce((sum, c) => sum + c.cost_usd, 0);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-baseline gap-x-6 gap-y-1 border border-line bg-bg px-3 py-2">
        <span className="text-base font-bold text-fg">{d.symbol}</span>
        <span className={`font-bold ${DECISION_STATUS_CLASS[d.status] ?? "text-fg"}`}>{upper(d.status)}</span>
        <span className="min-w-0 flex-1 break-words text-sm text-fg">{d.outcome || (d.status === "deliberating" ? "agents are still deliberating" : DASH)}</span>
        <span className="text-xs text-dim">
          {dateTime(d.ts)} | decision {d.id} | cycle {d.cycle_id} | {upper(d.mode)}
        </span>
      </div>

      <ol className="ml-1.5">
        <Step n={1} title="Scout" tone="ok">
          <div className="text-sm text-fg">{d.scout_reason || DASH}</div>
        </Step>

        <Step n={2} title="Candidate signals" tone="ok">
          {d.candidate ? <CandidateBlock c={d.candidate} /> : <Empty>no candidate snapshot recorded</Empty>}
        </Step>

        <Step n={3} title="Regime at the time" tone="ok">
          <RegimeBlock regime={d.regime} />
        </Step>

        <Step
          n={4}
          title="Agent opinions"
          tone={opinionErrors > 0 ? "fail" : trace.opinions.length === 0 ? "skipped" : "ok"}
          tag={opinionErrors > 0 ? <span className="text-danger">{opinionErrors} agent error(s)</span> : null}
        >
          {trace.opinions.length === 0 ? (
            <Empty>{d.status === "deliberating" ? "waiting for the specialists" : "no opinions recorded"}</Empty>
          ) : (
            <div className="grid gap-2 xl:grid-cols-2">
              {trace.opinions.map((o) => (
                <OpinionCard key={o.id} o={o} call={callByAgent.get(o.agent)} />
              ))}
            </div>
          )}
        </Step>

        <Step n={5} title="Context shown to the PM" tone="ok">
          {d.context ? (
            <div className="space-y-1.5">
              <Collapsible summary="portfolio">
                <JsonBlock value={d.context.portfolio ?? null} />
              </Collapsible>
              <Collapsible summary="track record">
                <JsonBlock value={d.context.track_record ?? null} />
              </Collapsible>
              <Collapsible summary={`lessons (${d.context.lessons?.length ?? 0})`}>
                <JsonBlock value={d.context.lessons ?? []} />
              </Collapsible>
              <Collapsible summary="risk envelope">
                <JsonBlock value={d.context.envelope ?? null} />
              </Collapsible>
            </div>
          ) : (
            <Empty>no context recorded</Empty>
          )}
        </Step>

        <Step
          n={6}
          title="Option shortlist offered to the PM"
          tone={shortlist === undefined ? "skipped" : "ok"}
          tag={shortlist && shortlist.length > 0 ? <span className="text-dim">{shortlist.length} contracts</span> : null}
        >
          {shortlist === undefined ? (
            <div className="text-sm text-faint">not recorded for this decision (it predates shortlist capture)</div>
          ) : shortlist.length === 0 ? (
            <div className="text-sm text-dim">no option contracts were offered: shares were the only vehicle available</div>
          ) : (
            <ShortlistTable symbol={d.symbol} contracts={shortlist} chosen={d.pm?.contract_index ?? null} />
          )}
        </Step>

        <Step
          n={7}
          title="PM plan"
          tone={d.pm === null ? "skipped" : d.pm.action === "enter" ? "ok" : "warn"}
          tag={d.pm ? <span className={d.pm.action === "enter" ? "text-phos" : "text-amber"}>{upper(d.pm.action)}</span> : null}
        >
          {d.pm ? (
            <div className="space-y-2">
              <PlanBlock pm={d.pm} symbol={d.symbol} shortlist={shortlist} />
              {pmCall ? <LLMCallViewer callId={pmCall.id} /> : null}
            </div>
          ) : (
            <NotReached why="the portfolio manager has not answered" />
          )}
        </Step>

        <Step
          n={8}
          title="Proposal (after code clamps)"
          tone={d.proposal ? (d.proposal.clamps.length > 0 ? "warn" : "ok") : stoppedBeforePlan ? "skipped" : "fail"}
          tag={d.proposal && d.proposal.clamps.length > 0 ? <span className="text-amber">{d.proposal.clamps.length} CLAMP(S)</span> : null}
        >
          {d.proposal ? (
            <ProposalBlock p={d.proposal} />
          ) : stoppedBeforePlan ? (
            <NotReached why={stoppedBeforePlan} />
          ) : (
            <div className="text-sm text-danger">no order could be built from the plan: {d.outcome || "reason not recorded"}</div>
          )}
        </Step>

        <Step
          n={9}
          title="Risk verdict"
          tone={d.risk ? (d.risk.approved ? "ok" : "fail") : "skipped"}
          tag={d.risk ? <span className={`font-bold ${d.risk.approved ? "text-phos" : "text-danger"}`}>{d.risk.approved ? "APPROVED" : "REJECTED"}</span> : null}
        >
          {d.risk ? <RiskBlock risk={d.risk} /> : <NotReached why="no proposal was sent to the risk engine" />}
        </Step>

        <Step n={10} title="Orders" tone={trace.orders.length > 0 ? "ok" : "skipped"}>
          {trace.orders.length > 0 ? <OrdersTable orders={trace.orders} /> : <NotReached why="no order was submitted" />}
        </Step>

        <Step n={11} title="Result" tone={outcomeTone(d.status)}>
          <div className="text-sm text-fg">{d.outcome || DASH}</div>
          {trace.position_id && showPositionLink ? (
            <Link className="link mt-1 inline-block text-sm" href={`/positions/${trace.position_id}`}>
              open position trace {trace.position_id} -&gt;
            </Link>
          ) : null}
          {!trace.position_id ? <div className="mt-1 text-xs text-faint">no position resulted from this decision</div> : null}
        </Step>

        <Step n={12} title={`Events (${trace.events.length})`} tone="ok">
          {trace.events.length === 0 ? <Empty>no events</Empty> : <div className="border border-line bg-bg py-1">{trace.events.map((e) => <EventRow key={e.id} e={e} />)}</div>}
        </Step>

        <Step n={13} title={`LLM calls (${trace.llm_calls.length})`} tone="ok" tag={<span className="text-dim">total {usd(llmCost, 4)}</span>}>
          {trace.llm_calls.length === 0 ? (
            <Empty>no LLM calls recorded</Empty>
          ) : (
            <div className="space-y-1.5">
              {trace.llm_calls.map((c) => (
                <div key={c.id} className={`border bg-bg px-2 py-1 ${c.error ? "border-danger" : "border-line"}`}>
                  <div className="flex flex-wrap items-baseline gap-x-4 text-sm">
                    <span className="w-40 font-bold text-fg">{upper(c.agent)}</span>
                    <span className="text-dim">{c.model}</span>
                    <span className="text-dim">{clock(c.ts, true)}</span>
                    <span className="ml-auto text-xs text-dim">
                      {num(c.latency_ms, 0)} ms | {c.tokens_in}/{c.tokens_out} tok | {usd(c.cost_usd, 4)}
                    </span>
                  </div>
                  {c.error ? <div className="break-words text-sm text-danger">ERROR: {c.error}</div> : null}
                  <LLMCallViewer callId={c.id} />
                </div>
              ))}
            </div>
          )}
        </Step>
      </ol>
    </div>
  );
}
