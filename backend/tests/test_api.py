import httpx
import pytest

from app.api.server import create_app
from app.runtime import Runtime


@pytest.fixture
async def api(rig):
    rt = Runtime(rig.engine.settings, rig.engine.cfg, rig.db, rig.events, rig.kill, rig.engine, rig.llm, "offline-heuristic")
    app = create_app(rt, start_engine=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_status_reports_mode_kill_state_account_and_limits(api, rig):
    s = (await api.get("/api/status")).json()
    assert s["mode"] == "paper" and s["kill"]["state"] == "armed" and s["llm"] == "offline-heuristic"
    assert s["account"]["equity"] == 100_000 and s["session"]["phase"] == "open"
    assert s["limits"]["max_daily_loss_pct"] == 2.0


async def test_status_still_answers_when_the_broker_is_down(api, rig):
    async def boom():
        raise ConnectionError("broker down")
    rig.broker.get_account = boom
    s = (await api.get("/api/status")).json()
    assert s["account"] == {} and "broker down" in s["account_error"] and s["kill"]["state"] == "armed"


async def test_kill_halt_flatten_and_rearm(api, rig):
    r = (await api.post("/api/kill", json={"level": "halt", "reason": "ui"})).json()
    assert r["changed"] and r["kill"]["state"] == "halted" and rig.kill.entries_blocked
    assert (await api.post("/api/kill", json={"level": "flatten", "reason": "ui"})).json()["kill"]["state"] == "flatten"
    assert not (await api.post("/api/kill", json={"level": "halt"})).json()["changed"]          # no downgrade
    assert (await api.post("/api/kill", json={"level": "nuke"})).status_code == 400
    assert (await api.post("/api/kill/rearm", json={"confirm": "please"})).status_code == 400
    assert rig.kill.state == "flatten"
    assert (await api.post("/api/kill/rearm", json={"confirm": "REARM"})).json()["kill"]["state"] == "armed"


async def test_mutations_require_the_token_when_one_is_configured(api, rig):
    rig.engine.settings.api_token = "s3cret"
    assert (await api.post("/api/kill", json={"level": "halt"})).status_code == 401
    assert rig.kill.state == "armed"
    assert (await api.get("/api/status")).status_code == 200                                     # reads stay open on localhost
    ok = await api.post("/api/kill", json={"level": "halt"}, headers={"X-API-Token": "s3cret"})
    assert ok.status_code == 200 and rig.kill.state == "halted"


async def test_full_trace_is_reachable_from_a_position(api, rig):
    await rig.tick(entry=True)
    rig.data.set("AAA", 102.5)
    await rig.tick()
    [p] = (await api.get("/api/positions", params={"status": "closed"})).json()
    assert p["exit_reason"] == "take_profit" and p["pnl_r"] > 0 and not p["is_option"]
    t = (await api.get(f"/api/positions/{p['id']}")).json()
    assert [o["reason"] for o in t["orders"]] == ["entry", "take_profit"] and t["lesson"]["lesson"] == "scripted lesson"
    d = t["decision"]
    assert d["decision"]["status"] == "executed" and len(d["opinions"]) == 4 and d["position_id"] == p["id"]
    assert all(c["passed"] for c in d["decision"]["risk"]["checks"])
    call = (await api.get(f"/api/llm_calls/{d['llm_calls'][-1]['id']}")).json()
    assert call["agent"] == "portfolio_manager" and "opinions" in call["prompt"]                  # exactly what the PM saw
    assert (await api.get("/api/positions/nope")).status_code == 404
    assert (await api.get("/api/decisions/nope")).status_code == 404


async def test_decision_feed_includes_non_trades(api, rig):
    rig.llm.responses["portfolio_manager"] = {"action": "skip", "confidence": 0.6, "summary": "not clean"}
    await rig.tick(entry=True)
    [d] = (await api.get("/api/decisions")).json()
    assert d["status"] == "skipped_by_pm" and d["pm_summary"] == "not clean" and len(d["stances"]) == 4
    assert (await api.get("/api/decisions", params={"status": "executed"})).json() == []


async def test_events_feed_paginates_forward(api, rig):
    await rig.tick(entry=True)
    events = (await api.get("/api/events")).json()
    assert [e["id"] for e in events] == sorted(e["id"] for e in events) and len(events) >= 4
    newer = (await api.get("/api/events", params={"after_id": events[-2]["id"]})).json()
    assert [e["id"] for e in newer] == [events[-1]["id"]]


async def test_stats_agents_lessons_orders_equity_config(api, rig):
    await rig.tick(entry=True, watchdog=True)
    rig.data.set("AAA", 98.5)
    await rig.tick()
    stats = (await api.get("/api/stats")).json()
    assert stats["trades"] == 1 and stats["wins"] == 0 and stats["by_exit_reason"]["stop_loss"]["count"] == 1
    agents = {a["agent"]: a for a in (await api.get("/api/agents")).json()}
    assert len(agents) == 9 and agents["momentum"]["calls"] == 1 and agents["momentum"]["hit_rate"] == 0.0   # bullish, and it lost
    assert agents["portfolio_manager"]["calls_today"] == 1
    assert len((await api.get("/api/lessons")).json()) == 1
    assert len((await api.get("/api/orders")).json()) == 2
    assert len((await api.get("/api/equity")).json()) == 1
    assert (await api.get("/api/config")).json()["risk"]["max_daily_loss_pct"] == 2.0


async def test_manual_close(api, rig):
    await rig.tick(entry=True)
    [p] = (await api.get("/api/positions")).json()
    assert (await api.post(f"/api/positions/{p['id']}/close")).json() == {"ok": True, "status": "closing"}
    import asyncio
    await asyncio.sleep(0.05)
    await rig.engine.drain()
    [closed] = await rig.positions("closed")
    assert closed.exit_reason == "manual"


async def test_status_polling_does_not_hammer_the_broker(api, rig):
    n = []
    orig = rig.broker.get_account

    async def counted():
        n.append(1)
        return await orig()
    rig.broker.get_account = counted
    for _ in range(5):
        await api.get("/api/status")
    assert len(n) == 1


async def test_events_page_backwards_and_filter(api, rig):
    await rig.tick(entry=True)
    newest = (await api.get("/api/events", params={"limit": 2})).json()
    older = (await api.get("/api/events", params={"limit": 2, "before_id": newest[0]["id"]})).json()
    assert [e["id"] for e in older] == [newest[0]["id"] - 2, newest[0]["id"] - 1]
    assert {e["kind"] for e in (await api.get("/api/events", params={"kind": "position."})).json()} == {"position.opened"}
    assert all("OPENED" in e["message"] for e in (await api.get("/api/events", params={"q": "opened"})).json())


async def test_position_trace_includes_the_review_and_lesson_model_calls(api, rig):
    await rig.tick(entry=True, review=True)
    rig.data.set("AAA", 102.5)
    await rig.tick()
    [p] = (await api.get("/api/positions", params={"status": "closed"})).json()
    t = (await api.get(f"/api/positions/{p['id']}")).json()
    assert [c["agent"] for c in t["llm_calls"]] == ["position_manager", "reviewer"] and len(t["reviews"]) == 1
    assert "thesis" in (await api.get(f"/api/llm_calls/{t['llm_calls'][0]['id']}")).json()["prompt"]


async def test_close_endpoint_is_honest_about_unknown_and_closed_positions(api, rig):
    assert (await api.post("/api/positions/nope/close")).status_code == 404
    await rig.tick(entry=True)
    rig.data.set("AAA", 102.5)
    await rig.tick()
    [p] = (await api.get("/api/positions", params={"status": "closed"})).json()
    assert (await api.post(f"/api/positions/{p['id']}/close")).status_code == 409


async def test_status_exposes_engine_liveness(api, rig):
    import asyncio, time
    task = asyncio.create_task(rig.engine._loop("exit", 0.01, rig.engine.exit_cycle))
    await asyncio.sleep(0.05)
    task.cancel()
    live = (await api.get("/api/status")).json()["engine"]["exit"]
    assert live["overdue"] is False and live["seconds_since_tick"] < 5 and live["last_error"] == ""
    rig.engine.heartbeat["exit"]["mono"] = time.monotonic() - 3600        # a wedged loop
    assert (await api.get("/api/status")).json()["engine"]["exit"]["overdue"] is True


async def test_extra_cors_origins_are_honoured(rig):
    rig.engine.settings.cors_origins = "http://192.168.1.20:3400, *:3400"
    rt = Runtime(rig.engine.settings, rig.engine.cfg, rig.db, rig.events, rig.kill, rig.engine, rig.llm, "offline-heuristic")
    app = create_app(rt, start_engine=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for origin, ok in [("http://192.168.1.20:3400", True), ("http://box.local:3400", True), ("http://10.8.0.5:3400", True),
                               ("http://localhost:3400", True), ("http://evil.example", False), ("http://10.8.0.5:3401", False)]:
                r = await c.options("/api/kill", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
                assert (r.headers.get("access-control-allow-origin") == origin) is ok, origin


async def test_history_groups_closed_trades_by_trading_session(api, rig):
    from datetime import timedelta
    from app.core.types import utcnow
    from app.db.models import Decision, Position
    now = utcnow()
    async with rig.db.session() as s:
        s.add(Decision(id="d", cycle_id="c", mode="paper", symbol="AAPL", status="executed", candidate={}))
        for i, pnl in enumerate([12.5, -4.0, 3.25]):
            s.add(Position(id=f"p{i}", decision_id="d", mode="paper", symbol="AAPL", instrument={"symbol": "AAPL"}, instrument_key="AAPL",
                           direction=1, qty=1, entry_price=100, initial_stop=99, stop_price=99, target_price=102, high_water=100,
                           low_water=100, last_price=100, risk_usd=10, status="closed", realized_pnl=pnl, exit_price=100 + pnl,
                           entry_ts=now - timedelta(minutes=30 + i), exit_ts=now - timedelta(minutes=i), exit_reason="agent_exit"))
        await s.commit()
    h = (await api.get("/api/history")).json()
    assert h["totals"] == {"trades": 3, "wins": 2, "pnl": 11.75}
    [day] = h["days"]
    assert (day["day"], day["trades"], day["wins"], day["pnl"], day["cumulative"], day["best"], day["worst"]) == ("2026-09-21", 3, 2, 11.75, 11.75, 12.5, -4.0)
    assert h["trades"][0]["hold_minutes"] == 30.0 and h["trades"][0]["day"] == "2026-09-21"
