import { EventRow } from "@/components/EventRow";
import { OrdersTable } from "@/components/OrdersTable";
import { Collapsible } from "@/components/ui/Collapsible";
import { JsonBlock } from "@/components/ui/JsonBlock";
import { KV } from "@/components/ui/KV";
import { Panel } from "@/components/ui/Panel";
import { Empty } from "@/components/ui/States";
import { DASH, dateTime, instrumentLabel, minutesBetween, num, pnlClass, signedR, signedUsd, upper, usd } from "@/lib/format";
import type { LLMCallMeta, Lesson, Position, PositionReview, PositionTrace } from "@/lib/types";
import { DecisionTraceView } from "./DecisionTraceView";
import { LevelsBar } from "./LevelsBar";
import { LLMCallViewer } from "./LLMCallViewer";

const ACTION_TONE: Record<string, string> = { hold: "text-dim", adjust: "text-cyan", exit: "text-danger" };
const REVIEW_AGENT = "position_manager";
const LESSON_AGENT = "reviewer";

function HeaderBlock({ p }: { p: Position }) {
  const closed = p.status === "closed";
  const pnl = closed ? p.realized_pnl : p.unrealized_pnl;
  const endMs = p.exit_ts ? new Date(p.exit_ts).getTime() : Date.now();
  return (
    <div className="flex flex-wrap items-start gap-x-8 gap-y-2 border border-line bg-panel px-3 py-2">
      <div>
        <div className="text-base font-bold text-fg">{instrumentLabel(p.instrument, p.instrument_key)}</div>
        <div className="text-xs text-dim">
          position {p.id} | {upper(p.mode)}
        </div>
      </div>
      <KV k="status">
        <span className={`font-bold ${closed ? "text-dim" : p.status === "closing" ? "text-amber" : "text-phos"}`}>{upper(p.status)}</span>
      </KV>
      <KV k="thesis direction">
        <span className={p.direction > 0 ? "text-phos" : "text-danger"}>{p.direction > 0 ? "LONG" : "SHORT"}</span>
      </KV>
      <KV k="qty">{num(p.qty, 0)}</KV>
      <KV k={closed ? "realized p&l" : "unrealized p&l"}>
        <span className={`text-base font-bold ${pnlClass(pnl)}`}>{signedUsd(pnl)}</span>
      </KV>
      <KV k="R multiple">
        <span className={`text-base font-bold ${pnlClass(p.pnl_r)}`}>{signedR(p.pnl_r)}</span>
      </KV>
      <KV k="risk at entry">{usd(p.risk_usd)}</KV>
      <KV k="fees">{usd(p.fees)}</KV>
      <KV k="opened">{dateTime(p.entry_ts)}</KV>
      <KV k={closed ? "closed" : "held"}>{closed ? dateTime(p.exit_ts) : `${minutesBetween(p.entry_ts, endMs)}m of ${p.max_hold_minutes}m max`}</KV>
      {closed ? <KV k="held for">{minutesBetween(p.entry_ts, endMs)}m of {p.max_hold_minutes}m max</KV> : null}
    </div>
  );
}

function LevelsBlock({ p }: { p: Position }) {
  const closed = p.status === "closed";
  const stopMoved = p.stop_price !== p.initial_stop;
  const mark = closed && p.exit_price !== null ? p.exit_price : p.last_price;
  const levels = [
    ...(stopMoved ? [{ key: "S0", label: "initial stop", value: p.initial_stop, color: "#6f8f7c" }] : []),
    { key: "S", label: "stop", value: p.stop_price, color: "#ff4d4d" },
    { key: "E", label: "entry", value: p.entry_price, color: "#b9d8c4" },
    { key: closed ? "X" : "L", label: closed ? "exit" : "last", value: mark, color: "#4fd6ff" },
    { key: "T", label: "target", value: p.target_price, color: "#3dff8b" },
  ];
  return (
    <div className="space-y-3 p-3">
      <div className="grid grid-cols-3 gap-3 lg:grid-cols-6">
        <KV k="[E] entry">{num(p.entry_price, 4)}</KV>
        <KV k="[S0] initial stop">{num(p.initial_stop)}</KV>
        <KV k="[S] current stop">
          <span className="text-danger">{num(p.stop_price)}</span>
          {stopMoved ? (
            <span className="ml-2 text-xs text-cyan">
              moved {p.stop_price > p.initial_stop ? "+" : "-"}
              {num(Math.abs(p.stop_price - p.initial_stop))} from initial
            </span>
          ) : (
            <span className="ml-2 text-xs text-faint">never moved</span>
          )}
        </KV>
        <KV k="[T] target">
          <span className="text-phos">{num(p.target_price)}</span>
        </KV>
        <KV k={closed ? "[X] exit price" : "[L] last price"}>
          <span className="text-cyan">{num(mark, closed ? 4 : 2)}</span>
        </KV>
        <KV k="high / low water">
          {num(p.high_water)} / {num(p.low_water)}
        </KV>
      </div>
      <LevelsBar levels={levels} />
      {p.is_option ? (
        <div className="grid grid-cols-3 gap-3 border-t border-line pt-2 lg:grid-cols-6">
          <KV k="thesis stop (underlying)">{p.und_stop ? num(p.und_stop) : DASH}</KV>
          <KV k="thesis target (underlying)">{p.und_target ? num(p.und_target) : DASH}</KV>
          <div className="col-span-3 self-end text-xs text-dim lg:col-span-4">
            Levels above are on the option premium; the option is also closed when the underlying crosses these thesis levels.
          </div>
        </div>
      ) : null}
      <div className="grid gap-3 border-t border-line pt-2 lg:grid-cols-2">
        <KV k="thesis">{p.thesis || DASH}</KV>
        <KV k="invalidation">{p.invalidation || DASH}</KV>
      </div>
    </div>
  );
}

function ReviewCard({ r, call }: { r: PositionReview; call: LLMCallMeta | undefined }) {
  const changes = Object.entries(r.applied.changes ?? {});
  const notes = r.applied.notes ?? [];
  const asked = [
    r.requested.new_stop != null ? `stop -> ${num(r.requested.new_stop)}` : null,
    r.requested.new_target != null ? `target -> ${num(r.requested.new_target)}` : null,
  ].filter((x): x is string => x !== null);

  return (
    <li className="relative border-l border-line-hi pb-4 pl-6 last:pb-1">
      <span className={`absolute -left-[5px] top-1 h-[9px] w-[9px] border ${notes.length > 0 ? "border-amber bg-amber" : "border-line-hi bg-panel"}`} aria-hidden />
      <div className="flex flex-wrap items-baseline gap-x-4 text-sm">
        <span className="text-faint">{dateTime(r.ts)}</span>
        <span className={`font-bold ${ACTION_TONE[r.action] ?? "text-fg"}`}>{upper(r.action)}</span>
        <span className="text-dim">conf {num(r.confidence)}</span>
        <span className="ml-auto text-xs text-dim">
          {r.model} | {usd(r.cost_usd, 4)}
        </span>
      </div>
      {r.error ? <div className="mt-1 break-words text-sm text-danger">ERROR: {r.error}</div> : null}
      <div className="mt-1 text-sm text-fg">{r.reasoning || DASH}</div>
      <div className="mt-1.5 grid gap-x-6 gap-y-1 text-sm lg:grid-cols-2">
        <div>
          <span className="text-dim">requested: </span>
          {asked.length > 0 ? asked.join(", ") : <span className="text-faint">no level change</span>}
        </div>
        <div>
          <span className="text-dim">applied: </span>
          {changes.length > 0 ? (
            <span className="text-cyan">{changes.map(([k, v]) => `${k} = ${num(v)}`).join(", ")}</span>
          ) : (
            <span className="text-faint">nothing changed</span>
          )}
        </div>
      </div>
      {notes.length > 0 ? (
        <div className="mt-1.5 border border-amber bg-amber/10 p-2">
          <div className="text-xs font-bold uppercase tracking-wider text-amber">code refused or altered the request</div>
          <ul className="mt-1 text-sm text-amber">
            {notes.map((n, i) => (
              <li key={i} className="flex gap-2">
                <span>!</span>
                <span className="min-w-0 break-words">{n}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="mt-1.5 space-y-1">
        <Collapsible summary="snapshot the position manager saw">
          <JsonBlock value={r.context} />
        </Collapsible>
        {call ? <LLMCallViewer callId={call.id} /> : null}
      </div>
    </li>
  );
}

function LessonBlock({ lesson, call }: { lesson: Lesson; call: LLMCallMeta | undefined }) {
  return (
    <div className="space-y-2 p-3">
      <div className="flex flex-wrap items-baseline gap-x-4 text-sm">
        <span className={`font-bold ${pnlClass(lesson.outcome_r)}`}>{signedR(lesson.outcome_r)}</span>
        <span className="text-dim">{upper(lesson.vehicle)}</span>
        <span className="text-cyan">{lesson.tags.map((t) => `#${t}`).join(" ")}</span>
        <span className="ml-auto text-xs text-dim">
          {lesson.model} | {usd(lesson.cost_usd, 4)} | {dateTime(lesson.ts)}
        </span>
      </div>
      <KV k="situation">{lesson.situation || DASH}</KV>
      <KV k="what happened">{lesson.what_happened || DASH}</KV>
      <KV k="lesson">
        <span className="text-phos">{lesson.lesson || DASH}</span>
      </KV>
      {call ? <LLMCallViewer callId={call.id} /> : null}
    </div>
  );
}

export function PositionTraceView({ trace }: { trace: PositionTrace }) {
  const p = trace.position;
  const closed = p.status === "closed";
  const calls = trace.llm_calls ?? [];
  // The backend records one position_manager call per review, in the same order. If the counts ever differ, pairing by index would attach the wrong prompt, so none are attached.
  const reviewCalls = calls.filter((c) => c.agent === REVIEW_AGENT);
  const paired = reviewCalls.length === trace.reviews.length;
  const lessonCall = calls.filter((c) => c.agent === LESSON_AGENT).at(-1);
  return (
    <div className="space-y-2">
      <HeaderBlock p={p} />

      <Panel title="LEVELS">
        <LevelsBlock p={p} />
      </Panel>

      <Panel title="EXIT" right={closed ? null : <span>position is still {p.status}</span>}>
        {closed ? (
          <div className="grid grid-cols-2 gap-3 p-3 lg:grid-cols-[auto_auto_auto_minmax(0,1fr)] lg:gap-x-8">
            <KV k="exit reason">
              <span className="font-bold text-fg">{upper(p.exit_reason) || DASH}</span>
            </KV>
            <KV k="exit price">{num(p.exit_price, 4)}</KV>
            <KV k="exit time">{dateTime(p.exit_ts)}</KV>
            <KV k="detail">{p.exit_detail || DASH}</KV>
          </div>
        ) : (
          <Empty>not closed yet: exits when the stop, target, time stop, position manager, end-of-day flatten or kill switch fires</Empty>
        )}
      </Panel>

      <Panel title={`POSITION MANAGER REVIEWS (${trace.reviews.length})`}>
        {trace.reviews.length === 0 ? (
          <Empty>the position manager has not reviewed this trade</Empty>
        ) : (
          <div className="p-3">
            {!paired && reviewCalls.length > 0 ? (
              <div className="mb-3 border border-amber bg-amber/10 p-2 text-sm text-amber">
                {reviewCalls.length} position-manager LLM calls for {trace.reviews.length} reviews: they cannot be matched one to one, so they are listed here instead of on each
                review.
                <div className="mt-1 space-y-1">
                  {reviewCalls.map((c) => (
                    <LLMCallViewer key={c.id} callId={c.id} />
                  ))}
                </div>
              </div>
            ) : null}
            <ol className="ml-1.5">
              {trace.reviews.map((r, i) => (
                <ReviewCard key={r.id} r={r} call={paired ? reviewCalls[i] : undefined} />
              ))}
            </ol>
          </div>
        )}
      </Panel>

      <Panel title={`ORDERS (${trace.orders.length})`}>
        <OrdersTable orders={trace.orders} />
      </Panel>

      <Panel title="LESSON WRITTEN AFTERWARDS">
        {trace.lesson ? <LessonBlock lesson={trace.lesson} call={lessonCall} /> : <Empty>{closed ? "no lesson recorded for this trade" : "written by the reviewer after the position closes"}</Empty>}
      </Panel>

      <Panel title={`POSITION EVENTS (${trace.events.length})`}>
        {trace.events.length === 0 ? <Empty>no events</Empty> : <div className="py-1">{trace.events.map((e) => <EventRow key={e.id} e={e} fullDate />)}</div>}
      </Panel>

      <Panel title="WHY IT WAS OPENED: DECISION TRACE">
        <div className="p-3">
          <DecisionTraceView trace={trace.decision} showPositionLink={false} />
        </div>
      </Panel>
    </div>
  );
}
