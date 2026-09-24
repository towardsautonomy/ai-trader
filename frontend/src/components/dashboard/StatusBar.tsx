"use client";

import { Meter } from "@/components/ui/Meter";
import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import Link from "next/link";
import { useRobinhood, useStatus } from "@/hooks/queries";
import { useNow } from "@/hooks/useNow";
import { engineLoops } from "@/lib/engine";
import { DASH, clock, duration, num, pnlClass, signedPct, signedUsd, upper, usd } from "@/lib/format";
import type { AgentModel, LoopHeartbeat, RobinhoodSnapshot, Session, Status } from "@/lib/types";

const OFFLINE_LLM = "offline-heuristic";

const PHASE_TONE: Record<Session["phase"], string> = {
  open: "text-phos",
  opening_buffer: "text-amber",
  closing_buffer: "text-amber",
  flatten: "text-danger",
  closed: "text-dim",
};

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="whitespace-nowrap">
      <div className="label">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

/** Counts down locally between polls from the server's own clock, so a skewed browser clock cannot distort it. */
function sessionCountdown(s: Status, fetchedAt: number, now: number | null): string {
  if (now === null) return DASH;
  const sinceFetch = (now - fetchedAt) / 1000;
  if (s.session.is_open) {
    if (s.session.seconds_to_close === null) return "no close time (clock ignored)";
    return `closes in ${duration(s.session.seconds_to_close - sinceFetch)}`;
  }
  if (!s.session.next_open) return "next open unknown";
  const untilOpen = (new Date(s.session.next_open).getTime() - new Date(s.server_time).getTime()) / 1000 - sinceFetch;
  return `opens in ${duration(untilOpen)}`;
}

const DATA_LABEL: Record<string, { text: string; tone: string; title: string }> = {
  robinhood: { text: "ROBINHOOD", tone: "font-bold text-phos", title: "quotes, 5m bars and option chains from your Robinhood connection" },
  yfinance: { text: "YFINANCE", tone: "text-amber", title: "delayed/unofficial Yahoo data: log in and verify Robinhood for real quotes" },
  synthetic: { text: "SYNTHETIC", tone: "font-bold text-amber", title: "made-up market for the demo" },
};

function DataSource({ s }: { s: Status }) {
  const d = DATA_LABEL[s.data_source] ?? { text: upper(s.data_source), tone: "text-fg", title: "" };
  return (
    <span className={d.tone} title={d.title}>
      {d.text}
    </span>
  );
}

function Orders({ s, rh }: { s: Status; rh?: RobinhoodSnapshot }) {
  if (s.broker === "robinhood") {
    return (
      <span className="font-bold text-danger" title="real orders, real money">
        ROBINHOOD{rh?.account ? ` ...${rh.account.last4}` : ""}
      </span>
    );
  }
  return (
    <span className="text-phos" title="simulated fills against real quotes; nothing reaches a broker">
      PAPER (simulated)
    </span>
  );
}

function RobinhoodLink({ rh }: { rh?: RobinhoodSnapshot }) {
  if (!rh) return <span className="text-dim">...</span>;
  if (!rh.logged_in) return <span className="text-amber">not logged in</span>;
  if (rh.error) return <span className="text-danger" title={rh.error}>ERROR</span>;
  if (!rh.account) return <span className="text-amber">no agentic account</span>;
  return (
    <span title={`${rh.account.type}, ${rh.account.option_level}${rh.verified_at ? `, verified ${rh.verified_at}` : ", NOT verified"}`}>
      <span className="text-phos">...{rh.account.last4}</span> <span className="text-dim">bal</span> {usd(rh.balance?.equity)}
      {!rh.verified_at ? <span className="text-amber"> unverified</span> : null}
    </span>
  );
}

/** The distinct models in use, most important seat first. */
function Models({ s }: { s: Status }) {
  const models: AgentModel[] = s.models ?? [];
  const pm = models.find((m) => m.agent === "portfolio_manager") ?? models[0];
  if (s.llm === OFFLINE_LLM || !pm) return <span className="font-bold text-amber">{s.llm}</span>;
  const others = new Set(models.map((m) => m.model).filter((m) => m !== pm.model));
  const title = models.map((m) => `${m.agent}: ${m.model}${m.thinking ? ` (thinking ${m.thinking})` : ""}`).join("\n");
  return (
    <span title={title}>
      <span className="text-fg">{pm.model.replace(/^local\//, "")}</span>
      <span className="text-dim"> {pm.provider === "local" ? "local" : "OpenRouter"}</span>
      {others.size ? <span className="text-dim"> +{others.size} more</span> : null}
    </span>
  );
}

function loopTone(hb: LoopHeartbeat): string {
  if (hb.overdue) return "font-bold text-danger";
  if (hb.last_error) return "text-amber";
  return "text-fg";
}

/** `ENGINE entry 41s exit 1s review 41s watchdog 1s`: seconds since each loop last started a pass. */
function EngineReadout({ s }: { s: Status }) {
  const loops = engineLoops(s);
  if (loops.length === 0) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 border-t border-line px-3 py-1 text-xs">
      <span className="text-dim">ENGINE</span>
      {loops.map(([name, hb]) => (
        <span key={name} className="whitespace-nowrap" title={`${name} loop runs every ${hb.interval}s${hb.last_error ? `; last error: ${hb.last_error}` : ""}`}>
          <span className="text-dim">{name} </span>
          <span className={loopTone(hb)}>
            {Math.round(hb.seconds_since_tick)}s{hb.overdue ? " OVERDUE" : ""}
          </span>
          <span className="text-faint">/{hb.interval}s</span>
          {hb.last_error ? <span className={hb.overdue ? "text-danger" : "text-amber"}> err</span> : null}
        </span>
      ))}
      {loops.filter(([, hb]) => hb.last_error).map(([name, hb]) => (
        <span key={`${name}-err`} className="min-w-0 break-words text-amber">
          {name} last error: {hb.last_error}
        </span>
      ))}
    </div>
  );
}

function Body({ s, fetchedAt }: { s: Status; fetchedAt: number }) {
  const now = useNow();
  const rh = useRobinhood().data;
  const a = s.account;
  const l = s.limits;
  const dayLossPct = a.day_pnl_pct !== undefined && a.day_pnl_pct < 0 ? -a.day_pnl_pct : 0;
  const offline = s.llm === OFFLINE_LLM;

  return (
    <div>
      <div className="flex flex-wrap items-start gap-x-7 gap-y-2 px-3 py-2">
        <Stat label="equity">
          <span className="text-base font-bold text-fg">{usd(a.equity)}</span>
        </Stat>
        <Stat label="day p&l">
          <span className={`text-base font-bold ${pnlClass(a.day_pnl)}`}>
            {signedUsd(a.day_pnl)} <span className="text-sm font-normal">({signedPct(a.day_pnl_pct, 3)})</span>
          </span>
        </Stat>
        <Stat label="drawdown">
          <span className={a.drawdown_pct ? "text-danger" : "text-fg"}>{a.drawdown_pct === undefined ? DASH : `-${num(a.drawdown_pct, 3)}%`}</span>
        </Stat>
        <Stat label="cash">{usd(a.cash)}</Stat>
        <Stat label="exposure">
          {usd(a.exposure)} <span className="text-dim">({a.exposure_pct === undefined ? DASH : `${num(a.exposure_pct, 1)}%`})</span>
        </Stat>
        <Stat label="session">
          <span className={`font-bold ${PHASE_TONE[s.session.phase] ?? "text-fg"}`}>{upper(s.session.phase)}</span>{" "}
          <span className="text-dim">{sessionCountdown(s, fetchedAt, now)}</span>
        </Stat>
        <Stat label="entries">
          {s.session.entries_allowed && s.kill.state === "armed" ? (
            <span className="text-phos">ALLOWED</span>
          ) : (
            <span className="text-amber">BLOCKED{s.kill.state !== "armed" ? " (kill switch)" : " (session)"}</span>
          )}
        </Stat>
        <Stat label="market data">
          <DataSource s={s} />
        </Stat>
        <Stat label="orders">
          <Orders s={s} rh={rh} />
        </Stat>
        <Stat label="robinhood">
          <Link href="/setup" className="hover:underline">
            <RobinhoodLink rh={rh} />
          </Link>
        </Stat>
        <Stat label="models (pm)">
          <Models s={s} />
        </Stat>
        <Stat label="server time">{clock(s.server_time)}</Stat>
      </div>

      <div className="flex flex-wrap gap-x-7 gap-y-1 border-t border-line px-3 py-1.5">
        <Meter label="DAY LOSS" ratio={dayLossPct / l.max_daily_loss_pct} text={`${signedPct(a.day_pnl_pct, 2)} / -${num(l.max_daily_loss_pct, 1)}%`} />
        <Meter
          label="DRAWDOWN"
          ratio={(a.drawdown_pct ?? 0) / l.max_drawdown_pct}
          text={`${a.drawdown_pct === undefined ? DASH : `${num(a.drawdown_pct, 2)}%`} / ${num(l.max_drawdown_pct, 1)}%`}
        />
        <Meter
          label="EXPOSURE"
          ratio={(a.exposure_pct ?? 0) / l.max_total_exposure_pct}
          text={`${a.exposure_pct === undefined ? DASH : `${num(a.exposure_pct, 1)}%`} / ${num(l.max_total_exposure_pct, 0)}%`}
        />
        <Meter label="POSITIONS" ratio={(a.open_positions ?? 0) / l.max_open_positions} text={`${a.open_positions ?? DASH} / ${l.max_open_positions}`} />
        <Meter label="TRADES" ratio={(a.trades_today ?? 0) / l.max_trades_per_day} text={`${a.trades_today ?? DASH} / ${l.max_trades_per_day}`} />
        <Meter
          label="LLM SPEND"
          ratio={(a.llm_spend_today ?? 0) / l.max_llm_spend_per_day_usd}
          text={`${usd(a.llm_spend_today)} / ${usd(l.max_llm_spend_per_day_usd)}`}
        />
      </div>

      <EngineReadout s={s} />

      {s.demo ? (
        <div className="border-t border-amber/60 bg-amber/10 px-3 py-1 text-sm text-amber">
          <span className="font-bold tracking-wider">DEMO:</span> synthetic market, clock ignored, separate demo data. Nothing here is real.{" "}
          <Link href="/setup" className="underline">
            How to run on real data
          </Link>
        </div>
      ) : null}
      {s.live_refused ? (
        <div className="border-t border-danger/60 bg-danger/10 px-3 py-1 text-sm text-danger">
          <span className="font-bold tracking-wider">LIVE START REFUSED, RUNNING PAPER:</span> <span className="text-fg">{s.live_refused.split("\n")[0]}</span>{" "}
          <Link href="/setup" className="underline">
            details
          </Link>
        </div>
      ) : null}

      {offline ? (
        <div className="border-t border-amber/60 bg-amber/10 px-3 py-1 text-sm text-amber">
          <span className="font-bold tracking-wider">WARNING: LLM IS OFFLINE-HEURISTIC.</span> No real model is reasoning about these trades; every agent opinion is a
          hard-coded heuristic.
        </div>
      ) : null}
      {s.account_error ? (
        <div className="border-t border-danger/60 bg-danger/10 px-3 py-1 text-sm text-danger">
          <span className="font-bold tracking-wider">BROKER ACCOUNT UNAVAILABLE:</span> <span className="text-fg">{s.account_error}</span>. Account numbers above are
          blank; the kill switch still works.
        </div>
      ) : null}
    </div>
  );
}

export function StatusBar() {
  const status = useStatus();
  return (
    <Panel title="STATUS" right={status.data ? <span>polled {clock(new Date(status.dataUpdatedAt).toISOString())}</span> : null}>
      <QueryBody query={status} what="status">
        {(s) => <Body s={s} fetchedAt={status.dataUpdatedAt} />}
      </QueryBody>
    </Panel>
  );
}
