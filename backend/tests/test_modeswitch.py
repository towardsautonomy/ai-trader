"""Switching paper <-> live from the dashboard: every gate, the one-time code, the fallback."""

import json

import httpx
import pytest

from app.api.server import create_app
from app.config import LIVE_CONFIRM_PHRASE, Settings, apply_mode_choice
from app.db.models import Decision, Position
from app.modeswitch import consume_code, issue_code, read_choice, write_choice
from app.runtime import Runtime, build
from tests.fakes import frozen_cfg


def test_dashboard_choice_overrides_env_but_never_the_demo(tmp_path):
    s = Settings(data_dir=tmp_path, mode="paper", _env_file=None)
    assert apply_mode_choice(s).mode_source == "env"
    write_choice(tmp_path, "live", live_confirm=LIVE_CONFIRM_PHRASE)
    s = apply_mode_choice(Settings(data_dir=tmp_path, mode="paper", _env_file=None))
    assert (s.mode, s.mode_source, s.live_armed) == ("live", "dashboard", True)
    demo = apply_mode_choice(Settings(data_dir=tmp_path, mode="paper", ignore_clock=True, _env_file=None))
    assert demo.mode == "paper" and demo.mode_source == "env"
    (tmp_path / "mode.json").write_text("{not json")
    assert read_choice(tmp_path) == {}


def test_one_time_code_works_once_and_any_wrong_guess_burns_it(tmp_path):
    code = issue_code(tmp_path)
    assert len(code) == 6 and consume_code(tmp_path, code) and not consume_code(tmp_path, code)
    code = issue_code(tmp_path)
    assert not consume_code(tmp_path, "000000" if code != "000000" else "111111")
    assert not consume_code(tmp_path, code)  # burned by the wrong guess
    issue_code(tmp_path)
    assert (tmp_path / "live_code.json").stat().st_mode & 0o777 == 0o600


def test_expired_code_is_refused(tmp_path):
    code = issue_code(tmp_path)
    p = tmp_path / "live_code.json"
    d = json.loads(p.read_text())
    d["expires"] = "2000-01-01T00:00:00+00:00"
    p.write_text(json.dumps(d))
    assert not consume_code(tmp_path, code)


async def test_refused_dashboard_live_start_falls_back_to_paper_and_says_why(tmp_path):
    write_choice(tmp_path, "live", live_confirm=LIVE_CONFIRM_PHRASE)
    s = apply_mode_choice(Settings(data_dir=tmp_path, data_source="yfinance", openrouter_api_key="k", _env_file=None))
    rt = await build(s, frozen_cfg())
    assert rt.settings.mode == "paper"
    choice = read_choice(tmp_path)
    assert choice["mode"] == "paper" and "data_source=yfinance is not allowed" in choice["live_refused"]
    await rt.db.close()


async def test_live_refused_from_env_still_refuses_loudly(tmp_path):
    from app.runtime import StartupRefused
    with pytest.raises(StartupRefused):
        await build(Settings(data_dir=tmp_path, mode="live", data_source="yfinance", _env_file=None), frozen_cfg())


@pytest.fixture
async def api(rig):
    restarts = []
    rt = Runtime(rig.engine.settings, rig.engine.cfg, rig.db, rig.events, rig.kill, rig.engine, rig.llm, "offline-heuristic")
    app = create_app(rt, start_engine=False, restart=lambda: restarts.append(1))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            c.restarts, c.rt = restarts, rt
            yield c


async def test_status_says_what_is_running(api):
    s = (await api.get("/api/status")).json()
    assert s["mode_source"] == "env" and s["demo"] is False and s["live_refused"] == ""
    assert {m["agent"] for m in s["models"]} >= {"portfolio_manager", "skeptic"} and all("thinking" in m for m in s["models"])


async def test_readiness_lists_every_gate_and_is_not_ready_without_robinhood(api):
    r = (await api.get("/api/live/readiness")).json()
    ids = {c["id"]: c for c in r["checks"]}
    assert not r["ready"] and {"models", "login", "verified", "schemas", "account", "funded", "positions_match", "kill"} <= set(ids)
    assert ids["login"]["ok"] is False and ids["login"]["fix"] == "./trader robinhood login"
    assert ids["track_record"]["blocking"] is False
    rh = (await api.get("/api/robinhood")).json()
    assert rh["logged_in"] is False and rh["in_use"] is False


async def test_live_switch_refused_until_ready_and_needs_phrase_and_code(api, monkeypatch):
    r = await api.post("/api/mode", json={"mode": "live", "confirm": LIVE_CONFIRM_PHRASE, "code": "123456"})
    assert r.status_code == 409 and "not ready" in r.json()["detail"] and api.restarts == []

    async def ready(rt, probe):
        return {"ready": True, "checks": []}
    monkeypatch.setattr("app.api.server.live_readiness", ready)
    assert (await api.post("/api/mode", json={"mode": "live", "confirm": "yes", "code": "1"})).status_code == 400
    assert (await api.post("/api/mode", json={"mode": "live", "confirm": LIVE_CONFIRM_PHRASE, "code": "000000"})).status_code == 403
    assert read_choice(api.rt.settings.data_dir) == {} and api.restarts == []
    code = issue_code(api.rt.settings.data_dir)
    r = await api.post("/api/mode", json={"mode": "live", "confirm": LIVE_CONFIRM_PHRASE, "code": code})
    assert r.status_code == 200 and r.json()["restarting"]
    assert read_choice(api.rt.settings.data_dir)["mode"] == "live"
    import asyncio
    await asyncio.sleep(0.7)
    assert api.restarts == [1]


async def test_leaving_live_is_refused_while_live_positions_are_open(api):
    api.rt.settings.mode = "live"
    async with api.rt.db.session() as s:
        s.add(Decision(id="d", cycle_id="c", mode="live", symbol="AAPL", status="executed", candidate={}))
        s.add(Position(id="p", decision_id="d", mode="live", symbol="AAPL", instrument={"symbol": "AAPL"}, instrument_key="AAPL",
                       direction=1, qty=1, entry_price=100, initial_stop=99, stop_price=99, target_price=102,
                       high_water=100, low_water=100, last_price=100, risk_usd=1))
        await s.commit()
    r = await api.post("/api/mode", json={"mode": "paper"})
    assert r.status_code == 409 and "live positions are open" in r.json()["detail"] and api.restarts == []


async def test_same_mode_and_bad_mode_are_refused(api):
    assert (await api.post("/api/mode", json={"mode": "paper"})).status_code == 409
    assert (await api.post("/api/mode", json={"mode": "yolo"})).status_code == 400
