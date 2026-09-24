"""End-to-end scenarios: the real engine, risk envelope, executor, paper broker and
database, driven by a scripted market and scripted agents. Each test is a story
about money; if one breaks, behaviour that matters has changed."""

from sqlalchemy import select

from app.core.killswitch import FLATTEN, HALTED
from app.db.models import AgentOpinion, Decision, Lesson, LLMCall, Order, PositionReview
from tests.fakes import EXPIRY, HOLD, opinion, pm_enter, scout_pick


async def decisions(rig):
    async with rig.db.session() as s:
        return list((await s.execute(select(Decision).order_by(Decision.ts))).scalars())


async def count(rig, model):
    async with rig.db.session() as s:
        return len(list((await s.execute(select(model))).scalars()))


# ---------------------------------------------------------------- the happy path, fully traced
async def test_entry_to_take_profit_leaves_a_complete_audit_trail(rig):
    await rig.tick(entry=True)
    [pos] = await rig.positions("open")
    assert pos.symbol == "AAA" and pos.stop_price == 99.0 and pos.target_price == 102.0
    assert pos.risk_usd <= 500 and pos.thesis == "scripted plan"

    rig.data.set("AAA", 102.5)
    await rig.tick()
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "take_profit" and pos.realized_pnl > 0

    [d] = await decisions(rig)
    assert d.status == "executed" and d.scout_reason == "scripted pick AAA"
    assert d.candidate["signals"] and d.regime["label"] == "trend_up"
    assert d.pm["action"] == "enter" and d.proposal["qty"] == pos.qty
    assert d.risk["approved"] and len(d.risk["checks"]) >= 15
    assert set(d.context) == {"portfolio", "track_record", "lessons", "envelope", "option_shortlist"}
    async with rig.db.session() as s:
        ops = list((await s.execute(select(AgentOpinion).where(AgentOpinion.decision_id == d.id))).scalars())
        orders = list((await s.execute(select(Order).order_by(Order.ts))).scalars())
        lesson = (await s.execute(select(Lesson))).scalar_one()
    assert [o.agent for o in ops] == ["momentum", "mean_reversion", "volatility", "skeptic"]
    assert [(o.side, o.reason, o.status) for o in orders] == [("buy", "entry", "filled"), ("sell", "take_profit", "filled")]
    assert all(o.decision_id == d.id and o.position_id == pos.id for o in orders)
    assert lesson.position_id == pos.id and lesson.outcome_r > 0
    kinds = await rig.event_kinds()
    for k in ("cycle.scan", "decision.enter", "order.submitted", "position.opened", "exit.triggered", "position.closed", "lesson.written"):
        assert k in kinds
    assert await count(rig, LLMCall) == 8   # regime, scout, 3 specialists, skeptic, PM, reviewer: every call is on record


async def test_stop_loss_caps_the_loss_near_the_planned_risk(rig):
    await rig.tick(entry=True)
    rig.data.set("AAA", 98.9)
    await rig.tick()
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "stop_loss" and pos.realized_pnl < 0
    assert abs(pos.realized_pnl) <= pos.risk_usd * 1.25      # slippage allowed, a blow-out is not
    acct = await rig.broker.get_account()
    assert acct.equity > 100_000 * (1 - 0.0065)                # one loser costs about the 0.5% budget


# ---------------------------------------------------------------- the agents decide
async def test_pm_skip_means_no_order(rig):
    rig.llm.responses["portfolio_manager"] = {"action": "skip", "confidence": 0.7, "summary": "third test of resistance"}
    await rig.tick(entry=True)
    assert not await rig.positions() and await count(rig, Order) == 0
    [d] = await decisions(rig)
    assert d.status == "skipped_by_pm" and "third test of resistance" in d.outcome
    assert await count(rig, AgentOpinion) == 4                 # the reasoning behind a non-trade is kept too


async def test_scout_picking_nothing_costs_no_deliberation(rig):
    rig.llm.responses["scout"] = scout_pick()
    await rig.tick(entry=True)
    assert rig.llm.agents_called() == ["regime", "scout"] and not await decisions(rig)


async def test_bearish_view_trades_a_long_put_and_exits_on_the_thesis_stop(rig):
    def plan(payload):
        idx = next(c["index"] for c in payload["option_shortlist"] if c["type"] == "put")
        return pm_enter(100.0, vehicle="long_put", direction="short", contract_index=idx, stop_pct=1.0, target_pct=3.0)
    rig.llm.responses["portfolio_manager"] = plan
    await rig.tick(entry=True)
    [pos] = await rig.positions("open")
    assert pos.instrument["right"] == "put" and pos.direction == -1 and pos.und_stop == 101.0
    assert pos.risk_usd <= 1_000                               # premium, the true max loss, is capped at 1% of equity
    orders_before = await count(rig, Order)
    rig.data.set("AAA", 101.3)                                 # underlying breaks the thesis before the premium stop
    await rig.tick()
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "thesis_stop" and await count(rig, Order) == orders_before + 1
    async with rig.db.session() as s:
        assert all(o.side == "buy" or o.reason != "entry" for o in (await s.execute(select(Order))).scalars())


async def test_a_short_stock_plan_is_discarded(rig):
    rig.llm.responses["portfolio_manager"] = pm_enter(100.0, vehicle="shares", direction="short")
    await rig.tick(entry=True)
    [d] = await decisions(rig)
    assert d.status == "unbuildable" and "cannot express" in d.outcome and await count(rig, Order) == 0


async def test_a_stale_plan_is_discarded_when_price_has_run_away(rig):
    """The PM planned around 100; by execution the price is past its target. Do not chase."""
    rig.llm.responses["portfolio_manager"] = lambda p: (rig.data.set("AAA", 103.0), pm_enter(100.0))[1]
    await rig.tick(entry=True)
    [d] = await decisions(rig)
    assert d.status == "unbuildable" and "bracket" in d.outcome and not await rig.positions()


async def test_an_overreaching_plan_is_clamped_and_the_clamp_is_visible(rig):
    rig.llm.responses["portfolio_manager"] = pm_enter(100.0, stop_pct=12.0, target_pct=40.0, hold=9999)
    await rig.tick(entry=True)
    [pos] = await rig.positions("open")
    assert pos.stop_price >= 97.0 and pos.target_price <= 108.1 and pos.max_hold_minutes == 240
    [d] = await decisions(rig)
    clamps = " | ".join(d.proposal["clamps"])
    assert all(word in clamps for word in ("maximum", "ceiling", "max hold")) and "plan.clamped" in await rig.event_kinds()


# ---------------------------------------------------------------- the position-manager agent
async def test_agent_can_tighten_a_stop_and_the_fast_loop_enforces_it(rig):
    await rig.tick(entry=True)
    rig.data.set("AAA", 101.5)
    rig.llm.responses["position_manager"] = {"action": "adjust", "new_stop": 101.0, "new_target": None, "confidence": 0.7, "reasoning": "lock in"}
    await rig.tick(review=True)
    [pos] = await rig.positions("open")
    assert pos.stop_price == 101.0 and pos.initial_stop == 99.0
    rig.data.set("AAA", 100.9)
    await rig.tick()
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "stop_loss" and pos.realized_pnl > 0       # stopped out in profit


async def test_agent_cannot_loosen_a_stop(rig):
    await rig.tick(entry=True)
    rig.llm.responses["position_manager"] = {"action": "adjust", "new_stop": 95.0, "new_target": None, "confidence": 0.9, "reasoning": "give it room"}
    await rig.tick(review=True)
    [pos] = await rig.positions("open")
    assert pos.stop_price == 99.0
    async with rig.db.session() as s:
        review = (await s.execute(select(PositionReview))).scalar_one()
    assert review.requested["new_stop"] == 95.0 and not review.applied["changes"] and "loosen" in review.applied["notes"][0]


async def test_agent_exit_closes_with_its_reasoning_on_record(rig):
    await rig.tick(entry=True)
    rig.llm.responses["position_manager"] = {"action": "exit", "confidence": 0.7, "reasoning": "failed breakout on heavy volume"}
    await rig.tick(review=True)
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "agent_exit" and "failed breakout" in pos.exit_detail


async def test_review_failure_leaves_the_position_protected_by_its_stop(rig):
    await rig.tick(entry=True)
    rig.llm.responses["position_manager"] = Exception("model down")
    await rig.tick(review=True)
    [pos] = await rig.positions("open")
    rig.data.set("AAA", 98.5)
    await rig.tick()
    assert (await rig.positions("closed"))[0].exit_reason == "stop_loss"     # no LLM needed to stop out


# ---------------------------------------------------------------- the trading window
async def test_nothing_happens_when_the_market_is_closed(rig):
    rig.clock.close_market()
    await rig.tick(entry=True, review=True, watchdog=True)
    assert rig.llm.calls == [] and not await decisions(rig)


async def test_no_entries_in_the_buffers_but_exits_still_work(rig):
    await rig.tick(entry=True)
    rig.clock.set(entries_allowed=False, phase="closing_buffer")
    calls = len(rig.llm.calls)
    await rig.tick(entry=True)
    assert len(rig.llm.calls) == calls and len(await rig.positions()) == 1
    rig.data.set("AAA", 98.0)
    await rig.tick()
    assert (await rig.positions("closed"))[0].exit_reason == "stop_loss"


async def test_everything_is_flattened_before_the_close(rig):
    await rig.tick(entry=True)
    rig.clock.set(entries_allowed=False, flatten_now=True, phase="flatten")
    await rig.tick()
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "eod_flatten" and not await rig.broker.get_positions()


# ---------------------------------------------------------------- kill switch
async def test_halt_blocks_entries_and_keeps_managing_open_positions(rig):
    await rig.tick(entry=True)
    await rig.kill.trip(HALTED, "test", "unit")
    calls = len(rig.llm.calls)
    await rig.tick(entry=True)
    assert len(rig.llm.calls) == calls and len(await rig.positions("open")) == 1     # no new entry, no LLM spend
    rig.data.set("AAA", 102.5)
    await rig.tick()
    assert (await rig.positions("closed"))[0].exit_reason == "take_profit"


async def test_flatten_liquidates_everything_immediately(rig):
    rig.llm.responses["scout"] = scout_pick("AAA", "BBB")
    rig.llm.responses["portfolio_manager"] = lambda p: pm_enter(p["price"])
    await rig.tick(entry=True)
    assert len(await rig.positions("open")) == 2
    await rig.kill.trip(FLATTEN, "panic", "unit")
    await rig.tick()
    closed = await rig.positions("closed")
    assert len(closed) == 2 and {p.exit_reason for p in closed} == {"kill_switch"}
    assert not await rig.broker.get_positions()
    await rig.tick(entry=True)
    assert len(await rig.positions()) == 2                                           # and stays out


async def test_flatten_while_closed_waits_for_the_open(rig):
    await rig.tick(entry=True)
    rig.clock.close_market()
    await rig.kill.trip(FLATTEN, "overnight panic", "unit")
    await rig.tick()
    assert len(await rig.positions("open")) == 1 and "killswitch.waiting" in await rig.event_kinds()


async def test_kill_engaged_between_decision_and_order_still_blocks_it(rig):
    async def trip_then_plan(payload):
        return pm_enter(100.0)
    rig.llm.responses["portfolio_manager"] = lambda p: pm_enter(100.0)
    orig = rig.engine._plan_and_execute

    async def kill_first(*a, **kw):
        await rig.kill.trip(HALTED, "pressed mid-cycle", "unit")
        return await orig(*a, **kw)
    rig.engine._plan_and_execute = kill_first
    await rig.tick(entry=True)
    [d] = await decisions(rig)
    assert d.status == "rejected_by_risk" and "kill_switch" in d.outcome and await count(rig, Order) == 0


# ---------------------------------------------------------------- automatic trips
async def test_daily_loss_limit_halts_trading(rig):
    await rig.tick(entry=True, watchdog=True)
    rig.broker.cash -= 2_100                                   # simulate a 2.1% day loss
    await rig.tick(watchdog=True)
    assert rig.kill.state == HALTED and "daily loss" in rig.kill.reason


async def test_drawdown_limit_flattens(rig):
    await rig.tick(entry=True, watchdog=True)
    rig.broker.cash -= 6_500
    await rig.tick(watchdog=True)
    assert rig.kill.state == FLATTEN and "drawdown" in rig.kill.reason
    await rig.tick()
    assert not await rig.positions("open")


async def test_repeated_model_failures_halt_trading(rig):
    for a in ("regime", "scout", "momentum", "mean_reversion", "volatility", "skeptic", "portfolio_manager"):
        rig.llm.responses[a] = Exception("provider outage")
    for _ in range(5):
        await rig.tick(entry=True)
    await rig.tick(watchdog=True)
    assert rig.kill.state == HALTED and "model calls" in rig.kill.reason and not await rig.positions()


async def test_market_data_outage_halts_trading(rig):
    await rig.tick(entry=True)
    rig.data.fail = True
    for _ in range(7):
        await rig.engine.exit_cycle()
    await rig.engine.watchdog_cycle()                          # still down: the watchdog must act on its own
    assert rig.kill.state == HALTED and "market-data" in rig.kill.reason


async def test_ledger_broker_mismatch_halts_trading(rig):
    await rig.tick(entry=True)
    rig.broker.positions["ZZZ"] = {"instrument": {"symbol": "ZZZ"}, "qty": 5, "avg_price": 10.0}   # something the ledger never opened
    for _ in range(3):
        await rig.engine.watchdog_cycle()
    assert rig.kill.state == HALTED and "disagree" in rig.kill.reason


async def test_stale_quotes_are_never_traded_on(rig):
    rig.data.quote_age = 120
    await rig.tick(entry=True)
    [d] = await decisions(rig)
    assert d.status == "rejected_by_risk" and "quote_fresh" in d.outcome


async def test_llm_budget_stops_new_entries(rig):
    rig.engine.cfg.risk.max_llm_spend_per_day_usd = 0.005
    await rig.tick(entry=True)                                 # spends ~0.007
    rig.engine.spend._cache = (0.0, 0.0)
    calls = len(rig.llm.calls)
    rig.llm.responses["scout"] = scout_pick("BBB")
    await rig.tick(entry=True)
    assert len(rig.llm.calls) == calls and "engine.entries_paused" in await rig.event_kinds()


# ---------------------------------------------------------------- portfolio limits and learning
async def test_position_slots_are_respected(rig):
    rig.engine.cfg.risk.max_open_positions = 1
    rig.llm.responses["scout"] = scout_pick("AAA", "BBB")
    rig.llm.responses["portfolio_manager"] = lambda p: pm_enter(p["price"])
    await rig.tick(entry=True)
    assert len(await rig.positions("open")) == 1


async def test_no_second_position_in_the_same_symbol(rig):
    await rig.tick(entry=True)
    await rig.tick(entry=True)
    assert len(await rig.positions()) == 1
    assert [d.status for d in await decisions(rig)] == ["executed", "rejected_by_risk"]


async def test_lessons_and_track_record_flow_back_to_the_portfolio_manager(rig):
    await rig.tick(entry=True)
    rig.data.set("AAA", 102.5)
    await rig.tick()
    rig.data.set("AAA", 100.0)
    await rig.tick(entry=True)
    pm_payloads = [p for a, p in rig.llm.calls if a == "portfolio_manager"]
    assert pm_payloads[0]["lessons"] == [] and pm_payloads[0]["track_record"] == {}
    assert pm_payloads[1]["lessons"][0]["lesson"] == "scripted lesson"
    assert pm_payloads[1]["track_record"]["momentum"] == {"calls": 1, "right": 1, "hit_rate": 1.0}
    assert pm_payloads[1]["portfolio"]["recent_closed"][0]["symbol"] == "AAA"


async def test_unfilled_entry_is_cancelled_not_chased(rig):
    real = rig.data._quote
    rig.llm.responses["portfolio_manager"] = lambda p: pm_enter(100.0, target_pct=5.0)
    orig_place = rig.broker.place_order

    async def jump_then_place(req):
        rig.data.set("AAA", 100.5)     # the market jumps away as the order goes in
        return await orig_place(req)
    rig.broker.place_order = jump_then_place
    await rig.tick(entry=True)
    [d] = await decisions(rig)
    assert d.status == "unfilled" and not await rig.positions() and not rig.broker.orders
    assert (await rig.broker.get_account()).cash == 100_000


async def test_rearm_after_a_drawdown_flatten_does_not_immediately_retrip(rig):
    await rig.tick(entry=True, watchdog=True)
    rig.broker.cash -= 6_500
    await rig.tick(watchdog=True)
    assert rig.kill.state == FLATTEN
    await rig.tick()
    await rig.kill.rearm("REARM", "unit")
    rig.engine.cfg.risk.max_daily_loss_pct = 50          # isolate the drawdown rule from the (intentionally sticky) daily limit
    await rig.tick(watchdog=True)
    assert rig.kill.state == "armed"


async def test_a_position_stuck_mid_exit_by_a_crash_is_recovered_at_startup(rig):
    from app.db.models import Position
    await rig.tick(entry=True)
    [pos] = await rig.positions()
    async with rig.db.session() as s:
        (await s.get(Position, pos.id)).status = "closing"
        await s.commit()
    rig.data.set("AAA", 98.0)
    await rig.tick()
    assert (await rig.positions())[0].status == "closing"     # nobody is managing it...
    await rig.engine.recover()                                # ...until startup recovery
    await rig.tick()
    [pos] = await rig.positions("closed")
    assert pos.exit_reason == "stop_loss" and "engine.recovered" in await rig.event_kinds()


async def test_exit_loop_batches_underlying_quotes(rig):
    rig.llm.responses["scout"] = scout_pick("AAA", "BBB")
    rig.llm.responses["portfolio_manager"] = lambda p: pm_enter(p["price"])
    await rig.tick(entry=True)
    calls = []
    orig = rig.data.get_quotes

    async def spy(symbols):
        calls.append(list(symbols))
        return await orig(symbols)
    rig.data.get_quotes = spy
    await rig.engine.exit_cycle()
    assert calls == [["AAA", "BBB"]]


async def test_concurrent_loops_do_not_race_on_shared_state(rig):
    """All four loops start at once and all ask for portfolio state (a real bug we hit)."""
    import asyncio
    states = await asyncio.gather(*(rig.engine.portfolio_state() for _ in range(12)))
    assert {s.day_start_equity for s in states} == {100_000.0}
    await asyncio.gather(*(rig.db.kv_set("same_key", {"n": i}) for i in range(20)))
    assert "n" in await rig.db.kv_get("same_key")


async def test_exit_loop_uses_a_stop_tightened_during_the_same_cycle(rig):
    """The review agent tightens the stop while the exit loop is mid-pass (a race seen in a real run)."""
    from app.db.models import Position
    await rig.tick(entry=True)
    [pos] = await rig.positions()
    rig.data.set("AAA", 100.8)
    orig = rig.data.get_quotes

    async def tighten_during_quote_fetch(symbols):
        async with rig.db.session() as s:
            (await s.get(Position, pos.id)).stop_price = 101.0
            await s.commit()
        return await orig(symbols)
    rig.data.get_quotes = tighten_during_quote_fetch
    await rig.tick()
    [closed] = await rig.positions("closed")
    assert closed.exit_reason == "stop_loss" and "stop 101.00" in closed.exit_detail


async def test_the_option_shortlist_the_pm_chose_from_is_kept_on_the_decision(rig):
    def plan(payload):
        return pm_enter(100.0, vehicle="long_call", contract_index=payload["option_shortlist"][0]["index"])
    rig.llm.responses["portfolio_manager"] = plan
    await rig.tick(entry=True)
    [d] = await decisions(rig)
    [pos] = await rig.positions()
    chosen = d.context["option_shortlist"][d.pm["contract_index"]]
    assert chosen["type"] == "call" and chosen["strike"] == pos.instrument["strike"]


async def test_market_read_survives_a_restart_within_the_day(rig):
    rig.llm.responses["scout"] = scout_pick()
    await rig.tick(entry=True)
    assert rig.engine.regime.get("label")
    rig.engine.regime, rig.engine.market_note = {}, ""
    await rig.engine.start()
    await rig.engine.stop()
    assert rig.engine.regime.get("label") == "trend_up" and rig.engine.market_note == "scripted"
