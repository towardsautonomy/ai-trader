"""The fast exit check and the tighten-only rule for the position-manager agent."""

from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

from tests.fakes import frozen_cfg
from app.core.types import OrderReason, PositionAction
from app.engine.exits import PosView, apply_review, check_exit

CFG = frozen_cfg()
NOW = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)


def share_pos(**kw) -> PosView:
    base = dict(is_option=False, direction=1, entry_price=100.0, stop_price=99.0, target_price=102.0, und_stop=0.0,
                und_target=0.0, entry_ts=NOW - timedelta(minutes=10), max_hold_minutes=120)
    return PosView(**{**base, **kw})


def call_pos(**kw) -> PosView:
    base = dict(is_option=True, direction=1, entry_price=3.0, stop_price=1.95, target_price=6.0, und_stop=99.0,
                und_target=103.0, entry_ts=NOW - timedelta(minutes=10), max_hold_minutes=120, expiry=date(2026, 10, 5))
    return PosView(**{**base, **kw})


def put_pos(**kw) -> PosView:
    return call_pos(direction=-1, und_stop=101.0, und_target=97.0, **kw)


def reason(pos, price, und=None, **kw):
    hit = check_exit(pos, price, und, now=NOW, cfg=CFG, **kw)
    return hit[0] if hit else None


def test_nothing_to_do_inside_the_levels():
    assert reason(share_pos(), 100.5) is None
    assert reason(call_pos(), 3.2, 100.5) is None


@pytest.mark.parametrize("pos,price,und,expected", [
    (share_pos(), 99.0, None, OrderReason.STOP_LOSS),
    (share_pos(), 98.0, None, OrderReason.STOP_LOSS),              # gapped through
    (share_pos(), 102.0, None, OrderReason.TAKE_PROFIT),
    (share_pos(target_price=120.0), 108.0, None, OrderReason.PROFIT_CEILING),   # +8% hard ceiling beats the agent's target
    (call_pos(), 1.95, 100.0, OrderReason.STOP_LOSS),              # premium -35%
    (call_pos(), 6.0, 102.0, OrderReason.PROFIT_CEILING),          # premium +100%
    (call_pos(), 2.8, 98.9, OrderReason.THESIS_STOP),              # underlying broke the thesis
    (call_pos(), 3.9, 103.1, OrderReason.THESIS_TARGET),
    (put_pos(), 2.8, 101.2, OrderReason.THESIS_STOP),              # mirrored for a short thesis
    (put_pos(), 3.9, 96.9, OrderReason.THESIS_TARGET),
    (put_pos(), 3.1, 99.0, None),
])
def test_each_exit_trigger(pos, price, und, expected):
    assert reason(pos, price, und) == expected


def test_time_stop_and_dte_exit():
    assert reason(share_pos(entry_ts=NOW - timedelta(minutes=121)), 100.5) == OrderReason.TIME_STOP
    assert reason(call_pos(expiry=NOW.date() + timedelta(days=2)), 3.1, 100.5) == OrderReason.DTE_EXIT
    assert reason(call_pos(expiry=NOW.date() + timedelta(days=3)), 3.1, 100.5) is None


def test_kill_and_end_of_day_override_everything():
    assert reason(share_pos(), 101.0, kill_flatten=True) == OrderReason.KILL_SWITCH
    assert reason(share_pos(), 101.0, session_flatten=True) == OrderReason.EOD_FLATTEN
    assert reason(share_pos(), 98.0, kill_flatten=True, session_flatten=True) == OrderReason.KILL_SWITCH


def test_missing_underlying_price_never_crashes_the_option_check():
    assert reason(call_pos(), 3.1, None) is None
    assert reason(call_pos(), 1.0, None) == OrderReason.STOP_LOSS


@settings(max_examples=300, deadline=None)
@given(price=st.floats(0.01, 500), entry=st.floats(1, 400), stop_frac=st.floats(0.5, 0.999))
def test_invariant_a_price_at_or_below_the_stop_always_exits(price, entry, stop_frac):
    pos = share_pos(entry_price=entry, stop_price=entry * stop_frac, target_price=entry * 1.05)
    hit = check_exit(pos, price, None, now=NOW, cfg=CFG)
    if price <= pos.stop_price:
        assert hit and hit[0] == OrderReason.STOP_LOSS
    if price >= entry * (1 + CFG.equity_exits.take_profit_ceiling_pct / 100):
        assert hit and hit[0] in (OrderReason.PROFIT_CEILING, OrderReason.TAKE_PROFIT)


# ---------------------------------------------------------------- position-manager adjustments: risk may shrink, never grow
def adjust(stop=None, target=None):
    return PositionAction(action="adjust", new_stop=stop, new_target=target, reasoning="r")


def test_hold_and_exit_change_nothing():
    assert apply_review(share_pos(), PositionAction(action="hold"), 101.0, None, CFG) == ({}, [])
    assert apply_review(share_pos(), PositionAction(action="exit", new_stop=50.0), 101.0, None, CFG) == ({}, [])


def test_stop_can_tighten():
    changes, notes = apply_review(share_pos(), adjust(stop=100.2), 101.0, None, CFG)
    assert changes == {"stop_price": 100.2} and not notes


def test_stop_can_never_loosen():
    changes, notes = apply_review(share_pos(), adjust(stop=97.0), 101.0, None, CFG)
    assert not changes and "loosen" in notes[0]
    changes, _ = apply_review(share_pos(), adjust(stop=99.0), 101.0, None, CFG)  # unchanged is not tighter
    assert not changes


def test_stop_at_or_through_the_market_is_refused():
    changes, notes = apply_review(share_pos(), adjust(stop=101.5), 101.0, None, CFG)
    assert not changes and "current price" in notes[0]


def test_target_moves_but_stays_under_the_ceiling():
    assert apply_review(share_pos(), adjust(target=104.0), 101.0, None, CFG)[0] == {"target_price": 104.0}
    changes, notes = apply_review(share_pos(), adjust(target=140.0), 101.0, None, CFG)
    assert changes == {"target_price": 108.0} and "ceiling" in notes[0]
    changes, notes = apply_review(share_pos(), adjust(target=100.5), 101.0, None, CFG)
    assert not changes and "not above" in notes[0]


def test_option_adjustments_are_on_the_underlying_and_never_touch_the_premium_stop():
    changes, _ = apply_review(call_pos(), adjust(stop=100.0, target=105.0), 3.4, 101.0, CFG)
    assert changes == {"und_stop": 100.0, "und_target": 105.0}
    assert "stop_price" not in changes and "target_price" not in changes
    assert not apply_review(call_pos(), adjust(stop=98.0), 3.4, 101.0, CFG)[0]            # loosen: refused
    assert not apply_review(call_pos(), adjust(stop=101.5), 3.4, 101.0, CFG)[0]           # through the market
    assert apply_review(put_pos(), adjust(stop=100.5), 3.4, 99.0, CFG)[0] == {"und_stop": 100.5}  # short thesis: tighter = lower
    assert not apply_review(put_pos(), adjust(stop=102.0), 3.4, 99.0, CFG)[0]
    assert apply_review(call_pos(), adjust(stop=100.0), 3.4, None, CFG)[0] == {}           # no underlying price


@settings(max_examples=400, deadline=None)
@given(new_stop=st.floats(0.01, 300), new_target=st.floats(0.01, 300), price=st.floats(99.01, 107.9))
def test_invariant_no_review_can_increase_risk_on_shares(new_stop, new_target, price):
    pos = share_pos()
    changes, _ = apply_review(pos, adjust(stop=new_stop, target=new_target), price, None, CFG)
    assert changes.get("stop_price", pos.stop_price) >= pos.stop_price
    assert changes.get("stop_price", pos.stop_price) < price
    if "target_price" in changes:
        assert price < changes["target_price"] <= 108.0


@settings(max_examples=400, deadline=None)
@given(new_stop=st.floats(50, 150), und=st.floats(99.5, 102.5), direction=st.sampled_from([1, -1]))
def test_invariant_no_review_can_loosen_a_thesis_stop(new_stop, und, direction):
    pos = call_pos() if direction == 1 else put_pos()
    if (und - pos.und_stop) * direction <= 0:
        return
    changes, _ = apply_review(pos, adjust(stop=new_stop), 3.2, und, CFG)
    final = changes.get("und_stop", pos.und_stop)
    assert (final - pos.und_stop) * direction >= 0      # only ever moves toward price
    assert (und - final) * direction > 0                # and never through it
