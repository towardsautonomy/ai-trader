"""REST + WebSocket API for the monitoring terminal."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.core.events import event_to_dict
from app.core.killswitch import FLATTEN, HALTED
from app.core.types import utcnow
from app.db.models import (
    AgentOpinion, Decision, EquitySnapshot, Event, Lesson, LLMCall, Order, Position, PositionReview,
)
from app.engine.orchestrator import pnl_r
from app.config import get_settings
from app.config import LIVE_CONFIRM_PHRASE
from app.modeswitch import consume_code, read_choice, write_choice
from app.readiness import RobinhoodProbe, agent_models, live_readiness
from app.runtime import Runtime, build


def cors_regex(setting: str) -> str:
    """Browser origins allowed to call the API. localhost always; then each comma-separated
    entry: an exact origin, `*:PORT` for any host on that port (a machine with several
    addresses, a VPN, a hostname), or `*` for anything."""
    import re
    parts = [r"https?://(localhost|127\.0\.0\.1)(:\d+)?"]
    for raw in setting.split(","):
        o = raw.strip().rstrip("/")
        if not o:
            continue
        if o == "*":
            parts.append(r".*")
        elif o.startswith("*:"):
            parts.append(rf"https?://[^/]+:{int(o[2:])}")
        else:
            parts.append(re.escape(o))
    return "^(" + "|".join(parts) + ")$"


def _cols(row, *skip: str) -> dict:
    out = {}
    for c in row.__table__.columns:
        if c.name in skip:
            continue
        v = getattr(row, c.name)
        out[c.name] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def position_dict(p: Position) -> dict:
    d = _cols(p)
    mult = 100 if p.instrument.get("right") else 1
    d["unrealized_pnl"] = round((p.last_price - p.entry_price) * p.qty * mult, 2) if p.status != "closed" else 0.0
    d["pnl_r"] = round(pnl_r(p, p.last_price), 2) if p.status != "closed" else (round(p.realized_pnl / p.risk_usd, 2) if p.risk_usd else 0.0)
    d["is_option"] = bool(p.instrument.get("right"))
    return d


class KillBody(BaseModel):
    level: str = "halt"  # halt | flatten
    reason: str = "manual"


class RearmBody(BaseModel):
    confirm: str


class ModeBody(BaseModel):
    mode: str            # paper | live
    confirm: str = ""    # live: the exact confirmation phrase
    code: str = ""       # live: one-time code from `./trader live-code` on the server


def reexec() -> None:
    """Replace this process with a fresh one, so the new mode goes through every startup gate."""
    import os
    import sys
    os.execv(sys.executable, [sys.executable, "-m", "app.cli", *sys.argv[1:]])


def create_app(runtime: Runtime | None = None, start_engine: bool = True, restart=reexec) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["rt"] = runtime or await build()
        state["probe"] = RobinhoodProbe(state["rt"])
        if start_engine:
            await state["rt"].engine.start()
        yield
        await state["probe"].close()
        if start_engine:
            await state["rt"].shutdown()

    app = FastAPI(title="AI Trader", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origin_regex=cors_regex((runtime.settings if runtime else get_settings()).cors_origins),
                       allow_methods=["*"], allow_headers=["*"])

    def rt() -> Runtime:
        return state["rt"]

    def authorised(x_api_token: str = Header(default="")) -> None:
        token = rt().settings.api_token
        if token and x_api_token != token:
            raise HTTPException(401, "bad or missing X-API-Token")

    # ------------------------------------------------------------------ status
    @app.get("/api/status")
    async def status():
        r = rt()
        e = r.engine
        sess = e.clock.state()
        try:
            st = await e.portfolio_state(max_age=3.0)
            account = {"equity": round(st.equity, 2), "cash": round(st.cash, 2),
                       "day_start_equity": round(st.day_start_equity, 2),
                       "day_pnl": round(st.equity - st.day_start_equity, 2), "day_pnl_pct": round(st.day_pnl_pct, 3),
                       "drawdown_pct": round(st.drawdown_pct, 3), "exposure": round(st.exposure, 2),
                       "exposure_pct": round(st.exposure / st.equity * 100, 2) if st.equity else 0.0,
                       "open_positions": len(st.open_positions), "trades_today": st.trades_today,
                       "llm_spend_today": round(st.llm_spend_today, 4)}
            error = ""
        except Exception as ex:  # the terminal must still load (and the kill switch still work) if the broker is down
            account, error = {}, f"{type(ex).__name__}: {ex}"
        risk = r.cfg.risk
        choice = read_choice(r.settings.data_dir)
        return {
            "mode": r.settings.mode, "mode_source": r.settings.mode_source, "live_refused": choice.get("live_refused", ""),
            "demo": r.settings.ignore_clock or e.data.name == "synthetic",
            "data_source": e.data.name, "broker": e.broker.name,
            "llm": r.llm_label, "models": agent_models(r),
            "kill": r.kill.snapshot(), "account": account, "account_error": error,
            "session": {"is_open": sess.is_open, "phase": sess.phase, "entries_allowed": sess.entries_allowed,
                        "seconds_to_close": sess.seconds_to_close,
                        "next_open": sess.next_open.isoformat() if sess.next_open else None},
            "limits": {"max_daily_loss_pct": risk.max_daily_loss_pct, "max_drawdown_pct": risk.max_drawdown_pct,
                       "max_open_positions": risk.max_open_positions, "max_total_exposure_pct": risk.max_total_exposure_pct,
                       "max_trades_per_day": risk.max_trades_per_day, "max_llm_spend_per_day_usd": risk.max_llm_spend_per_day_usd},
            "engine": e.liveness(), "regime": e.regime, "market_note": e.market_note, "last_cycle": e.last_cycle, "server_time": utcnow().isoformat(),
        }

    # ------------------------------------------------------------------ broker + mode
    @app.get("/api/robinhood")
    async def robinhood(fresh: bool = False):
        return await state["probe"].snapshot(fresh=fresh)

    @app.get("/api/live/readiness")
    async def readiness():
        return await live_readiness(rt(), state["probe"])

    @app.post("/api/mode", dependencies=[Depends(authorised)])
    async def set_mode(body: ModeBody):
        r = rt()
        if body.mode not in ("paper", "live"):
            raise HTTPException(400, "mode must be paper or live")
        if r.settings.ignore_clock or r.engine.data.name == "synthetic":
            raise HTTPException(409, "the demo cannot switch modes: restart without --demo")
        if body.mode == r.settings.mode:
            raise HTTPException(409, f"already in {body.mode} mode")
        if body.mode == "live":
            ready = await live_readiness(r, state["probe"])
            if not ready["ready"]:
                failing = [c["label"] for c in ready["checks"] if c["blocking"] and not c["ok"]]
                raise HTTPException(409, f"not ready for live: {', '.join(failing)}")
            if body.confirm.strip() != LIVE_CONFIRM_PHRASE:
                raise HTTPException(400, "the confirmation phrase does not match")
            if not consume_code(r.settings.data_dir, body.code):
                raise HTTPException(403, "wrong or expired code: run `./trader live-code` on the server for a new one")
            write_choice(r.settings.data_dir, "live", live_confirm=LIVE_CONFIRM_PHRASE)
        else:
            async with r.db.session() as sess:
                open_live = (await sess.execute(select(func.count()).select_from(Position).where(
                    Position.mode == "live", Position.status != "closed"))).scalar_one()
            if open_live:
                raise HTTPException(409, f"{open_live} live positions are open: close them (or FLATTEN) before leaving live mode")
            write_choice(r.settings.data_dir, "paper")
        await r.events.emit("critical", "engine.mode_switch", f"switching to {body.mode.upper()} from the dashboard; the engine restarts")

        async def later():
            await asyncio.sleep(0.5)  # let this response reach the browser
            await state["probe"].close()
            await r.shutdown()
            restart()
        state["restart_task"] = asyncio.create_task(later())
        return {"switching_to": body.mode, "restarting": True}

    @app.get("/api/config")
    async def config():
        return rt().cfg.model_dump(mode="json")

    # ------------------------------------------------------------------ positions
    @app.get("/api/positions")
    async def positions(status: str = "open", limit: int = 100):
        async with rt().db.session() as s:
            q = select(Position).where(Position.mode == rt().settings.mode)
            q = q.where(Position.status != "closed") if status == "open" else q.where(Position.status == "closed") if status == "closed" else q
            rows = (await s.execute(q.order_by(desc(Position.entry_ts)).limit(limit))).scalars()
            return [position_dict(p) for p in rows]

    @app.get("/api/positions/{position_id}")
    async def position_trace(position_id: str):
        """Everything behind one position: why it was opened, how it was managed, how it ended."""
        async with rt().db.session() as s:
            p = await s.get(Position, position_id)
            if not p:
                raise HTTPException(404, "no such position")
            reviews = (await s.execute(select(PositionReview).where(PositionReview.position_id == p.id).order_by(PositionReview.ts))).scalars()
            orders = (await s.execute(select(Order).where(Order.position_id == p.id).order_by(Order.ts))).scalars()
            events = (await s.execute(select(Event).where(Event.position_id == p.id).order_by(Event.id))).scalars()
            lesson = (await s.execute(select(Lesson).where(Lesson.position_id == p.id))).scalars().first()
            calls = (await s.execute(select(LLMCall).where(LLMCall.position_id == p.id).order_by(LLMCall.id))).scalars()
            out = {"position": position_dict(p), "reviews": [_cols(x) for x in reviews], "orders": [_cols(x) for x in orders],
                   "events": [event_to_dict(x) for x in events], "lesson": _cols(lesson) if lesson else None,
                   # position_manager reviews and the reviewer's lesson, in order: reviews[i] pairs with the i-th position_manager call
                   "llm_calls": [_cols(c, "prompt", "response") for c in calls]}
        out["decision"] = await decision_trace(p.decision_id)
        return out

    @app.post("/api/positions/{position_id}/close", dependencies=[Depends(authorised)])
    async def close_position(position_id: str):
        async with rt().db.session() as s:
            p = await s.get(Position, position_id)
        if p is None:
            raise HTTPException(404, "no such position")
        if p.status != "open":
            raise HTTPException(409, f"position is {p.status}, not open")
        asyncio.create_task(rt().engine.manual_close(position_id, "closed from the terminal"))
        return {"ok": True, "status": "closing"}

    # ------------------------------------------------------------------ decisions
    @app.get("/api/history")
    async def history(limit: int = 500):
        """Closed trades with exact fills, plus P&L per trading session (the same "day" the
        dashboard's day P&L uses) and running totals."""
        r = rt()
        async with r.db.session() as s:
            rows = list((await s.execute(select(Position).where(Position.mode == r.settings.mode, Position.status == "closed")
                                         .order_by(desc(Position.exit_ts)).limit(limit))).scalars())
        trades, days = [], {}
        for p in rows:
            d = position_dict(p)
            d["day"] = r.engine.clock.trading_day(p.exit_ts)[0] if p.exit_ts else None
            d["hold_minutes"] = round((p.exit_ts - p.entry_ts).total_seconds() / 60, 1) if p.exit_ts and p.entry_ts else None
            trades.append(d)
            day = days.setdefault(d["day"], {"day": d["day"], "trades": 0, "wins": 0, "pnl": 0.0, "best": None, "worst": None})
            day["trades"] += 1
            day["wins"] += p.realized_pnl > 0
            day["pnl"] = round(day["pnl"] + p.realized_pnl, 2)
            day["best"] = max(day["best"], p.realized_pnl) if day["best"] is not None else p.realized_pnl
            day["worst"] = min(day["worst"], p.realized_pnl) if day["worst"] is not None else p.realized_pnl
        ordered = sorted(days.values(), key=lambda x: x["day"] or "")
        running = 0.0
        for day in ordered:
            running = round(running + day["pnl"], 2)
            day["cumulative"] = running
        total = round(sum(t["realized_pnl"] for t in trades), 2)
        return {"mode": r.settings.mode, "trades": trades, "days": list(reversed(ordered)),
                "totals": {"trades": len(trades), "wins": sum(t["realized_pnl"] > 0 for t in trades), "pnl": total}}

    @app.get("/api/decisions")
    async def decisions(limit: int = 50, status: str | None = None, symbol: str | None = None):
        async with rt().db.session() as s:
            q = select(Decision).where(Decision.mode == rt().settings.mode)
            if status:
                q = q.where(Decision.status == status)
            if symbol:
                q = q.where(Decision.symbol == symbol.upper())
            rows = list((await s.execute(q.order_by(desc(Decision.ts)).limit(limit))).scalars())
            ids = [d.id for d in rows]
            ops = (await s.execute(select(AgentOpinion).where(AgentOpinion.decision_id.in_(ids)))).scalars() if ids else []
            by_decision: dict[str, list] = {}
            for o in ops:
                by_decision.setdefault(o.decision_id, []).append({"agent": o.agent, "stance": o.stance, "confidence": o.confidence})
            return [{"id": d.id, "ts": d.ts.isoformat(), "symbol": d.symbol, "status": d.status, "outcome": d.outcome,
                     "scout_reason": d.scout_reason, "pm_action": (d.pm or {}).get("action"), "pm_vehicle": (d.pm or {}).get("vehicle"),
                     "pm_direction": (d.pm or {}).get("direction"), "pm_confidence": (d.pm or {}).get("confidence"),
                     "pm_summary": (d.pm or {}).get("summary"), "stances": by_decision.get(d.id, [])} for d in rows]

    @app.get("/api/decisions/{decision_id}")
    async def decision_trace(decision_id: str):
        """The full reasoning chain for one deliberation."""
        async with rt().db.session() as s:
            d = await s.get(Decision, decision_id)
            if not d:
                raise HTTPException(404, "no such decision")
            ops = (await s.execute(select(AgentOpinion).where(AgentOpinion.decision_id == d.id).order_by(AgentOpinion.id))).scalars()
            orders = (await s.execute(select(Order).where(Order.decision_id == d.id).order_by(Order.ts))).scalars()
            events = (await s.execute(select(Event).where(Event.decision_id == d.id).order_by(Event.id))).scalars()
            calls = (await s.execute(select(LLMCall).where(LLMCall.decision_id == d.id).order_by(LLMCall.id))).scalars()
            pos = (await s.execute(select(Position).where(Position.decision_id == d.id))).scalars().first()
            return {"decision": _cols(d), "opinions": [_cols(o, "raw") for o in ops], "orders": [_cols(o) for o in orders],
                    "events": [event_to_dict(e) for e in events], "position_id": pos.id if pos else None,
                    "llm_calls": [_cols(c, "prompt", "response") for c in calls]}

    @app.get("/api/llm_calls/{call_id}")
    async def llm_call(call_id: int):
        """Exactly what one agent was shown and exactly what it replied."""
        async with rt().db.session() as s:
            c = await s.get(LLMCall, call_id)
            if not c:
                raise HTTPException(404, "no such call")
            return _cols(c)

    # ------------------------------------------------------------------ feeds
    @app.get("/api/events")
    async def events(limit: int = 200, after_id: int = 0, before_id: int | None = None, level: str | None = None,
                     kind: str | None = None, q: str | None = None):
        """Newest `limit` events in (after_id, before_id), oldest first. Page backwards with before_id=<oldest id seen>."""
        text = q
        async with rt().db.session() as s:
            q = select(Event).where(Event.id > after_id)
            if before_id is not None:
                q = q.where(Event.id < before_id)
            if kind:
                q = q.where(Event.kind.like(f"{kind}%"))
            if text:
                q = q.where(Event.message.ilike(f"%{text}%"))
            if level:
                q = q.where(Event.level == level)
            rows = list((await s.execute(q.order_by(desc(Event.id)).limit(limit))).scalars())
            return [event_to_dict(e) for e in reversed(rows)]

    @app.get("/api/orders")
    async def orders(limit: int = 100):
        async with rt().db.session() as s:
            rows = (await s.execute(select(Order).where(Order.mode == rt().settings.mode).order_by(desc(Order.ts)).limit(limit))).scalars()
            return [_cols(o, "raw") for o in rows]

    @app.get("/api/equity")
    async def equity(hours: int = 24):
        async with rt().db.session() as s:
            rows = (await s.execute(select(EquitySnapshot).where(
                EquitySnapshot.mode == rt().settings.mode, EquitySnapshot.ts >= utcnow() - timedelta(hours=hours))
                .order_by(EquitySnapshot.ts))).scalars()
            return [{"ts": x.ts.isoformat(), "equity": x.equity, "day_pnl": x.day_pnl} for x in rows]

    @app.get("/api/agents")
    async def agents():
        record = await rt().engine.track_record()
        since = utcnow() - timedelta(hours=18)
        async with rt().db.session() as s:
            rows = (await s.execute(select(LLMCall.agent, func.count(), func.sum(LLMCall.cost_usd), func.avg(LLMCall.latency_ms),
                                           func.sum(func.iif(LLMCall.error != "", 1, 0)))
                                    .where(LLMCall.ts >= since).group_by(LLMCall.agent))).all()
        usage = {a: {"calls_today": n, "cost_today": round(c or 0, 4), "avg_latency_ms": round(l or 0), "errors_today": int(e or 0)}
                 for a, n, c, l, e in rows}
        from app.agents.swarm import AGENTS
        return [{"agent": a, "model": rt().cfg.agents.model_for(a) if rt().llm_label != "offline-heuristic" else "offline-heuristic",
                 **record.get(a, {"calls": 0, "right": 0, "hit_rate": None}),
                 **usage.get(a, {"calls_today": 0, "cost_today": 0.0, "avg_latency_ms": 0, "errors_today": 0})} for a in AGENTS]

    @app.get("/api/lessons")
    async def lessons(limit: int = 50):
        async with rt().db.session() as s:
            rows = (await s.execute(select(Lesson).order_by(desc(Lesson.ts)).limit(limit))).scalars()
            return [_cols(x) for x in rows]

    @app.get("/api/stats")
    async def stats():
        async with rt().db.session() as s:
            rows = list((await s.execute(select(Position).where(Position.mode == rt().settings.mode, Position.status == "closed"))).scalars())
        wins = [p for p in rows if p.realized_pnl > 0]
        losses = [p for p in rows if p.realized_pnl <= 0]
        rs = [p.realized_pnl / p.risk_usd for p in rows if p.risk_usd]
        by_reason: dict[str, dict] = {}
        for p in rows:
            b = by_reason.setdefault(p.exit_reason, {"count": 0, "pnl": 0.0})
            b["count"] += 1
            b["pnl"] = round(b["pnl"] + p.realized_pnl, 2)
        gross_loss = -sum(p.realized_pnl for p in losses)
        return {"trades": len(rows), "wins": len(wins), "win_rate": round(len(wins) / len(rows), 3) if rows else None,
                "total_pnl": round(sum(p.realized_pnl for p in rows), 2), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
                "profit_factor": round(sum(p.realized_pnl for p in wins) / gross_loss, 2) if gross_loss > 0 else None,
                "by_exit_reason": by_reason}

    # ------------------------------------------------------------------ kill switch
    @app.post("/api/kill", dependencies=[Depends(authorised)])
    async def kill(body: KillBody):
        if body.level not in ("halt", "flatten"):
            raise HTTPException(400, "level must be 'halt' or 'flatten'")
        changed = await rt().kill.trip(FLATTEN if body.level == "flatten" else HALTED, body.reason or "manual", "api")
        return {"changed": changed, "kill": rt().kill.snapshot()}

    @app.post("/api/kill/rearm", dependencies=[Depends(authorised)])
    async def rearm(body: RearmBody):
        try:
            await rt().kill.rearm(body.confirm, "api")
        except PermissionError as e:
            raise HTTPException(400, str(e))
        return {"kill": rt().kill.snapshot()}

    # ------------------------------------------------------------------ live feed
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q = rt().events.subscribe()
        try:
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt().events.unsubscribe(q)

    return app
