"""config.yaml is meant to be tuned. These checks only catch settings that contradict
each other or remove a safety property; they do not pin the numbers."""

from app.config import load_config

CFG = load_config()


def test_loads_and_has_a_universe():
    assert len(CFG.universe.symbols) >= 5 and set(CFG.universe.regime_symbols)


def test_loss_limits_are_ordered():
    r = CFG.risk
    assert 0 < r.max_risk_per_trade_pct < r.max_daily_loss_pct < r.max_drawdown_pct <= 20
    assert r.max_option_premium_pct <= r.max_daily_loss_pct, "one option going to zero must not blow the daily limit"
    assert r.max_total_exposure_pct <= 100, "no leverage"


def test_the_risk_budget_is_reachable():
    """With the widest allowed stop, the notional cap must not make the risk budget unreachable."""
    assert CFG.risk.max_position_notional_pct * CFG.equity_exits.max_stop_pct / 100 >= CFG.risk.max_risk_per_trade_pct


def test_session_windows_are_coherent():
    s = CFG.session
    assert s.no_entry_last_minutes >= s.flatten_before_close_minutes > 0


def test_options_are_defined_risk_only():
    assert set(CFG.options.allowed_strategies) <= {"long_call", "long_put"}
    assert 0 < CFG.options.stop_loss_pct < 100 and CFG.options.min_dte > CFG.options.exit_at_dte


def test_fast_loop_is_faster_than_the_slow_loops():
    e = CFG.engine
    assert e.exit_poll_seconds <= 10 and e.exit_poll_seconds < e.cycle_seconds and e.exit_poll_seconds < e.position_review_seconds


def test_paper_fills_are_reachable_by_the_order_offsets():
    """If paper slippage exceeds the limit offsets, nothing ever fills (a bug we hit once)."""
    assert CFG.execution.option_entry_offset_pct >= CFG.paper.option_slippage_pct
    assert CFG.execution.option_exit_offset_pct >= CFG.paper.option_slippage_pct
    assert CFG.execution.entry_limit_offset_bps >= CFG.paper.slippage_bps
