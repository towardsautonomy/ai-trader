"""Builds the running system from settings, and enforces what may run in live mode."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.llm import LLM, LOCAL_PREFIX, RouterLLM, SpendTracker, local_llm, openrouter
from app.agents.swarm import AGENTS
from app.agents.offline import OfflineLLM
from app.agents.swarm import Swarm
from app.broker.base import Broker, MarketData
from app.broker.paper import PaperBroker
from app.broker.robinhood_mcp import RobinhoodBroker, RobinhoodMCP, schema_digest
from app.config import LIVE_CONFIRM_PHRASE, AppConfig, Settings, get_settings, load_config
from app.core.clock import MarketClock
from app.core.events import EventLog
from app.core.killswitch import KillSwitch
from app.db.session import Database
from app.engine.orchestrator import Engine
from app.marketdata.synthetic import SyntheticData
from app.marketdata.yahoo import YahooData

VERIFY_KEY = "robinhood_verified"


class StartupRefused(Exception):
    pass


@dataclass
class Runtime:
    settings: Settings
    cfg: AppConfig
    db: Database
    events: EventLog
    kill: KillSwitch
    engine: Engine
    llm: LLM
    llm_label: str  # "openrouter", "local", "openrouter+local" or "offline-heuristic"
    robinhood: RobinhoodMCP | None = None

    async def shutdown(self) -> None:
        await self.engine.stop()
        if isinstance(self.llm, RouterLLM):
            await self.llm.aclose()
        if self.robinhood:
            await self.robinhood.close()
        await self.db.close()


def unresolved_models(settings: Settings, cfg: AppConfig) -> dict[str, str]:
    """agent -> model, for every agent whose model has no configured provider."""
    out = {}
    for a in AGENTS:
        m = cfg.agents.model_for(a)
        has = settings.local_llm_url if m.startswith(LOCAL_PREFIX) else settings.openrouter_api_key
        if not has:
            out[a] = m
    return out


def build_llm(settings: Settings, cfg: AppConfig, db: Database) -> tuple[LLM, str]:
    hosted = openrouter(settings.openrouter_api_key, db, timeout=cfg.agents.timeout_seconds) if settings.openrouter_api_key else None
    local = local_llm(settings.local_llm_url, settings.local_llm_api_key, db, timeout=settings.local_llm_timeout,
                      thinking=settings.local_llm_thinking) if settings.local_llm_url else None
    if not hosted and not local:
        return OfflineLLM(db), "offline-heuristic"
    router = RouterLLM(hosted, local, db, thinking=cfg.agents.thinking)
    return router, router.name




async def build(settings: Settings | None = None, cfg: AppConfig | None = None) -> Runtime:
    settings, cfg = settings or get_settings(), cfg or load_config(settings=settings)
    try:
        return await _build(settings, cfg)
    except StartupRefused as e:
        if not (settings.mode == "live" and settings.mode_source == "dashboard"):
            raise
        # Live was chosen in the dashboard but a gate refused it: fall back to paper and say why,
        # rather than leaving the dashboard with no backend.
        from app.modeswitch import write_choice
        write_choice(settings.data_dir, "paper", live_refused=str(e))
        settings.mode, settings.live_confirm = "paper", ""
        rt = await _build(settings, load_config(settings=settings))
        await rt.events.emit("critical", "engine.live_refused", f"LIVE start refused, running PAPER instead: {e}")
        return rt


async def _build(settings: Settings, cfg: AppConfig) -> Runtime:
    live = settings.mode == "live"

    # ---- everything that must be true before real money moves
    if live:
        problems = []
        if not settings.live_armed:
            problems.append(f"AIT_LIVE_CONFIRM must equal {LIVE_CONFIRM_PHRASE!r}")
        if unresolved_models(settings, cfg):
            problems.append(f"no model provider for {unresolved_models(settings, cfg)}: the offline heuristic never trades real money")
        if settings.data_source not in ("auto", "robinhood"):
            problems.append(f"data_source={settings.data_source} is not allowed in live mode")
        if settings.ignore_clock:
            problems.append("AIT_IGNORE_CLOCK is not allowed in live mode")
        if problems:
            raise StartupRefused("live mode refused:\n  - " + "\n  - ".join(problems))
    if settings.ignore_clock and settings.data_source != "synthetic":
        raise StartupRefused("AIT_IGNORE_CLOCK is only allowed with AIT_DATA_SOURCE=synthetic")

    db = Database(settings.db_url)
    await db.init()
    try:
        return await _assemble(settings, cfg, db, live)
    except BaseException:
        await db.close()
        raise


async def _assemble(settings: Settings, cfg: AppConfig, db: Database, live: bool) -> Runtime:
    events = EventLog(db)
    kill = KillSwitch(db, events, settings.data_dir)
    await kill.load()

    # ---- market data and broker
    mcp = RobinhoodMCP(settings.robinhood_mcp_url, settings.data_dir)
    verified = await db.kv_get(VERIFY_KEY)
    source = settings.data_source
    if source == "auto":
        source = "robinhood" if mcp.logged_in and verified else "yfinance"

    robinhood = None
    data: MarketData
    if source == "synthetic":
        data = SyntheticData(sorted(set(cfg.universe.symbols) | set(cfg.universe.regime_symbols)))
        from sqlalchemy import select
        from app.db.models import Position
        async with db.session() as s:
            for pos in (await s.execute(select(Position).where(Position.status != "closed", Position.mode == settings.mode))).scalars():
                if not pos.instrument.get("right"):
                    data.anchor(pos.symbol, pos.last_price)
    elif source == "yfinance":
        data = YahooData()
    else:
        if not mcp.logged_in:
            raise StartupRefused("not logged in to Robinhood: run `ait robinhood login`")
        robinhood = mcp
        data = RobinhoodBroker(mcp)

    broker: Broker
    if live:
        try:
            current = schema_digest(await mcp.list_tools())
            await data.agentic_account()  # exactly one agent-tradable account, or refuse
            options_ok = await data.options_allowed()
        except Exception as e:
            raise StartupRefused(f"Robinhood check failed: {type(e).__name__}: {e}")
        if not verified or verified.get("digest") != current:
            raise StartupRefused("Robinhood's tool schemas have not passed `ait robinhood verify` in their current form")
        broker = data  # type: ignore[assignment]  # RobinhoodBroker is both
        if not options_ok:
            cfg.options.enabled = False
            await events.emit("warn", "engine.options_off", "the agentic account has no options level 2+: options disabled")
    else:
        paper = PaperBroker(data, db, cfg.paper)
        await paper.load()
        broker = paper

    llm, label = build_llm(settings, cfg, db)
    clock = MarketClock(cfg.session, always_open=settings.ignore_clock)
    engine = Engine(settings, cfg, db, events, kill, clock, data, broker, Swarm(llm, cfg.agents), SpendTracker(db))
    if label == "offline-heuristic":
        await events.emit("warn", "engine.offline_llm", "no model provider configured: agents are the OFFLINE HEURISTIC stand-in, not real models")
    elif unresolved_models(settings, cfg):
        await events.emit("error", "engine.unresolved_models",
                          f"these agents have a model with no provider and will fail every call: {unresolved_models(settings, cfg)}")
    if live:
        await events.emit("critical", "engine.live", "LIVE MODE: orders are real and use real money")
    return Runtime(settings, cfg, db, events, kill, engine, llm, label, robinhood)
