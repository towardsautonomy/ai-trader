"""Feature computation over the whole universe, every cycle, in milliseconds.

This module measures; it does not decide. The scout agent reads the feature
table and chooses what deserves deliberation. `hint_score` is a rough heuristic
used to order the table and to stand in for the scout when no LLM is configured
(offline/demo mode); it is never a gate."""

from __future__ import annotations

import numpy as np

from app.config import UniverseCfg
from app.core.types import Bar, Candidate, Quote

MIN_BARS = 30


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    alpha = 2 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, n: int = 14) -> float:
    d = np.diff(close[-(n + 1):])
    up, down = d[d > 0].sum(), -d[d < 0].sum()
    if down == 0:
        return 100.0 if up > 0 else 50.0
    return float(100 - 100 / (1 + up / down))


def atr(bars: list[Bar], n: int = 14) -> float:
    h = np.array([b.high for b in bars[-(n + 1):]])
    l = np.array([b.low for b in bars[-(n + 1):]])
    c = np.array([b.close for b in bars[-(n + 1):]])
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    return float(tr.mean())


def compute_signals(bars: list[Bar]) -> dict[str, float]:
    c = np.array([b.close for b in bars])
    h = np.array([b.high for b in bars])
    l = np.array([b.low for b in bars])
    v = np.array([b.volume for b in bars])
    px = c[-1]
    ema9, ema21 = _ema(c, 9)[-1], _ema(c, 21)[-1]
    typical = (h + l + c) / 3
    vwap = float((typical * v).sum() / v.sum()) if v.sum() > 0 else float(c.mean())
    sd20 = c[-20:].std()
    a = atr(bars)
    return {
        "price": float(px),
        "rsi14": _rsi(c),
        "roc6_pct": float((px / c[-7] - 1) * 100),
        "roc12_pct": float((px / c[-13] - 1) * 100),
        "ema_trend_pct": float((ema9 / ema21 - 1) * 100),
        "vwap_dev_pct": float((px / vwap - 1) * 100),
        "zscore20": float((px - c[-20:].mean()) / sd20) if sd20 > 0 else 0.0,
        "rvol": float(v[-3:].mean() / v[-23:-3].mean()) if v[-23:-3].mean() > 0 else 1.0,
        "breakout_up_atr": float((px - h[-21:-1].max()) / a) if a > 0 else 0.0,
        "breakout_dn_atr": float((l[-21:-1].min() - px) / a) if a > 0 else 0.0,
        "atr_pct": float(a / px * 100),
    }


def _clip(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def score_setups(s: dict[str, float]) -> list[tuple[str, int, float]]:
    """Return (setup, direction, score in [0,1]) for every setup that fires."""
    out = []
    rvol_boost = _clip((s["rvol"] - 1) / 2)

    if s["breakout_up_atr"] > 0 and s["ema_trend_pct"] > 0:
        out.append(("momentum_breakout", 1, _clip(0.45 + 0.3 * s["breakout_up_atr"] + 0.25 * rvol_boost)))
    if s["breakout_dn_atr"] > 0 and s["ema_trend_pct"] < 0:
        out.append(("momentum_breakdown", -1, _clip(0.45 + 0.3 * s["breakout_dn_atr"] + 0.25 * rvol_boost)))

    trend = s["ema_trend_pct"] / max(s["atr_pct"], 1e-9)
    if trend > 0.3 and 40 <= s["rsi14"] <= 58 and abs(s["vwap_dev_pct"]) < s["atr_pct"]:
        out.append(("trend_pullback", 1, _clip(0.35 + 0.35 * trend + 0.1 * rvol_boost)))
    if trend < -0.3 and 42 <= s["rsi14"] <= 60 and abs(s["vwap_dev_pct"]) < s["atr_pct"]:
        out.append(("trend_pullback_short", -1, _clip(0.35 + 0.35 * -trend + 0.1 * rvol_boost)))

    if s["zscore20"] < -2 and s["rsi14"] < 30:
        out.append(("oversold_reversion", 1, _clip(0.3 + 0.15 * (-s["zscore20"] - 2) + (30 - s["rsi14"]) / 60)))
    if s["zscore20"] > 2 and s["rsi14"] > 70:
        out.append(("overbought_reversion", -1, _clip(0.3 + 0.15 * (s["zscore20"] - 2) + (s["rsi14"] - 70) / 60)))
    return out


def recent_path(bars: list[Bar]) -> dict:
    """The price path agents read like a chart."""
    v = np.array([b.volume for b in bars])
    base = v[-26:-6].mean() if len(v) >= 26 and v[-26:-6].mean() > 0 else (v.mean() or 1.0)
    session = [b for b in bars if b.ts.date() == bars[-1].ts.date()] or bars
    return {
        "closes": [round(b.close, 2) for b in bars[-24:]],
        "rel_volume": [round(float(x / base), 2) for x in v[-6:]],
        "day_open": round(session[0].open, 2),
        "day_high": round(max(b.high for b in session), 2),
        "day_low": round(min(b.low for b in session), 2),
    }


def build_table(bars: dict[str, list[Bar]], quotes: dict[str, Quote], cfg: UniverseCfg) -> list[Candidate]:
    """One row per tradable symbol, ordered by hint_score (strongest first)."""
    rows = []
    for symbol, series in bars.items():
        q = quotes.get(symbol)
        # Tradability filters only: enough history, not a penny stock, a spread we can cross.
        if q is None or len(series) < MIN_BARS or q.mid < cfg.min_price or q.spread_pct > cfg.max_spread_pct:
            continue
        signals = compute_signals(series)
        setups = score_setups(signals)
        best = max(setups, key=lambda x: x[2], default=None)
        rows.append(Candidate(
            symbol=symbol, quote=q, atr=atr(series), recent=recent_path(series),
            signals={k: round(v, 4) for k, v in signals.items()},
            hint_score=round(best[2], 3) if best else 0.0,
            hint_setups=[name for name, _, _ in setups],
        ))
    return sorted(rows, key=lambda c: c.hint_score, reverse=True)
