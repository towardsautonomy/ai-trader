"""The risk envelope. Each rule must (a) pass on a clean proposal, (b) fail on its own
violation, and (c) hold as an invariant under random inputs."""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings, strategies as st

from tests.fakes import frozen_cfg
from app.core.types import Instrument, OrderReason, OrderRequest, Right, Side, TradeProposal, Vehicle
from app.risk.engine import OpenPos, PortfolioState, RiskEngine, validate_exit_order

CFG = frozen_cfg()
ENGINE = RiskEngine(CFG)
OPT = Instrument(symbol="AAA", right=Right.CALL, strike=100.0, expiry=date.today() + timedelta(days=14))


def shares(**kw) -> TradeProposal:
    base = dict(symbol="AAA", instrument=Instrument(symbol="AAA"), vehicle=Vehicle.SHARES, side=Side.BUY, qty=50,
                limit_price=100.0, stop_price=99.0, target_price=102.0, risk_usd=50.0, notional_usd=5000.0,
                quote_age_seconds=1.0, spread_pct=0.02, max_hold_minutes=120)
    return TradeProposal(**{**base, **kw})


def option(**kw) -> TradeProposal:
    base = dict(symbol="AAA", instrument=OPT, vehicle=Vehicle.LONG_CALL, side=Side.BUY, qty=2, limit_price=3.0,
                stop_price=1.95, target_price=6.0, und_stop=99.0, und_target=103.0, risk_usd=600.0, notional_usd=600.0,
                quote_age_seconds=1.0, spread_pct=2.0, max_hold_minutes=120)
    return TradeProposal(**{**base, **kw})


def state(**kw) -> PortfolioState:
    base = dict(equity=100_000.0, cash=100_000.0, day_start_equity=100_000.0, equity_high_water=100_000.0)
    return PortfolioState(**{**base, **kw})


def run(p, s=None, *, kill=False, window=True):
    return ENGINE.evaluate(p, s or state(), kill_blocked=kill, entries_allowed=window)


def failed(v) -> set[str]:
    return {c.rule for c in v.failures}


def test_clean_share_trade_is_approved_and_every_rule_is_recorded():
    v = run(shares())
    assert v.approved and not v.failures
    assert {"kill_switch", "trading_window", "long_only", "sane_order", "risk_per_trade", "max_stop_distance",
            "position_notional_cap", "buying_power", "total_exposure", "max_open_positions", "positions_per_symbol",
            "daily_loss_limit", "drawdown_limit", "orders_per_minute", "trades_per_day", "quote_fresh", "spread",
            "llm_budget"} <= {c.rule for c in v.checks}


def test_clean_option_trade_is_approved():
    v = run(option())
    assert v.approved, v.failures
    assert {"options_enabled", "defined_risk_strategy", "option_premium_cap"} <= {c.rule for c in v.checks}


@pytest.mark.parametrize("proposal,st_kw,flags,rule", [
    (shares(), {}, {"kill": True}, "kill_switch"),
    (shares(), {}, {"window": False}, "trading_window"),
    (shares(side=Side.SELL), {}, {}, "long_only"),
    (option(side=Side.SELL), {}, {}, "long_only"),
    (shares(qty=0), {}, {}, "sane_order"),
    (shares(stop_price=101.0), {}, {}, "sane_order"),
    (shares(target_price=99.5), {}, {}, "sane_order"),
    (shares(risk_usd=510.0), {}, {}, "risk_per_trade"),
    (shares(stop_price=96.0), {}, {}, "max_stop_distance"),
    (shares(notional_usd=10_100.0), {}, {}, "position_notional_cap"),
    (option(notional_usd=1_050.0, risk_usd=1_050.0), {}, {}, "option_premium_cap"),
    (shares(), {"cash": 4_000.0}, {}, "buying_power"),
    (shares(), {"open_positions": [OpenPos("ZZZ", "ZZZ", 56_000.0)]}, {}, "total_exposure"),
    (shares(), {"open_positions": [OpenPos(f"S{i}", f"S{i}", 100.0) for i in range(8)]}, {}, "max_open_positions"),
    (shares(), {"open_positions": [OpenPos("AAA", "AAA", 100.0)]}, {}, "positions_per_symbol"),
    (shares(), {"equity": 97_900.0}, {}, "daily_loss_limit"),
    (shares(), {"equity_high_water": 107_000.0}, {}, "drawdown_limit"),
    (shares(), {"order_times_last_minute": 12}, {}, "orders_per_minute"),
    (shares(), {"trades_today": 40}, {}, "trades_per_day"),
    (shares(quote_age_seconds=21.0), {}, {}, "quote_fresh"),
    (shares(spread_pct=0.2), {}, {}, "spread"),
    (option(spread_pct=8.5), {}, {}, "spread"),
    (shares(), {"llm_spend_today": 15.0}, {}, "llm_budget"),
])
def test_each_rule_rejects_its_own_violation(proposal, st_kw, flags, rule):
    v = run(proposal, state(**st_kw), **flags)
    assert not v.approved
    assert rule in failed(v)


def test_no_short_circuit_all_failures_are_reported():
    v = run(shares(side=Side.SELL, qty=0, quote_age_seconds=99), kill=True, window=False)
    assert {"kill_switch", "trading_window", "long_only", "sane_order", "quote_fresh"} <= failed(v)


def test_options_disabled_blocks_options_only():
    cfg = frozen_cfg()
    cfg.options.enabled = False
    eng = RiskEngine(cfg)
    assert "options_enabled" in failed(eng.evaluate(option(), state(), kill_blocked=False, entries_allowed=True))
    assert eng.evaluate(shares(), state(), kill_blocked=False, entries_allowed=True).approved


def test_strategy_outside_allowed_list_is_rejected():
    cfg = frozen_cfg()
    cfg.options.allowed_strategies = ["long_put"]
    v = RiskEngine(cfg).evaluate(option(), state(), kill_blocked=False, entries_allowed=True)
    assert "defined_risk_strategy" in failed(v)


def test_day_trade_limit_applies_only_when_configured():
    cfg = frozen_cfg()
    cfg.risk.max_day_trades_5d = 3
    v = RiskEngine(cfg).evaluate(shares(), state(day_trades_5d=3), kill_blocked=False, entries_allowed=True)
    assert "day_trade_limit" in failed(v)
    assert run(shares(), state(day_trades_5d=99)).approved  # default 0 = not enforced


def test_judgment_is_not_gated_in_code():
    """Conviction, consensus and reward:risk belong to the agents. If someone re-adds
    them as hard gates, this fails: that is a design decision, not a bug fix."""
    rules = {c.rule for c in run(shares()).checks}
    assert not rules & {"pm_confidence", "swarm_consensus", "reward_risk", "symbol_cooldown", "loss_streak_pause"}


# ---------------------------------------------------------------- invariants
money = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)


@settings(max_examples=400, deadline=None)
@given(qty=st.integers(-5, 5000), limit=money, stop=money, target=money, risk=money, notional=money,
       equity=st.floats(1_000, 1e6), cash_frac=st.floats(0, 1), day_loss=st.floats(0, 0.05),
       is_opt=st.booleans(), side=st.sampled_from(list(Side)))
def test_invariant_nothing_approved_ever_breaches_the_envelope(qty, limit, stop, target, risk, notional, equity,
                                                              cash_frac, day_loss, is_opt, side):
    make = option if is_opt else shares
    p = make(qty=qty, limit_price=limit, stop_price=stop, target_price=target, risk_usd=risk, notional_usd=notional, side=side)
    s = state(equity=equity * (1 - day_loss), cash=equity * cash_frac, day_start_equity=equity, equity_high_water=equity)
    v = ENGINE.evaluate(p, s, kill_blocked=False, entries_allowed=True)
    if not v.approved:
        return
    r = CFG.risk
    assert p.side == Side.BUY and p.qty > 0
    assert p.stop_price < p.limit_price < p.target_price
    assert p.notional_usd <= s.cash
    assert s.day_pnl_pct > -r.max_daily_loss_pct
    if is_opt:
        assert p.notional_usd <= s.equity * r.max_option_premium_pct / 100 * 1.001
    else:
        assert p.risk_usd <= s.equity * r.max_risk_per_trade_pct / 100 * 1.001
        assert p.notional_usd <= s.equity * r.max_position_notional_pct / 100 * 1.001
        assert (1 - p.stop_price / p.limit_price) * 100 <= CFG.equity_exits.max_stop_pct * 1.001


@given(kill=st.booleans(), window=st.booleans())
def test_invariant_kill_or_closed_window_always_blocks(kill, window):
    v = run(shares(), kill=kill, window=window)
    assert v.approved == (not kill and window)


# ---------------------------------------------------------------- exits may only reduce
def _exit(side=Side.SELL, qty=10, opens=False):
    return OrderRequest(client_order_id="x", instrument=Instrument(symbol="AAA"), side=side, qty=qty,
                        limit_price=99.0, reason=OrderReason.STOP_LOSS, opens_position=opens)


def test_exit_order_may_only_reduce_a_held_position():
    assert validate_exit_order(_exit(), 10).passed
    assert validate_exit_order(_exit(qty=4), 10).passed
    assert not validate_exit_order(_exit(qty=11), 10).passed      # would flip short
    assert not validate_exit_order(_exit(side=Side.BUY), 10).passed
    assert not validate_exit_order(_exit(opens=True), 10).passed
    assert not validate_exit_order(_exit(qty=0), 10).passed
