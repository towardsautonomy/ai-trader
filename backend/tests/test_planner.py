"""Plan -> order. The agent's plan is honoured inside the envelope, clamped (and the
clamp recorded) at its edge, and discarded when it cannot be made safe."""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings, strategies as st

from tests.fakes import frozen_cfg
from app.core.types import Candidate, Instrument, OptionContract, PMDecision, Quote, Right, Side, Vehicle
from app.engine.planner import Unbuildable, build_proposal, build_shortlist
from app.risk.engine import PortfolioState, RiskEngine

CFG = frozen_cfg()
EXP = date.today() + timedelta(days=14)


def cand(price=100.0) -> Candidate:
    q = Quote(instrument=Instrument(symbol="AAA"), bid=price - 0.01, ask=price + 0.01, last=price)
    return Candidate(symbol="AAA", quote=q, signals={}, atr=0.5)


def contract(right=Right.CALL, strike=100.0, bid=2.95, ask=3.05, delta=0.5, oi=1000, vol=500, dte=14) -> OptionContract:
    return OptionContract(instrument=Instrument(symbol="AAA", right=right, strike=strike, expiry=date.today() + timedelta(days=dte)),
                          bid=bid, ask=ask, last=(bid + ask) / 2, delta=delta if right == Right.CALL else -delta, iv=0.4,
                          open_interest=oi, volume=vol)


SHORTLIST = [contract(Right.CALL), contract(Right.PUT)]


def pm(**kw) -> PMDecision:
    base = dict(action="enter", direction="long", vehicle=Vehicle.SHARES, stop_price=99.0, target_price=102.0,
                size_fraction=1.0, max_hold_minutes=120, confidence=0.7, summary="s")
    return PMDecision(**{**base, **kw})


def build(p, c=None, shortlist=SHORTLIST, equity=100_000.0, cash=100_000.0, cfg=CFG):
    return build_proposal(p, c or cand(), shortlist, equity=equity, cash=cash, cfg=cfg)


# ---------------------------------------------------------------- shares
def test_shares_are_sized_from_the_stop_distance():
    p = build(pm(size_fraction=0.2))                              # $100 of risk: small enough that no cap binds
    assert p.side == Side.BUY and p.instrument.key == "AAA"
    assert p.limit_price == pytest.approx(100.06, abs=0.01)      # ask + 5 bps
    assert p.qty == int(100 / (p.limit_price - 99.0))
    assert p.risk_usd <= 100 and not p.clamps


def test_size_fraction_scales_risk():
    a, b = build(pm(size_fraction=0.2)), build(pm(size_fraction=0.1))
    assert b.qty == pytest.approx(a.qty / 2, abs=1)


def test_a_stop_beyond_the_maximum_is_pulled_in_and_recorded():
    p = build(pm(stop_price=90.0))
    assert p.stop_price == pytest.approx(p.limit_price * 0.97, abs=0.01)
    assert any("maximum" in c for c in p.clamps)


def test_a_target_beyond_the_ceiling_is_pulled_in_and_recorded():
    p = build(pm(target_price=150.0))
    assert p.target_price == pytest.approx(p.limit_price * 1.08, abs=0.01)
    assert any("ceiling" in c for c in p.clamps)


def test_a_tight_stop_cannot_create_an_oversized_position():
    p = build(pm(stop_price=99.95))  # tiny stop distance -> huge share count, capped by notional
    assert p.notional_usd <= 10_000 * 1.001
    assert any("cap" in c for c in p.clamps)


def test_position_is_capped_by_cash():
    p = build(pm(stop_price=99.9), cash=2_000.0)
    assert p.notional_usd <= 2_000


def test_hold_time_is_capped():
    p = build(pm(max_hold_minutes=10_000))
    assert p.max_hold_minutes == CFG.equity_exits.max_hold_minutes
    assert build(pm(max_hold_minutes=0)).max_hold_minutes == CFG.equity_exits.max_hold_minutes


@pytest.mark.parametrize("plan,why", [
    (pm(action="skip"), "did not choose"),
    (pm(vehicle=None), "missing vehicle"),
    (pm(direction="sideways"), "missing vehicle or direction"),
    (pm(size_fraction=0.0), "size_fraction"),
    (pm(direction="short"), "cannot express"),                       # no short stock
    (pm(vehicle=Vehicle.LONG_PUT, contract_index=1), "cannot express"),  # put with a long thesis
    (pm(stop_price=101.0), "do not bracket"),
    (pm(target_price=99.5), "do not bracket"),
    (pm(stop_price=0.0), "do not bracket"),
    (pm(vehicle=Vehicle.LONG_CALL, contract_index=None), "not in the shortlist"),
    (pm(vehicle=Vehicle.LONG_CALL, contract_index=7), "not in the shortlist"),
    (pm(vehicle=Vehicle.LONG_CALL, contract_index=1), "is a put"),
])
def test_unsafe_or_incoherent_plans_are_discarded(plan, why):
    with pytest.raises(Unbuildable, match=why):
        build(plan)


def test_too_small_a_budget_is_discarded_not_rounded_up():
    with pytest.raises(Unbuildable, match="too small"):
        build(pm(stop_price=97.5, size_fraction=0.1), equity=1_000.0, cash=1_000.0)


# ---------------------------------------------------------------- options
def test_long_call_risk_is_the_whole_premium_and_is_capped():
    p = build(pm(vehicle=Vehicle.LONG_CALL, contract_index=0))
    assert p.instrument.is_option and p.side == Side.BUY
    assert p.limit_price == 3.10                                   # ask 3.05 + 1.5%, rounded up
    assert p.qty == 3 and p.notional_usd == pytest.approx(930.0)   # floor(1000 / 310): sized on the worst-case fill
    assert p.risk_usd == p.notional_usd                            # max loss = premium, not the stop
    assert p.stop_price == pytest.approx(3.10 * 0.65, abs=0.01)    # hard -35% premium stop
    assert p.target_price == pytest.approx(3.10 * 2.0, abs=0.01)   # hard +100% ceiling
    assert (p.und_stop, p.und_target) == (99.0, 102.0)             # the agent's thesis levels ride along


def test_long_put_expresses_a_short_thesis():
    p = build(pm(direction="short", vehicle=Vehicle.LONG_PUT, contract_index=1, stop_price=101.0, target_price=97.0))
    assert p.instrument.right == Right.PUT and p.side == Side.BUY
    assert (p.und_stop, p.und_target) == (101.0, 97.0)


def test_unaffordable_contract_is_discarded():
    with pytest.raises(Unbuildable, match="does not cover"):
        build(pm(vehicle=Vehicle.LONG_CALL, contract_index=0, size_fraction=0.2))  # $200 budget, $310 contract


def test_one_sided_option_market_is_discarded():
    with pytest.raises(Unbuildable, match="two-sided"):
        build(pm(vehicle=Vehicle.LONG_CALL, contract_index=0), shortlist=[contract(bid=0.0)])


# ---------------------------------------------------------------- shortlist
def test_shortlist_keeps_only_liquid_affordable_in_range_contracts():
    chain = [
        contract(strike=100),                        # good
        contract(strike=101, bid=2.0, ask=2.6),      # spread too wide
        contract(strike=102, oi=10),                 # no open interest
        contract(strike=103, vol=1, oi=300),         # no volume and shallow open interest
        contract(strike=104, dte=2),                 # too close to expiry
        contract(strike=105, dte=90),                # too far
        contract(strike=106, delta=0.05),            # lottery ticket
        contract(strike=107, bid=0.0, ask=0.05),     # no bid
        contract(strike=108, bid=14.9, ask=15.1),    # unaffordable under the cap
        contract(Right.PUT, strike=100),             # good
    ]
    out = build_shortlist(chain, CFG, max_premium_usd=1_000)
    assert sorted(c.instrument.strike for c in out) == [100.0, 100.0]
    assert {c.instrument.right for c in out} == {Right.CALL, Right.PUT}


def test_shortlist_affordability_is_judged_at_the_limit_price_not_the_ask():
    """A contract whose ask fits the cap but whose entry limit (ask + offset) does not must not be offered."""
    edge = contract(strike=100, bid=9.80, ask=9.90)             # $990 at the ask, $1,005 at the limit
    assert build_shortlist([edge], CFG, max_premium_usd=1_000) == []
    assert len(build_shortlist([edge], CFG, max_premium_usd=1_010)) == 1


def test_everything_on_the_shortlist_is_buildable_at_full_size():
    chain = [contract(strike=90 + i, bid=1.0 + i, ask=1.05 + i * 1.01, delta=0.4 + 0.02 * i) for i in range(10)]
    for i, c in enumerate(build_shortlist(chain, CFG, max_premium_usd=1_000)):
        assert build(pm(vehicle=Vehicle.LONG_CALL, contract_index=i), shortlist=build_shortlist(chain, CFG, max_premium_usd=1_000)).qty >= 1


def test_the_discard_reason_explains_the_size_budget():
    with pytest.raises(Unbuildable, match=r"size_fraction 0.2 .* \$200.*one contract at \$310"):
        build(pm(vehicle=Vehicle.LONG_CALL, contract_index=0, size_fraction=0.2))


def test_shortlist_prefers_target_delta_and_respects_size():
    chain = [contract(strike=90 + i, delta=0.35 + 0.03 * i) for i in range(10)]
    out = build_shortlist(chain, CFG)
    assert len(out) == CFG.options.shortlist_size // 2
    assert abs(out[0].delta - 0.5) <= abs(out[-1].delta - 0.5)


# ---------------------------------------------------------------- invariant: planner output always fits the envelope
@settings(max_examples=300, deadline=None)
@given(price=st.floats(5, 900), stop_pct=st.floats(0.01, 30), target_pct=st.floats(0.01, 60), size=st.floats(0.01, 1),
       equity=st.floats(2_000, 2e6), cash_frac=st.floats(0.05, 1), use_option=st.booleans())
def test_invariant_whatever_the_agent_asks_the_built_order_passes_sizing_rules(price, stop_pct, target_pct, size, equity,
                                                                              cash_frac, use_option):
    plan = pm(stop_price=price * (1 - stop_pct / 100), target_price=price * (1 + target_pct / 100), size_fraction=size,
              vehicle=Vehicle.LONG_CALL if use_option else Vehicle.SHARES, contract_index=0 if use_option else None)
    cash = equity * cash_frac
    try:
        p = build_proposal(plan, cand(price), SHORTLIST, equity=equity, cash=cash, cfg=CFG)
    except Unbuildable:
        return
    state = PortfolioState(equity=equity, cash=cash, day_start_equity=equity, equity_high_water=equity)
    verdict = RiskEngine(CFG).evaluate(p, state, kill_blocked=False, entries_allowed=True)
    sizing = {"sane_order", "risk_per_trade", "max_stop_distance", "position_notional_cap", "option_premium_cap", "buying_power", "long_only"}
    assert not [c for c in verdict.failures if c.rule in sizing], verdict.failures


def test_early_session_put_with_deep_open_interest_is_kept_despite_low_volume():
    min_oi = CFG.options.min_open_interest
    chain = [contract(Right.PUT, strike=100, vol=3, oi=5 * min_oi),   # 10:05 am: deep OI, little volume yet
             contract(Right.PUT, strike=101, vol=3, oi=2 * min_oi)]   # thin on both counts
    out = build_shortlist(chain, CFG, max_premium_usd=1_000)
    assert [c.instrument.strike for c in out] == [100.0]
