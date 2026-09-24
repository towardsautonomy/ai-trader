"""The engine: four loops, all gated by the market clock.

  entry     scan -> regime -> scout -> swarm -> plan -> risk envelope -> execute
  exit      every few seconds; enforces stops/targets/backstops; no LLM
  review    the position-manager agent re-examines each open trade
  watchdog  equity tracking, automatic kill-switch trips, reconciliation

Outside the trading window nothing runs except the watchdog's kill-file check.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import desc, func, select

from app.agents.llm import SpendTracker
from app.agents.swarm import Swarm, shortlist_payload
from app.broker.base import Broker, MarketData
from app.config import AppConfig, Settings
from app.core.clock import MarketClock, SessionState
from app.core.events import EventLog
from app.core.killswitch import FLATTEN, HALTED, KillSwitch
from app.core.types import Candidate, Instrument, Opinion, OrderReason, RiskVerdict, utcnow
from app.db.models import AgentOpinion, Decision, EquitySnapshot, Lesson, LLMCall, Position, PositionReview
from app.db.session import Database
from app.engine.execution import Executor, new_id
from app.engine.exits import PosView, apply_review, check_exit
from app.engine.planner import Unbuildable, build_proposal, build_shortlist
from app.risk.engine import OpenPos, PortfolioState, RiskEngine
from app.scanner.scanner import build_table

log = logging.getLogger("ait.engine")

MAX_BROKER_ERRORS = 5
MAX_DATA_FAILURES = 6
LLM_FAILURE_WINDOW = 8
MAX_RECONCILE_MISMATCHES = 3


def pos_view(p: Position) -> PosView:
    inst = Instrument.model_validate(p.instrument)
    return PosView(is_option=inst.is_option, direction=p.direction, entry_price=p.entry_price,
                   stop_price=p.stop_price, target_price=p.target_price, und_stop=p.und_stop,
                   und_target=p.und_target, entry_ts=p.entry_ts, max_hold_minutes=p.max_hold_minutes,
                   expiry=inst.expiry)


def pnl_r(p: Position, price: float) -> float:
    mult = 100 if p.instrument.get("right") else 1
    return (price - p.entry_price) * p.qty * mult / p.risk_usd if p.risk_usd > 0 else 0.0


class Engine:
    def __init__(self, settings: Settings, cfg: AppConfig, db: Database, events: EventLog, kill: KillSwitch,
                 clock: MarketClock, data: MarketData, broker: Broker, swarm: Swarm, spend: SpendTracker):
        self.settings, self.cfg, self.db, self.events, self.kill = settings, cfg, db, events, kill
        self.clock, self.data, self.broker, self.swarm, self.spend = clock, data, broker, swarm, spend
        self.mode = settings.mode
        self.risk = RiskEngine(cfg)
        self.executor = Executor(broker, data, db, events, kill, cfg, self.mode, on_closed=self._on_closed)
        self.regime: dict = {}
        self.market_note = ""
        self.last_cycle: dict = {}
        self.account = None
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self._data_failures = 0
        self._reconcile_mismatches = 0
        self._last_snapshot = 0.0
        self._announced: set[str] = set()
        self._state: tuple[float, PortfolioState] | None = None
        self._state_lock = asyncio.Lock()
        self.heartbeat: dict[str, dict] = {}

    # ------------------------------------------------------------------ lifecycle
    async def recover(self) -> None:
        """A crash mid-exit leaves a position marked 'closing' with nobody closing it.
        Hand it back to the exit loop."""
        async with self.db.session() as s:
            stuck = list((await s.execute(select(Position).where(Position.mode == self.mode, Position.status == "closing"))).scalars())
            for p in stuck:
                p.status = "open"
            await s.commit()
        for p in stuck:
            await self.events.emit("warn", "engine.recovered", f"{p.instrument_key} was mid-exit at the last shutdown; exit loop resumes managing it", position_id=p.id)

    async def start(self) -> None:
        await self.recover()
        last = await self.db.kv_get(f"last_market_read:{self.mode}")
        if last and last.get("day") == self.clock.session_date():
            self.regime, self.market_note = last.get("regime") or {}, last.get("market_note") or ""
        await self.events.emit("info", "engine.start", f"engine started: mode={self.mode} data={self.data.name} broker={self.broker.name}",
                               {"mode": self.mode, "kill": self.kill.snapshot()})
        e = self.cfg.engine
        self._tasks = [
            asyncio.create_task(self._loop("entry", e.cycle_seconds, self.entry_cycle)),
            asyncio.create_task(self._loop("exit", e.exit_poll_seconds, self.exit_cycle)),
            asyncio.create_task(self._loop("review", e.position_review_seconds, self.review_cycle)),
            asyncio.create_task(self._loop("watchdog", e.watchdog_seconds, self.watchdog_cycle)),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, *self._bg, return_exceptions=True)
        await self.events.emit("info", "engine.stop", "engine stopped")

    async def _loop(self, name: str, interval: float, fn) -> None:
        while True:
            started = time.monotonic()
            self.heartbeat[name] = {"interval": interval, "last_start": utcnow().isoformat(), "mono": started,
                                    "last_error": self.heartbeat.get(name, {}).get("last_error", "")}
            try:
                await fn()
                self.heartbeat[name]["last_error"] = ""
            except asyncio.CancelledError:
                raise
            except Exception as e:  # a loop must never die; surface and keep going
                log.exception("%s loop error", name)
                self.heartbeat[name]["last_error"] = f"{type(e).__name__}: {e}"
                await self.events.emit("error", f"engine.{name}_error", f"{name} loop: {type(e).__name__}: {e}")
            await asyncio.sleep(max(0.2, interval - (time.monotonic() - started)))

    async def drain(self) -> None:
        """Wait for background work (closes, lessons) to finish."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _once(self, key: str, level: str, kind: str, msg: str) -> None:
        """Emit a state message once until the state changes (avoids log spam)."""
        if key not in self._announced:
            self._announced.add(key)
            await self.events.emit(level, kind, msg)

    def liveness(self) -> dict:
        """Per loop: seconds since it last started a pass, and whether that is overdue.
        A healthy API in front of a wedged engine must not look healthy."""
        now, out = time.monotonic(), {}
        for name, hb in self.heartbeat.items():
            age = now - hb["mono"]
            out[name] = {"seconds_since_tick": round(age, 1), "interval": hb["interval"],
                         "overdue": age > hb["interval"] * 3 + 60, "last_error": hb["last_error"]}
        return out

    # ------------------------------------------------------------------ portfolio state
    async def _day_key(self) -> str:
        return f"day_start:{self.mode}:{self.clock.session_date()}"

    async def portfolio_state(self, max_age: float = 0.0) -> PortfolioState:
        """`max_age` > 0 lets read-only callers (the UI poll) reuse a recent snapshot
        instead of hitting the broker. Trading decisions always use max_age=0."""
        if max_age > 0 and self._state and time.monotonic() - self._state[0] <= max_age:
            return self._state[1]
        async with self._state_lock:  # the loops run concurrently; day-start and high-water updates must not interleave
            state = await self._portfolio_state()
            self._state = (time.monotonic(), state)
            return state

    async def _portfolio_state(self) -> PortfolioState:
        acct = self.account = await self.broker.get_account()
        day = await self.db.kv_get(await self._day_key())
        if day is None:
            day = {"equity": acct.equity}
            await self.db.kv_set(await self._day_key(), day)
        hw_key = f"equity_high_water:{self.mode}"
        hw = (await self.db.kv_get(hw_key, {"equity": acct.equity}))["equity"]
        if acct.equity >= hw:
            hw = acct.equity
            await self.db.kv_set(hw_key, {"equity": hw})

        open_pos = await self.executor.open_positions()
        since = self.clock.trading_day()[1]  # same "today" as the day's starting equity
        async with self.db.session() as s:
            trades_today = (await s.execute(select(func.count()).select_from(Position).where(
                Position.mode == self.mode, Position.entry_ts >= since))).scalar_one()
            day_trades = (await s.execute(select(func.count()).select_from(Position).where(
                Position.mode == self.mode, Position.status == "closed",
                Position.entry_ts >= utcnow() - timedelta(days=7)))).scalar_one()
        return PortfolioState(
            equity=acct.equity, cash=acct.cash, day_start_equity=day["equity"], equity_high_water=hw,
            open_positions=[OpenPos(p.symbol, p.instrument_key,
                                    p.last_price * p.qty * (100 if p.instrument.get("right") else 1)) for p in open_pos],
            trades_today=trades_today, order_times_last_minute=self.executor.orders_last_minute(),
            day_trades_5d=day_trades, llm_spend_today=await self.spend.today(),
        )

    async def _recent_closed(self, limit: int = 6) -> list[dict]:
        async with self.db.session() as s:
            rows = (await s.execute(select(Position).where(
                Position.mode == self.mode, Position.status == "closed",
                Position.exit_ts >= utcnow() - timedelta(hours=18)).order_by(desc(Position.exit_ts)).limit(limit))).scalars()
            return [{"symbol": p.symbol, "direction": "long" if p.direction > 0 else "short",
                     "minutes_ago": round((utcnow() - p.exit_ts).total_seconds() / 60),
                     "pnl_r": round(p.realized_pnl / p.risk_usd, 2) if p.risk_usd else 0.0,
                     "exit_reason": p.exit_reason} for p in rows]

    async def track_record(self) -> dict[str, dict]:
        """Per agent: how often its directional stance matched how the trade turned out."""
        async with self.db.session() as s:
            rows = (await s.execute(
                select(AgentOpinion.agent, AgentOpinion.stance, Position.direction, Position.realized_pnl)
                .join(Position, Position.decision_id == AgentOpinion.decision_id)
                .where(Position.status == "closed", Position.mode == self.mode,
                       AgentOpinion.stance != "neutral", AgentOpinion.error == ""))).all()
        out: dict[str, dict] = {}
        for agent, stance, direction, pnl in rows:
            agreed = (stance == "bullish") == (direction > 0)
            right = agreed == (pnl > 0)
            rec = out.setdefault(agent, {"calls": 0, "right": 0})
            rec["calls"] += 1
            rec["right"] += int(right)
        for rec in out.values():
            rec["hit_rate"] = round(rec["right"] / rec["calls"], 2)
        return out

    async def _lessons_for(self, c: Candidate) -> list[dict]:
        n = self.cfg.agents.lessons_in_prompt
        if n <= 0:
            return []
        async with self.db.session() as s:
            rows = list((await s.execute(select(Lesson).order_by(desc(Lesson.ts)).limit(200))).scalars())
        tags = set(c.hint_setups)
        rows.sort(key=lambda l: (l.symbol == c.symbol, len(tags & set(l.tags)), l.ts), reverse=True)
        return [{"symbol": l.symbol, "vehicle": l.vehicle, "outcome_r": l.outcome_r, "tags": l.tags,
                 "situation": l.situation, "what_happened": l.what_happened, "lesson": l.lesson} for l in rows[:n]]

    def _envelope(self, st: PortfolioState) -> dict:
        r, ex, o = self.cfg.risk, self.cfg.equity_exits, self.cfg.options
        return {
            "shares": {"max_stop_pct_from_entry": ex.max_stop_pct, "profit_ceiling_pct": ex.take_profit_ceiling_pct,
                       "full_size_risk_usd": round(st.equity * r.max_risk_per_trade_pct / 100),
                       "max_position_usd": round(st.equity * r.max_position_notional_pct / 100), "max_hold_minutes": ex.max_hold_minutes},
            "options": {"enabled": o.enabled, "full_size_premium_usd": round(st.equity * r.max_option_premium_pct / 100),
                        "hard_premium_stop_pct": o.stop_loss_pct, "profit_ceiling_pct": o.take_profit_ceiling_pct,
                        "max_hold_minutes": o.max_hold_minutes},
            "position_slots_left": r.max_open_positions - len(st.open_positions),
            "note": "Short stock and short options do not exist here. All positions are closed before the bell.",
        }

    # ------------------------------------------------------------------ entry loop
    async def _entry_gate(self, st: SessionState) -> str | None:
        if not st.is_open:
            return "market closed"
        if self.kill.entries_blocked:
            return f"kill switch {self.kill.state}"
        if not st.entries_allowed:
            return f"session phase {st.phase}"
        if await self.spend.today() >= self.cfg.risk.max_llm_spend_per_day_usd:
            return "daily LLM budget reached"
        return None

    async def entry_cycle(self) -> None:
        sess = self.clock.state()
        blocked = await self._entry_gate(sess)
        if blocked:
            self.last_cycle = {"ts": utcnow().isoformat(), "skipped": blocked}
            await self._once(f"entry:{blocked}", "info", "engine.entries_paused", f"entries paused: {blocked}")
            return
        self._announced = {k for k in self._announced if not k.startswith("entry:")}

        cycle_id, started = new_id(), time.monotonic()
        state = await self.portfolio_state()
        if len(state.open_positions) >= self.cfg.risk.max_open_positions:
            self.last_cycle = {"ts": utcnow().isoformat(), "skipped": "all position slots in use"}
            return

        uni = self.cfg.universe
        symbols = sorted(set(uni.symbols) | set(uni.regime_symbols))
        try:
            quotes, bars = await asyncio.gather(
                self.data.get_quotes(symbols),
                self.data.get_bars(symbols, self.cfg.engine.bar_interval, self.cfg.engine.bar_lookback))
            self._data_failures = 0
        except Exception as e:
            self._data_failures += 1
            await self.events.emit("error", "data.error", f"market data failed: {type(e).__name__}: {e}")
            return
        table = build_table(bars, quotes, uni)
        if not table:
            await self.events.emit("warn", "scan.empty", "no tradable symbols in the feature table")
            return
        mtc = None if sess.seconds_to_close is None else sess.seconds_to_close / 60

        above = [c for c in table if c.signals["vwap_dev_pct"] > 0]
        breadth = {"symbols": len(table), "pct_above_vwap": round(100 * len(above) / len(table), 1),
                   "pct_uptrend": round(100 * sum(c.signals["ema_trend_pct"] > 0 for c in table) / len(table), 1),
                   "median_roc12_pct": round(sorted(c.signals["roc12_pct"] for c in table)[len(table) // 2], 3)}
        self.regime, _ = await self.swarm.regime([c for c in table if c.symbol in uni.regime_symbols], breadth)

        recent_closed = await self._recent_closed()
        held = sorted({p.symbol for p in state.open_positions})
        slots = self.cfg.risk.max_open_positions - len(state.open_positions)
        picks, self.market_note, scout_res = await self.swarm.scout(
            table[: self.cfg.engine.scout_table_rows], open_symbols=held, recently_closed=recent_closed,
            regime=self.regime, max_picks=min(self.cfg.engine.max_candidates_per_cycle, slots), minutes_to_close=mtc)
        # the dashboard shows the latest read; keep it across restarts within the same trading day
        await self.db.kv_set(f"last_market_read:{self.mode}", {"day": self.clock.session_date(), "regime": self.regime,
                                                                "market_note": self.market_note, "ts": utcnow().isoformat()})
        await self.events.emit(
            "info", "cycle.scan",
            f"scanned {len(table)} symbols | regime {self.regime.get('label')} ({self.regime.get('bias')}) | scout picked "
            + (", ".join(c.symbol for c in picks) or "nothing") + (f" | {self.market_note}" if self.market_note else ""),
            {"cycle_id": cycle_id, "regime": self.regime, "breadth": breadth,
             "picks": [{"symbol": c.symbol, "reason": c.scout_reason} for c in picks], "scout_error": scout_res.error})

        record = await self.track_record() if self.cfg.agents.show_agent_track_record else {}
        sem = asyncio.Semaphore(max(1, self.cfg.engine.parallel_candidates))

        async def run(c: Candidate):
            async with sem:
                return await self._deliberate(c, cycle_id, state, recent_closed, record, mtc)

        deliberated = await asyncio.gather(*(run(c) for c in picks))
        # Execute one at a time so each order is checked against the book as it now stands.
        executed = 0
        for item in deliberated:
            if item is not None:
                executed += int(await self._plan_and_execute(*item))
        self.last_cycle = {"ts": utcnow().isoformat(), "cycle_id": cycle_id, "scanned": len(table),
                           "picked": [c.symbol for c in picks], "entered": executed,
                           "seconds": round(time.monotonic() - started, 1)}

    async def _deliberate(self, c: Candidate, cycle_id: str, state: PortfolioState, recent_closed: list[dict],
                          record: dict, mtc: float | None):
        decision_id = new_id()
        if hasattr(self.data, "get_catalysts"):
            try:
                c = c.model_copy(update={"catalysts": await self.data.get_catalysts(c.symbol)})
            except Exception as e:  # context, not a gate: deliberate without it and say so
                await self.events.emit("warn", "data.catalysts_error", f"no news/earnings for {c.symbol}: {type(e).__name__}: {e}")
        shortlist = []
        if self.cfg.options.enabled:
            try:
                chain = await self.data.get_option_chain(c.symbol, self.cfg.options.min_dte, self.cfg.options.max_dte)
                shortlist = build_shortlist(chain, self.cfg, max_premium_usd=min(
                    state.equity * self.cfg.risk.max_option_premium_pct / 100, state.cash))
            except Exception as e:
                await self.events.emit("warn", "data.chain_error", f"no option chain for {c.symbol}: {e}", decision_id=decision_id)

        open_rows = await self.executor.open_positions()
        losses = 0
        for t in recent_closed:
            if t["pnl_r"] >= 0:
                break
            losses += 1
        context = {
            "portfolio": {
                "equity": round(state.equity), "day_pnl_pct": round(state.day_pnl_pct, 2),
                "exposure_pct": round(state.exposure / state.equity * 100, 1) if state.equity else 0,
                "open_positions": [{"symbol": p.symbol, "instrument": p.instrument_key,
                                    "direction": "long" if p.direction > 0 else "short",
                                    "pnl_r": round(pnl_r(p, p.last_price), 2)} for p in open_rows],
                "trades_today": state.trades_today, "consecutive_losses": losses, "recent_closed": recent_closed,
            },
            "track_record": record, "lessons": await self._lessons_for(c), "envelope": self._envelope(state),
        }
        async with self.db.session() as s:
            # The record keeps the shortlist too, so pm.contract_index can always be resolved in the trace.
            s.add(Decision(id=decision_id, cycle_id=cycle_id, mode=self.mode, symbol=c.symbol, status="deliberating",
                           candidate=c.model_dump(mode="json"), regime=self.regime, scout_reason=c.scout_reason,
                           context={**context, "option_shortlist": shortlist_payload(shortlist, self.cfg.execution.option_entry_offset_pct)}))
            await s.commit()

        opinions, pm, pm_error = await self.swarm.deliberate(
            c, regime=self.regime, shortlist=shortlist, pm_context=context, minutes_to_close=mtc, decision_id=decision_id)
        await self._save_opinions(decision_id, opinions)
        stances = " ".join(f"{o.agent}:{o.stance.value[:4]}{o.confidence:.2f}" for o in opinions)
        if pm.action != "enter":
            await self._finish(decision_id, "skipped_by_pm", pm=pm.model_dump(mode="json"),
                               outcome=f"PM skipped: {pm.summary}" + (f" [error: {pm_error}]" if pm_error else ""))
            await self.events.emit("warn" if pm_error else "info", "decision.skip", f"SKIP {c.symbol} | {stances} | {pm.summary}",
                                   {"pm_error": pm_error}, decision_id=decision_id)
            return None
        await self.events.emit(
            "info", "decision.enter",
            f"PM wants {pm.direction.upper()} {c.symbol} via {pm.vehicle.value if pm.vehicle else '?'} conf {pm.confidence:.2f} | {stances} | {pm.summary}",
            decision_id=decision_id)
        return c, pm, shortlist, decision_id

    async def _plan_and_execute(self, c: Candidate, pm, shortlist, decision_id: str) -> bool:
        pm_json = pm.model_dump(mode="json")
        try:
            fresh = (await self.data.get_quotes([c.symbol]))[c.symbol]
            c = c.model_copy(update={"quote": fresh})
            oq = None
            if pm.vehicle and pm.vehicle.value != "shares" and pm.contract_index is not None and 0 <= pm.contract_index < len(shortlist):
                oq = await self.data.get_quote(shortlist[pm.contract_index].instrument)
            state = await self.portfolio_state()
            proposal = build_proposal(pm, c, shortlist, equity=state.equity, cash=state.cash, cfg=self.cfg, option_quote=oq)
        except Unbuildable as e:
            await self._finish(decision_id, "unbuildable", pm=pm_json, outcome=f"plan could not be built: {e}")
            await self.events.emit("warn", "decision.unbuildable", f"{c.symbol}: plan discarded: {e}", decision_id=decision_id)
            return False
        except Exception as e:
            await self._finish(decision_id, "failed", pm=pm_json, outcome=f"error building plan: {e}")
            await self.events.emit("error", "decision.failed", f"{c.symbol}: {type(e).__name__}: {e}", decision_id=decision_id)
            return False

        sess = self.clock.state()
        verdict: RiskVerdict = self.risk.evaluate(proposal, state, kill_blocked=self.kill.entries_blocked,
                                                  entries_allowed=sess.entries_allowed)
        prop_json, risk_json = proposal.model_dump(mode="json"), verdict.model_dump(mode="json")
        for note in proposal.clamps:
            await self.events.emit("warn", "plan.clamped", f"{c.symbol}: {note}", decision_id=decision_id)
        if not verdict.approved:
            why = "; ".join(f"{f.rule}: {f.detail}" for f in verdict.failures)
            await self._finish(decision_id, "rejected_by_risk", pm=pm_json, proposal=prop_json, risk=risk_json,
                               outcome=f"risk envelope rejected: {why}")
            await self.events.emit("warn", "risk.rejected", f"RISK REJECTED {c.symbol}: {why}", decision_id=decision_id)
            return False

        pos = await self.executor.enter(proposal, decision_id=decision_id, candidate=c, pm=pm)
        if pos is None:
            await self._finish(decision_id, "unfilled", pm=pm_json, proposal=prop_json, risk=risk_json,
                               outcome="entry order did not fill; not chased")
            return False
        await self._finish(decision_id, "executed", pm=pm_json, proposal=prop_json, risk=risk_json,
                           outcome=f"opened {pos.qty} {pos.instrument_key} @ {pos.entry_price:.2f}")
        return True

    async def _save_opinions(self, decision_id: str, opinions: list[Opinion]) -> None:
        async with self.db.session() as s:
            for o in opinions:
                s.add(AgentOpinion(decision_id=decision_id, agent=o.agent, model=o.model, stance=o.stance.value,
                                   confidence=o.confidence, thesis=o.thesis, evidence=o.evidence,
                                   invalidation=o.invalidation, latency_ms=o.latency_ms, tokens_in=o.tokens_in,
                                   tokens_out=o.tokens_out, cost_usd=o.cost_usd, error=o.error, raw=o.raw))
            await s.commit()

    async def _finish(self, decision_id: str, status: str, **fields) -> None:
        async with self.db.session() as s:
            d = await s.get(Decision, decision_id)
            d.status = status
            for k, v in fields.items():
                setattr(d, k, v)
            await s.commit()

    # ------------------------------------------------------------------ exit loop (no LLM)
    async def exit_cycle(self) -> None:
        sess = self.clock.state()
        positions = [p for p in await self.executor.open_positions() if p.status == "open"]
        if not positions:
            return
        if not sess.is_open:
            if self.kill.must_flatten:
                await self._once("flatten_closed", "critical", "killswitch.waiting",
                                 f"FLATTEN is set but the market is closed: {len(positions)} positions will be sold at the next open")
            return
        self._announced.discard("flatten_closed")

        try:  # one batched call for every underlying; options then need one call each
            underlying = await self.data.get_quotes(sorted({p.symbol for p in positions}))
        except Exception as e:
            self._data_failures += 1
            await self.events.emit("error", "data.error", f"quotes failed for open positions: {type(e).__name__}: {e}")
            return
        for p in positions:
            inst = Instrument.model_validate(p.instrument)
            try:
                q = await self.data.get_quote(inst) if inst.is_option else underlying[p.symbol]
                und = underlying[p.symbol].mid if inst.is_option else None
                self._data_failures = 0
            except Exception as e:
                self._data_failures += 1
                await self.events.emit("error", "data.error", f"no quote for {p.instrument_key}: {e}", position_id=p.id)
                continue
            price = q.bid if q.bid > 0 else q.last
            async with self.db.session() as s:
                row = await s.get(Position, p.id)
                if row.status != "open":
                    continue
                row.last_price = price
                row.high_water, row.low_water = max(row.high_water, price), min(row.low_water, price)
                await s.commit()
                # Judge the exit on the row as it is NOW: the position-manager agent may have
                # tightened the stop since this cycle's list was loaded.
                view = pos_view(row)
            hit = check_exit(view, price, und, now=utcnow(), cfg=self.cfg,
                             kill_flatten=self.kill.must_flatten, session_flatten=sess.flatten_now)
            if hit:
                self._spawn(self.executor.close(p.id, hit[0], hit[1]))

    # ------------------------------------------------------------------ review loop (position-manager agent)
    async def review_cycle(self) -> None:
        sess = self.clock.state()
        if not sess.is_open or sess.flatten_now or self.kill.must_flatten:
            return
        positions = [p for p in await self.executor.open_positions() if p.status == "open"]
        if not positions:
            return
        symbols = sorted({p.symbol for p in positions})
        quotes, bars = await asyncio.gather(
            self.data.get_quotes(symbols),
            self.data.get_bars(symbols, self.cfg.engine.bar_interval, self.cfg.engine.bar_lookback))
        table = {c.symbol: c for c in build_table(bars, quotes, self.cfg.universe.model_copy(update={"max_spread_pct": 100, "min_price": 0}))}
        mtc = None if sess.seconds_to_close is None else sess.seconds_to_close / 60
        await asyncio.gather(*(self._review_one(p, table.get(p.symbol), mtc) for p in positions))

    async def _review_one(self, p: Position, c: Candidate | None, mtc: float | None) -> None:
        if c is None:
            return
        inst = Instrument.model_validate(p.instrument)
        price, und = p.last_price, c.quote.mid
        async with self.db.session() as s:
            prior = list((await s.execute(select(PositionReview).where(PositionReview.position_id == p.id)
                                          .order_by(desc(PositionReview.ts)).limit(3))).scalars())
        snapshot = {
            "symbol": p.symbol, "instrument": p.instrument_key, "is_option": inst.is_option,
            "direction": "long" if p.direction > 0 else "short", "qty": p.qty, "entry": p.entry_price,
            "price": price, "underlying_price": round(und, 2), "pnl_pct": round((price / p.entry_price - 1) * 100, 2),
            "pnl_r": round(pnl_r(p, price), 2), "best_r": round(pnl_r(p, p.high_water), 2), "worst_r": round(pnl_r(p, p.low_water), 2),
            "stop": p.und_stop if inst.is_option else p.stop_price, "target": p.und_target if inst.is_option else p.target_price,
            "levels_are_on": "underlying" if inst.is_option else "shares",
            "minutes_held": round((utcnow() - p.entry_ts).total_seconds() / 60), "max_hold_minutes": p.max_hold_minutes,
            "thesis": p.thesis, "invalidation": p.invalidation,
        }
        payload = {"position": snapshot, "signals": c.signals, "recent": c.recent, "regime": self.regime,
                   "minutes_to_close": None if mtc is None else round(mtc),
                   "reviews": [{"minutes_ago": round((utcnow() - r.ts).total_seconds() / 60), "action": r.action,
                                "reasoning": r.reasoning} for r in prior]}
        act, res = await self.swarm.review_position(payload, p.id)
        changes, notes = apply_review(pos_view(p), act, price, und, self.cfg)
        async with self.db.session() as s:
            if changes:
                row = await s.get(Position, p.id)
                for k, v in changes.items():
                    setattr(row, k, v)
            s.add(PositionReview(position_id=p.id, model=res.model, action=act.action, reasoning=act.reasoning,
                                 confidence=act.confidence, requested={"new_stop": act.new_stop, "new_target": act.new_target},
                                 applied={"changes": changes, "notes": notes}, context=snapshot, cost_usd=res.cost_usd, error=res.error))
            await s.commit()
        if act.action == "exit":
            await self.events.emit("info", "review.exit", f"position manager exits {p.instrument_key}: {act.reasoning}", position_id=p.id, decision_id=p.decision_id)
            self._spawn(self.executor.close(p.id, OrderReason.AGENT_EXIT, act.reasoning[:400]))
        elif changes or notes:
            await self.events.emit("info", "review.adjust", f"position manager on {p.instrument_key}: {changes or 'no change'}"
                                   + (f" ({'; '.join(notes)})" if notes else "") + f" | {act.reasoning}",
                                   {"changes": changes, "notes": notes}, position_id=p.id, decision_id=p.decision_id)

    # ------------------------------------------------------------------ post-trade lessons
    async def _on_closed(self, position_id: str) -> None:
        self._spawn(self._write_lesson(position_id))

    async def _write_lesson(self, position_id: str) -> None:
        async with self.db.session() as s:
            p = await s.get(Position, position_id)
            d = await s.get(Decision, p.decision_id)
            ops = list((await s.execute(select(AgentOpinion).where(AgentOpinion.decision_id == p.decision_id))).scalars())
            reviews = list((await s.execute(select(PositionReview).where(PositionReview.position_id == p.id).order_by(PositionReview.ts))).scalars())
        vehicle = (d.pm or {}).get("vehicle") or "shares"
        r = round(p.realized_pnl / p.risk_usd, 2) if p.risk_usd else 0.0
        payload = {
            "trade": {"symbol": p.symbol, "vehicle": vehicle, "direction": "long" if p.direction > 0 else "short",
                      "entry": p.entry_price, "exit": p.exit_price, "exit_reason": p.exit_reason, "exit_detail": p.exit_detail,
                      "pnl_r": r, "mfe_r": round(pnl_r(p, p.high_water), 2), "mae_r": round(pnl_r(p, p.low_water), 2),
                      "minutes_held": round((p.exit_ts - p.entry_ts).total_seconds() / 60)},
            "scout_reason": d.scout_reason, "regime_at_entry": d.regime, "signals_at_entry": (d.candidate or {}).get("signals"),
            "hint_setups": (d.candidate or {}).get("hint_setups"),
            "opinions": [{"agent": o.agent, "stance": o.stance, "confidence": o.confidence, "thesis": o.thesis} for o in ops],
            "plan": d.pm, "clamps": (d.proposal or {}).get("clamps"),
            "reviews": [{"action": x.action, "reasoning": x.reasoning, "applied": x.applied} for x in reviews],
        }
        lesson, res = await self.swarm.write_lesson(payload, position_id)
        if lesson is None:
            return
        async with self.db.session() as s:
            s.add(Lesson(position_id=p.id, symbol=p.symbol, vehicle=vehicle, outcome_r=r, model=res.model, cost_usd=res.cost_usd, **lesson))
            await s.commit()
        await self.events.emit("info", "lesson.written", f"lesson from {p.symbol} ({r:+.2f}R): {lesson['lesson']}", position_id=p.id)

    # ------------------------------------------------------------------ watchdog
    async def watchdog_cycle(self) -> None:
        if await self.kill.check_files():
            return
        sess = self.clock.state()
        if not sess.is_open:
            return
        r = self.cfg.risk
        state = await self.portfolio_state()
        if time.monotonic() - self._last_snapshot >= 60:
            self._last_snapshot = time.monotonic()
            async with self.db.session() as s:
                s.add(EquitySnapshot(mode=self.mode, equity=state.equity, cash=state.cash,
                                     day_pnl=state.equity - state.day_start_equity))
                await s.commit()

        if state.day_pnl_pct <= -r.max_daily_loss_pct:
            await self.kill.trip(HALTED, f"daily loss {state.day_pnl_pct:.2f}% reached the {r.max_daily_loss_pct}% limit", "watchdog")
        if state.drawdown_pct >= r.max_drawdown_pct:
            await self.kill.trip(FLATTEN, f"drawdown {state.drawdown_pct:.2f}% reached the {r.max_drawdown_pct}% limit", "watchdog")
        if self.executor.broker_errors >= MAX_BROKER_ERRORS:
            await self.kill.trip(HALTED, f"{self.executor.broker_errors} consecutive broker errors", "watchdog")
        if self._data_failures >= MAX_DATA_FAILURES:
            await self.kill.trip(HALTED, f"{self._data_failures} consecutive market-data failures", "watchdog")

        async with self.db.session() as s:
            errs = list((await s.execute(select(LLMCall.error).where(LLMCall.ts >= utcnow() - timedelta(minutes=15))
                                         .order_by(desc(LLMCall.id)).limit(LLM_FAILURE_WINDOW))).scalars())
        if len(errs) == LLM_FAILURE_WINDOW and all(errs):
            await self.kill.trip(HALTED, f"the last {LLM_FAILURE_WINDOW} model calls all failed", "watchdog")

        await self._reconcile()

    async def _reconcile(self) -> None:
        """The ledger and the broker must agree on what is held."""
        ledger: dict[str, int] = {}
        for p in await self.executor.open_positions():
            if p.status == "closing":
                return  # an exit is in flight; compare next time
            ledger[p.instrument_key] = ledger.get(p.instrument_key, 0) + p.qty
        held = {bp.instrument.key: bp.qty for bp in await self.broker.get_positions() if bp.qty}
        if ledger == held:
            self._reconcile_mismatches = 0
            return
        self._reconcile_mismatches += 1
        if self._reconcile_mismatches >= MAX_RECONCILE_MISMATCHES:
            await self.kill.trip(HALTED, f"ledger and broker disagree on positions: ledger={ledger} broker={held}", "watchdog")

    # ------------------------------------------------------------------ manual controls
    async def manual_close(self, position_id: str, note: str = "") -> bool:
        return await self.executor.close(position_id, OrderReason.MANUAL, note or "closed manually")
