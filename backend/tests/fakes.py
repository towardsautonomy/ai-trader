"""Test doubles: a market whose prices the test controls, a scripted LLM, a settable clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from app.agents.llm import LLMResult, log_call
from app.core.clock import SessionState
from app.core.pricing import bs_delta, bs_price
from app.core.types import Bar, Instrument, OptionContract, Quote, Right, utcnow

IV = 0.40


def frozen_cfg():
    from pathlib import Path
    from app.config import load_config
    return load_config(Path(__file__).parent / "fixtures" / "config.yaml")

EXPIRY = date.today() + timedelta(days=14)


class ScriptedData:
    name = "scripted"

    def __init__(self, prices: dict[str, float]):
        self.prices = dict(prices)
        self.fail = False          # raise on every call
        self.quote_age = 0.0       # seconds; simulate a stale feed
        self.option_spread = 0.01  # half-spread as a fraction of mid

    def set(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    def _check(self):
        if self.fail:
            raise ConnectionError("scripted data failure")

    def _quote(self, symbol: str) -> Quote:
        px = self.prices[symbol]
        return Quote(instrument=Instrument(symbol=symbol), bid=round(px * 0.9999, 4), ask=round(px * 1.0001, 4),
                     last=px, ts=utcnow() - timedelta(seconds=self.quote_age))

    async def get_quotes(self, symbols):
        self._check()
        return {s: self._quote(s) for s in symbols if s in self.prices}

    def _contract(self, inst: Instrument) -> OptionContract:
        spot = self.prices[inst.symbol]
        t = max((inst.expiry - date.today()).days, 0) / 365
        call = inst.right == Right.CALL
        mid = max(0.05, bs_price(spot, inst.strike, t, IV, call))
        return OptionContract(instrument=inst, bid=round(mid * (1 - self.option_spread), 2), ask=round(mid * (1 + self.option_spread), 2),
                              last=round(mid, 2), delta=bs_delta(spot, inst.strike, t, IV, call), iv=IV, open_interest=5000, volume=900)

    async def get_quote(self, instrument: Instrument) -> Quote:
        self._check()
        if not instrument.is_option:
            return self._quote(instrument.symbol)
        c = self._contract(instrument)
        return Quote(instrument=instrument, bid=c.bid, ask=c.ask, last=c.last, ts=utcnow() - timedelta(seconds=self.quote_age))

    async def get_bars(self, symbols, interval, lookback):
        self._check()
        out = {}
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        for s in symbols:
            if s not in self.prices:
                continue
            end = self.prices[s]
            bars = []
            for i in range(lookback):
                c = end * (1 - 0.0004 * (lookback - 1 - i))  # gentle, deterministic uptrend into the current price
                bars.append(Bar(ts=now - timedelta(minutes=5 * (lookback - i)), open=c * 0.9998, high=c * 1.0006,
                                low=c * 0.9994, close=c, volume=100_000 + 500 * i))
            out[s] = bars
        return out

    async def get_option_chain(self, symbol, min_dte, max_dte):
        self._check()
        spot = self.prices[symbol]
        step = 1 if spot < 50 else 5
        atm = round(spot / step) * step
        return [self._contract(Instrument(symbol=symbol, right=r, strike=float(atm + k * step), expiry=EXPIRY))
                for k in range(-3, 4) for r in (Right.CALL, Right.PUT)]


class FakeLLM:
    """responses[agent] is a dict, an Exception, a callable(payload)->dict, or a list of those consumed in order."""

    def __init__(self, db, responses: dict):
        self.db, self.responses, self.calls = db, responses, []

    async def complete_json(self, agent, model, system, payload, *, decision_id=None, position_id=None):
        self.calls.append((agent, payload))
        r = self.responses.get(agent)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if callable(r):
            r = r(payload)
        if isinstance(r, Exception) or r is None:
            res = LLMResult(data=None, model=model, error=str(r) if r else f"no scripted response for {agent}")
        else:
            res = LLMResult(data=r, model=model, raw=str(r), cost_usd=0.001)
        await log_call(self.db, agent, res, str(payload), decision_id, position_id)
        return res

    def agents_called(self) -> list[str]:
        return [a for a, _ in self.calls]


OPEN = SessionState(True, True, False, "open", None, None, None, 3 * 3600.0)


class FakeClock:
    def __init__(self):
        self.current = OPEN

    def state(self, now=None):
        return self.current

    def set(self, **kw):
        self.current = replace(self.current, **kw)

    def close_market(self):
        self.current = SessionState(False, False, False, "closed", None, None, utcnow() + timedelta(hours=10), None)

    def session_date(self, now=None):
        return "2026-09-21"

    def trading_day(self, now=None):
        return "2026-09-21", utcnow() - timedelta(hours=6)


def opinion(stance="bullish", confidence=0.7, thesis="scripted"):
    return {"stance": stance, "confidence": confidence, "thesis": thesis, "evidence": ["e1"], "invalidation": "inv"}


def scout_pick(*symbols):
    return {"picks": [{"symbol": s, "reason": f"scripted pick {s}"} for s in symbols], "market_note": "scripted"}


REGIME = {"label": "trend_up", "bias": "bullish", "confidence": 0.7, "summary": "scripted", "what_works": "scripted"}


def pm_enter(price: float, *, vehicle="shares", direction="long", contract_index=None, stop_pct=1.0, target_pct=2.0,
             size=1.0, hold=120, confidence=0.75):
    sign = 1 if direction == "long" else -1
    return {"action": "enter", "direction": direction, "vehicle": vehicle, "contract_index": contract_index,
            "stop_price": round(price * (1 - sign * stop_pct / 100), 2), "target_price": round(price * (1 + sign * target_pct / 100), 2),
            "size_fraction": size, "max_hold_minutes": hold, "confidence": confidence, "summary": "scripted plan",
            "key_risks": ["scripted"], "invalidation": "scripted invalidation"}


HOLD = {"action": "hold", "new_stop": None, "new_target": None, "confidence": 0.6, "reasoning": "scripted hold"}
LESSON = {"tags": ["scripted"], "situation": "s", "what_happened": "w", "lesson": "scripted lesson"}
