"""What is allowed to start. Real money must clear every gate."""

import pytest

from app.config import LIVE_CONFIRM_PHRASE, Settings
from app.runtime import StartupRefused, build
from tests.fakes import frozen_cfg


def settings(tmp_path, **kw):
    base = dict(data_dir=tmp_path, mode="paper", data_source="synthetic", openrouter_api_key="", _env_file=None)
    return Settings(**{**base, **kw})


async def test_paper_synthetic_starts_with_offline_agents(tmp_path):
    rt = await build(settings(tmp_path, ignore_clock=True), frozen_cfg())
    assert rt.engine.broker.name == "paper" and rt.engine.data.name == "synthetic" and rt.kill.state == "armed"
    await rt.db.close()


@pytest.mark.parametrize("kw,why", [
    (dict(mode="live"), "AIT_LIVE_CONFIRM"),
    (dict(mode="live", live_confirm="yes"), "AIT_LIVE_CONFIRM"),
    (dict(mode="live", live_confirm=LIVE_CONFIRM_PHRASE), "no model provider"),
    (dict(mode="live", live_confirm=LIVE_CONFIRM_PHRASE, openrouter_api_key="k"), "not allowed in live"),      # synthetic data
    (dict(mode="live", live_confirm=LIVE_CONFIRM_PHRASE, openrouter_api_key="k", data_source="yfinance"), "not allowed in live"),
    (dict(mode="live", live_confirm=LIVE_CONFIRM_PHRASE, openrouter_api_key="k", data_source="robinhood", ignore_clock=True), "IGNORE_CLOCK"),
    (dict(mode="live", live_confirm=LIVE_CONFIRM_PHRASE, openrouter_api_key="k", data_source="robinhood"), "not logged in"),
    (dict(data_source="yfinance", ignore_clock=True), "only allowed with"),
    (dict(data_source="robinhood"), "not logged in"),
])
async def test_startup_is_refused(tmp_path, kw, why):
    with pytest.raises(StartupRefused, match=why):
        await build(settings(tmp_path, **kw), frozen_cfg())


async def test_live_requires_verify_against_the_current_schemas(tmp_path, monkeypatch):
    from app.broker import robinhood_mcp as rh
    from app.db.session import Database
    from app.runtime import VERIFY_KEY
    tools = [{"name": n, "input_schema": {"type": "object"}} for n in rh.USED_TOOLS]

    async def list_tools(self):
        return tools

    async def one_account(self):
        return {"account_number": "A", "option_level": "option_level_2"}

    async def no_account(self):
        raise rh.BrokerError("expected exactly one agentic_allowed Robinhood account, found 0")

    monkeypatch.setattr(rh.RobinhoodMCP, "list_tools", list_tools)
    monkeypatch.setattr(rh.RobinhoodBroker, "agentic_account", one_account)
    (tmp_path / "robinhood_tokens.json").write_text('{"tokens": {"access_token": "x", "token_type": "Bearer"}}')
    s = settings(tmp_path, mode="live", live_confirm=LIVE_CONFIRM_PHRASE, openrouter_api_key="k", data_source="robinhood")
    with pytest.raises(StartupRefused, match="not passed"):
        await build(s, frozen_cfg())

    async def mark(digest):
        db = Database(s.db_url)
        await db.init()
        await db.kv_set(VERIFY_KEY, {"digest": digest})
        await db.close()
    await mark("stale")
    with pytest.raises(StartupRefused, match="not passed"):
        await build(s, frozen_cfg())
    await mark(rh.schema_digest(tools))
    rt = await build(s, frozen_cfg())
    assert rt.engine.broker.name == "robinhood" and rt.engine.data.name == "robinhood"
    await rt.close() if hasattr(rt, "close") else await rt.db.close()

    monkeypatch.setattr(rh.RobinhoodBroker, "agentic_account", no_account)
    with pytest.raises(StartupRefused, match="exactly one"):
        await build(s, frozen_cfg())


async def test_a_kill_marker_left_on_disk_is_honoured_at_startup(tmp_path):
    (tmp_path / "KILL").write_text("from a previous session")
    rt = await build(settings(tmp_path, ignore_clock=True), frozen_cfg())
    assert rt.kill.state == "halted"
    await rt.db.close()


async def test_synthetic_restart_continues_from_open_positions_prices(tmp_path):
    from app.core.types import utcnow
    from app.db.models import Decision, Position
    rt = await build(settings(tmp_path, ignore_clock=True), frozen_cfg())
    async with rt.db.session() as s:
        s.add(Decision(id="d", cycle_id="c", mode="paper", symbol="AAPL", status="executed", candidate={}))
        s.add(Position(id="p", decision_id="d", mode="paper", symbol="AAPL", instrument={"symbol": "AAPL"}, instrument_key="AAPL",
                       direction=1, qty=1, entry_price=812.0, initial_stop=800.0, stop_price=800.0, target_price=830.0,
                       high_water=815.0, low_water=810.0, last_price=814.5, risk_usd=12.0))
        await s.commit()
    await rt.db.close()
    rt2 = await build(settings(tmp_path, ignore_clock=True), frozen_cfg())
    q = (await rt2.engine.data.get_quotes(["AAPL"]))["AAPL"]
    assert abs(q.mid / 814.5 - 1) < 0.01
    await rt2.db.close()


async def test_all_local_models_count_as_real_models(tmp_path):
    """Live mode needs real models, hosted or local; the offline stand-in is never enough."""
    from app.runtime import build_llm, unresolved_models
    from app.db.session import Database
    cfg = frozen_cfg()
    cfg.agents.default_model = "local/llama3.2"
    cfg.agents.models = {}
    s = settings(tmp_path, local_llm_url="http://127.0.0.1:11434/v1")
    assert unresolved_models(s, cfg) == {}
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
    llm, label = build_llm(s, cfg, db)
    assert label == "local"
    await llm.aclose()
    cfg.agents.models = {"portfolio_manager": "anthropic/claude-sonnet-5"}
    assert unresolved_models(s, cfg) == {"portfolio_manager": "anthropic/claude-sonnet-5"}
    await db.close()


def test_ait_model_overrides_every_agent(tmp_path):
    from app.config import load_config
    from pathlib import Path
    cfg = load_config(Path(__file__).parent / "fixtures" / "config.yaml", settings=settings(tmp_path, model="local/qwen2.5:32b"))
    assert {cfg.agents.model_for(a) for a in ("scout", "portfolio_manager", "reviewer")} == {"local/qwen2.5:32b"}
    cfg = load_config(Path(__file__).parent / "fixtures" / "config.yaml", settings=settings(tmp_path))
    assert cfg.agents.model_for("portfolio_manager") != cfg.agents.default_model
