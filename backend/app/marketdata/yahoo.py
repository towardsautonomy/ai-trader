"""yfinance-backed data for paper trading without a Robinhood login.

Unofficial and possibly delayed: fine for exercising the system on real price
action, not acceptable for live trading (the factory refuses that combination).
Quotes are the last 1-minute close with an assumed spread, since Yahoo has no
reliable bulk bid/ask.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone

import yfinance as yf

from app.broker.base import BrokerError
from app.core.pricing import bs_delta
from app.core.types import Bar, Instrument, OptionContract, Quote, Right, utcnow

_ASSUMED_HALF_SPREAD = 0.0002


class YahooData:
    name = "yfinance"

    def __init__(self, quote_ttl: float = 4.0, bars_ttl: float = 45.0):
        self._quote_ttl, self._bars_ttl = quote_ttl, bars_ttl
        self._cache: dict[tuple, tuple[float, object]] = {}

    def _cached(self, key: tuple, ttl: float):
        hit = self._cache.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < ttl else None

    def _store(self, key: tuple, value):
        self._cache[key] = (time.monotonic(), value)
        return value

    @staticmethod
    def _download(symbols: list[str], interval: str, period: str):
        return yf.download(symbols, period=period, interval=interval, group_by="ticker",
                           progress=False, threads=True, auto_adjust=False)

    async def _frames(self, symbols: list[str], interval: str, period: str, ttl: float) -> dict:
        key = (tuple(sorted(symbols)), interval, period)
        if (hit := self._cached(key, ttl)) is not None:
            return hit
        df = await asyncio.to_thread(self._download, symbols, interval, period)
        out = {}
        for s in symbols:
            try:
                sub = df[s].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(sub):
                out[s] = sub
        return self._store(key, out)

    async def get_bars(self, symbols: list[str], interval: str, lookback: int) -> dict[str, list[Bar]]:
        frames = await self._frames(symbols, interval, "5d", self._bars_ttl)
        return {
            s: [
                Bar(ts=ts.to_pydatetime(), open=r.Open, high=r.High, low=r.Low, close=r.Close, volume=r.Volume)
                for ts, r in f.tail(lookback).iterrows()
            ]
            for s, f in frames.items()
        }

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        frames = await self._frames(symbols, "1m", "1d", self._quote_ttl)
        out = {}
        for s, f in frames.items():
            px = float(f["Close"].iloc[-1])
            ts = f.index[-1].to_pydatetime().astimezone(timezone.utc)
            out[s] = Quote(instrument=Instrument(symbol=s), bid=round(px * (1 - _ASSUMED_HALF_SPREAD), 2),
                           ask=round(px * (1 + _ASSUMED_HALF_SPREAD), 2), last=px, ts=ts)
        return out

    async def get_quote(self, instrument: Instrument) -> Quote:
        if not instrument.is_option:
            q = (await self.get_quotes([instrument.symbol])).get(instrument.symbol)
            if q is None:
                raise BrokerError(f"no quote for {instrument.symbol}")
            return q
        for c in await self._expiry_chain(instrument.symbol, instrument.expiry):
            if c.instrument.key == instrument.key:
                return Quote(instrument=instrument, bid=c.bid, ask=c.ask, last=c.last)
        raise BrokerError(f"no quote for {instrument.key}")

    async def _expiry_chain(self, symbol: str, expiry: date) -> list[OptionContract]:
        key = ("chain", symbol, expiry)
        if (hit := self._cached(key, 10.0)) is not None:
            return hit
        spot = (await self.get_quote(Instrument(symbol=symbol))).mid
        chain = await asyncio.to_thread(lambda: yf.Ticker(symbol).option_chain(expiry.isoformat()))
        t = max((expiry - date.today()).days, 0) / 365
        out = []
        for right, df in ((Right.CALL, chain.calls), (Right.PUT, chain.puts)):
            for r in df.itertuples():
                iv = float(r.impliedVolatility or 0)
                out.append(OptionContract(
                    instrument=Instrument(symbol=symbol, right=right, strike=float(r.strike), expiry=expiry),
                    bid=float(r.bid or 0), ask=float(r.ask or 0), last=float(r.lastPrice or 0),
                    delta=bs_delta(spot, float(r.strike), t, iv, right == Right.CALL) if iv > 0 else None,
                    iv=iv or None,
                    open_interest=int(r.openInterest) if r.openInterest == r.openInterest else 0,
                    volume=int(r.volume) if r.volume == r.volume else 0,
                ))
        return self._store(key, out)

    async def get_option_chain(self, symbol: str, min_dte: int, max_dte: int) -> list[OptionContract]:
        expiries = await asyncio.to_thread(lambda: yf.Ticker(symbol).options)
        today = date.today()
        out: list[OptionContract] = []
        for e in expiries:
            d = datetime.strptime(e, "%Y-%m-%d").date()
            if min_dte <= (d - today).days <= max_dte:
                out.extend(await self._expiry_chain(symbol, d))
        return out


def quote_is_fresh(q: Quote, max_age: float) -> bool:
    return (utcnow() - q.ts).total_seconds() <= max_age
