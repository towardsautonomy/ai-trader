"""What the dashboard needs to show about the broker connection and whether live trading
could start right now. Every check is evaluated against the real systems, never assumed."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import func, select

from app.agents.swarm import AGENTS
from app.broker.robinhood_mcp import RobinhoodBroker, RobinhoodMCP, schema_digest
from app.db.models import Position
from app.runtime import VERIFY_KEY, Runtime, unresolved_models

CACHE_SECONDS = 60


def agent_models(rt: Runtime) -> list[dict]:
    a = rt.cfg.agents
    bad = unresolved_models(rt.settings, rt.cfg)
    out = []
    for name in AGENTS:
        m = a.model_for(name)
        local = m.startswith("local/")
        thinking = (a.thinking.get(name) or rt.settings.local_llm_thinking) if local else ""
        out.append({"agent": name, "model": m, "provider": "local" if local else "openrouter",
                    "thinking": "" if thinking in ("", "none") else thinking, "resolved": name not in bad})
    return out


class RobinhoodProbe:
    """Robinhood account facts for the dashboard, cached. Uses the engine's own connection when
    the engine runs on Robinhood, otherwise a separate read-only one."""

    def __init__(self, rt: Runtime):
        self.rt = rt
        self._own: RobinhoodMCP | None = None
        self._cache: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    def _broker(self) -> RobinhoodBroker:
        if isinstance(self.rt.engine.data, RobinhoodBroker):
            return self.rt.engine.data
        if self._own is None:
            self._own = RobinhoodMCP(self.rt.settings.robinhood_mcp_url, self.rt.settings.data_dir)
            self._broker_own = RobinhoodBroker(self._own)
        return self._broker_own

    async def close(self) -> None:
        if self._own:
            await self._own.close()

    async def snapshot(self, fresh: bool = False) -> dict:
        async with self._lock:
            if self._cache and not fresh and time.monotonic() - self._cache[0] < CACHE_SECONDS:
                return self._cache[1]
            snap = await self._snapshot()
            self._cache = (time.monotonic(), snap)
            return snap

    async def _snapshot(self) -> dict:
        s = self.rt.settings
        mcp = RobinhoodMCP(s.robinhood_mcp_url, s.data_dir)
        verified = await self.rt.db.kv_get(VERIFY_KEY)
        out: dict[str, Any] = {
            "logged_in": mcp.logged_in, "verified_at": (verified or {}).get("at") or ("yes" if verified else None),
            "in_use": self.rt.engine.data.name == "robinhood", "broker_in_use": self.rt.engine.broker.name == "robinhood",
            "account": None, "balance": None, "positions": None, "schemas_current": None, "error": "",
            "checked_at": time.time(),
        }
        if not mcp.logged_in:
            return out
        b = self._broker()
        try:
            a = await b.agentic_account()
            out["account"] = {"last4": str(a.get("account_number", ""))[-4:], "type": a.get("type"),
                              "option_level": a.get("option_level"), "options_ok": await b.options_allowed()}
            acct = await b.get_account()
            out["balance"] = {"equity": acct.equity, "cash": acct.cash, "buying_power": acct.buying_power}
            out["positions"] = [{"key": p.instrument.key, "qty": p.qty} for p in await b.get_positions()]
            if verified:
                out["schemas_current"] = schema_digest(await b.mcp.list_tools()) == verified.get("digest")
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out


def _check(cid: str, label: str, ok: bool | None, detail: str, fix: str = "", blocking: bool = True) -> dict:
    return {"id": cid, "label": label, "ok": ok, "detail": detail, "fix": "" if ok else fix, "blocking": blocking}


async def live_readiness(rt: Runtime, probe: RobinhoodProbe) -> dict:
    s, cfg = rt.settings, rt.cfg
    rh = await probe.snapshot(fresh=True)
    demo = s.ignore_clock or rt.engine.data.name == "synthetic"
    models = agent_models(rt)
    unresolved = [m for m in models if not m["resolved"]]
    checks = [
        _check("not_demo", "Not the demo", not demo, "running the synthetic demo market" if demo else "real market data",
               "restart without --demo: ./trader restart --data robinhood --llm <model>"),
        _check("models", "Every agent has a real model", not unresolved and rt.llm_label != "offline-heuristic",
               "offline heuristic stand-in" if rt.llm_label == "offline-heuristic" else
               (f"no provider for {', '.join(m['agent'] for m in unresolved)}" if unresolved else f"{len(models)} agents resolve ({rt.llm_label})"),
               "start with --llm local/<model>, or set AIT_OPENROUTER_API_KEY"),
        _check("login", "Logged in to Robinhood", rh["logged_in"], "tokens present" if rh["logged_in"] else "no Robinhood session",
               "./trader robinhood login"),
        _check("verified", "Adapter verified", bool(rh["verified_at"]), f"verified {str(rh['verified_at'])[:16].replace('T', ' ')} UTC" if rh["verified_at"] else "never verified",
               "./trader robinhood verify"),
        _check("schemas", "Robinhood tools unchanged since verify", rh["schemas_current"],
               {True: "schemas match", False: "Robinhood changed its tools", None: "not checked (needs login + verify)"}[rh["schemas_current"]],
               "./trader robinhood verify (and read what changed)"),
        _check("account", "One agentic account", rh["account"] is not None,
               f"...{rh['account']['last4']} {rh['account']['type']}, {rh['account']['option_level']}" if rh["account"] else (rh["error"] or "unknown"),
               "enable Agentic trading for exactly one account in the Robinhood app"),
    ]
    bal = rh["balance"] or {}
    checks.append(_check("funded", "Agentic account funded", bal.get("equity", 0) > 0,
                         f"equity ${bal.get('equity', 0):,.2f}, buying power ${bal.get('buying_power', 0):,.2f}" if rh["balance"] else "unknown",
                         "transfer a small amount into the Agentic account in the Robinhood app"))
    async with rt.db.session() as sess:
        live_open = (await sess.execute(select(func.count()).select_from(Position).where(
            Position.mode == "live", Position.status != "closed"))).scalar_one()
        paper_closed = (await sess.execute(select(func.count()).select_from(Position).where(
            Position.mode == "paper", Position.status == "closed"))).scalar_one()
    held = rh["positions"]
    checks.append(_check("positions_match", "Broker positions match the live ledger",
                         None if held is None else len(held) == live_open,
                         "unknown" if held is None else f"Robinhood holds {len(held)}, live ledger has {live_open} open",
                         "close anything in the Agentic account the engine did not open (reconciliation would HALT)"))
    checks.append(_check("kill", "Kill switch armed", rt.kill.state == "armed", f"kill switch {rt.kill.state}",
                         "re-arm it on the dashboard after reading why it tripped"))
    checks.append(_check("track_record", "Paper track record", paper_closed >= 20,
                         f"{paper_closed} closed paper trades on real data", "run paper on real data until the traces convince you",
                         blocking=False))
    ready = all(c["ok"] for c in checks if c["blocking"])
    return {"mode": s.mode, "mode_source": s.mode_source, "ready": ready, "checks": checks,
            "options": "enabled" if (rh["account"] or {}).get("options_ok") and cfg.options.enabled else "shares only",
            "confirm_phrase_hint": "type the phrase shown in the confirmation box"}
