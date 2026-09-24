"""Synthetic market: regime-switching random walks. For demos, tests and
working on the system outside market hours. Never used in live mode."""

from __future__ import annotations

import random
import time
from datetime import date, timedelta

from app.core.pricing import bs_delta, bs_price
from app.core.types import Bar, Instrument, OptionContract, Quote, Right, utcnow


class _Sym:
    def __init__(self, rng: random.Random, symbol: str):
        self.price = rng.uniform(20, 600)
        self.vol = rng.uniform(0.0008, 0.0030)  # per-bar sigma
        self.iv = min(0.9, self.vol * 180)
        self.drift = 0.0
        self.bars: list[Bar] = []
        self.symbol = symbol


class SyntheticData:
    name = "synthetic"

    def __init__(self, symbols: list[str], seed: int = 7, seconds_per_bar: float = 6.0):
        self._rng = random.Random(seed)
        self._syms = {s: _Sym(self._rng, s) for s in symbols}
        self._seconds_per_bar = seconds_per_bar
        self._last_step = time.monotonic()
        self._bar_ts = utcnow() - timedelta(minutes=5 * 120)
        for _ in range(120):
            self._step_bar()

    def anchor(self, symbol: str, price: float) -> None:
        """Rescale a symbol's history so it continues from `price`. Used at restart so an
        open position is not marked against an unrelated new price series."""
        s = self._syms.get(symbol)
        if s is None or price <= 0:
            return
        k = price / s.price
        s.price = price
        s.bars = [b.model_copy(update={"open": b.open * k, "high": b.high * k, "low": b.low * k, "close": b.close * k}) for b in s.bars]

    def _step_bar(self) -> None:
        self._bar_ts += timedelta(minutes=5)
        for s in self._syms.values():
            if self._rng.random() < 0.04:  # regime change: trend up, trend down or chop
                s.drift = self._rng.choice([-1.2, -0.6, 0, 0, 0.6, 1.2]) * s.vol
            o = s.price
            path = [o]
            for _ in range(5):
                path.append(path[-1] * (1 + self._rng.gauss(s.drift / 5, s.vol / 2.2)))
            s.price = path[-1]
            vol = abs(self._rng.gauss(1.0, 0.35)) * 1e5 * (1 + 40 * abs(s.price / o - 1) / s.vol / 10)
            s.bars.append(Bar(ts=self._bar_ts, open=o, high=max(path), low=min(path), close=s.price, volume=vol))
            del s.bars[:-400]

    def _advance(self) -> None:
        now = time.monotonic()
        while now - self._last_step >= self._seconds_per_bar:
            self._step_bar()
            self._last_step += self._seconds_per_bar

    def _equity_quote(self, symbol: str) -> Quote:
        s = self._syms[symbol]
        px = s.price * (1 + self._rng.gauss(0, s.vol / 8))  # intra-bar jitter
        half = px * 0.0002
        return Quote(instrument=Instrument(symbol=symbol), bid=round(px - half, 2),
                     ask=round(px + half, 2), last=round(px, 2))

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self._advance()
        return {s: self._equity_quote(s) for s in symbols if s in self._syms}

    def _option(self, inst: Instrument, spot: float, iv: float) -> OptionContract:
        t = max((inst.expiry - date.today()).days, 0) / 365
        is_call = inst.right == Right.CALL
        mid = max(0.05, bs_price(spot, inst.strike, t, iv, is_call))
        half = max(0.01, mid * 0.012)
        return OptionContract(
            instrument=inst, bid=round(mid - half, 2), ask=round(mid + half, 2), last=round(mid, 2),
            delta=bs_delta(spot, inst.strike, t, iv, is_call), iv=iv,
            open_interest=1500, volume=400,
        )

    async def get_quote(self, instrument: Instrument) -> Quote:
        self._advance()
        if not instrument.is_option:
            return self._equity_quote(instrument.symbol)
        s = self._syms[instrument.symbol]
        c = self._option(instrument, self._equity_quote(instrument.symbol).mid, s.iv)
        return Quote(instrument=instrument, bid=c.bid, ask=c.ask, last=c.last)

    async def get_bars(self, symbols: list[str], interval: str, lookback: int) -> dict[str, list[Bar]]:
        self._advance()
        return {s: self._syms[s].bars[-lookback:] for s in symbols if s in self._syms}

    async def get_option_chain(self, symbol: str, min_dte: int, max_dte: int) -> list[OptionContract]:
        self._advance()
        s = self._syms[symbol]
        spot = s.price
        step = 1 if spot < 50 else 5 if spot < 300 else 10
        atm = round(spot / step) * step
        out: list[OptionContract] = []
        for dte in (7, 14, 28):
            if not min_dte <= dte <= max_dte:
                continue
            expiry = date.today() + timedelta(days=dte)
            for k in range(-4, 5):
                for right in (Right.CALL, Right.PUT):
                    inst = Instrument(symbol=symbol, right=right, strike=float(atm + k * step), expiry=expiry)
                    out.append(self._option(inst, spot, s.iv))
        return out
