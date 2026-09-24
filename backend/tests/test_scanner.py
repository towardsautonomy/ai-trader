from datetime import datetime, timedelta, timezone

import pytest

from app.core.types import Bar, Instrument, Quote
from app.scanner.scanner import atr, build_table, compute_signals, score_setups
from tests.fakes import frozen_cfg

CFG = frozen_cfg()
T0 = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)


def series(closes, vol=None, spread=0.001):
    vol = vol or [100_000] * len(closes)
    return [Bar(ts=T0 + timedelta(minutes=5 * i), open=c, high=c * (1 + spread), low=c * (1 - spread), close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, vol))]


def quote(px, spread_pct=0.02, sym="AAA"):
    h = px * spread_pct / 200
    return Quote(instrument=Instrument(symbol=sym), bid=px - h, ask=px + h, last=px)


def test_flat_series_is_neutral():
    s = compute_signals(series([100.0] * 60))
    assert s["roc12_pct"] == 0 and s["ema_trend_pct"] == pytest.approx(0) and s["zscore20"] == 0
    assert s["rvol"] == pytest.approx(1.0) and s["rsi14"] == 50.0
    assert not score_setups(s)


def test_uptrend_breakout_is_measured_and_hinted():
    closes = [100.0] * 40 + [100.0 + 0.4 * i for i in range(1, 11)]
    vols = [100_000] * 47 + [300_000] * 3
    s = compute_signals(series(closes, vols))
    assert s["ema_trend_pct"] > 0 and s["breakout_up_atr"] > 0 and s["rvol"] > 2 and s["rsi14"] > 70
    assert "momentum_breakout" in [name for name, _, _ in score_setups(s)]


def test_capitulation_is_measured_and_hinted():
    closes = [100.0 + 0.02 * (i % 3) for i in range(50)] + [99.0, 98.0, 97.0]
    s = compute_signals(series(closes))
    assert s["zscore20"] < -2 and s["rsi14"] < 30
    assert ("oversold_reversion", 1) in [(n, d) for n, d, _ in score_setups(s)]


def test_atr_matches_hand_calculation():
    bars = [Bar(ts=T0, open=10, high=11, low=9, close=10, volume=1)] * 20
    assert atr(bars) == pytest.approx(2.0)


def test_table_applies_tradability_filters_only():
    good = series([100.0 + 0.1 * i for i in range(60)])
    bars = {"AAA": good, "THIN": good, "PENNY": series([2.0] * 60), "NEW": good[:10], "NOQUOTE": good}
    quotes = {"AAA": quote(105.9), "THIN": quote(105.9, spread_pct=0.5, sym="THIN"), "PENNY": quote(2.0, sym="PENNY"),
              "NEW": quote(100.0, sym="NEW")}
    rows = build_table(bars, quotes, CFG.universe)
    assert [r.symbol for r in rows] == ["AAA"]
    assert len(rows[0].recent["closes"]) == 24 and rows[0].recent["day_high"] >= rows[0].recent["day_low"]


def test_table_keeps_symbols_with_no_setup():
    """The scout, not a threshold, decides what is interesting: quiet symbols stay in the table."""
    rows = build_table({"AAA": series([100.0] * 60)}, {"AAA": quote(100.0)}, CFG.universe)
    assert len(rows) == 1 and rows[0].hint_score == 0 and rows[0].hint_setups == []
