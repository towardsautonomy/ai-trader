from __future__ import annotations

import pytest

from app.agents.llm import SpendTracker
from app.agents.swarm import Swarm
from app.broker.paper import PaperBroker
from app.config import Settings
from app.core.events import EventLog
from app.core.killswitch import KillSwitch
from app.db.session import Database
from app.engine.orchestrator import Engine
from tests.fakes import frozen_cfg, HOLD, LESSON, REGIME, FakeClock, FakeLLM, ScriptedData, opinion, pm_enter, scout_pick

PRICES = {"AAA": 100.0, "BBB": 50.0, "SPY": 400.0, "QQQ": 300.0}


@pytest.fixture
def cfg():
    c = frozen_cfg()
    c.universe.symbols = ["AAA", "BBB"]
    c.universe.regime_symbols = ["SPY", "QQQ"]
    return c


@pytest.fixture
async def db(tmp_path):
    d = Database(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    await d.init()
    yield d
    await d.close()


@pytest.fixture
def events(db):
    return EventLog(db)


@pytest.fixture
async def kill(db, events, tmp_path):
    k = KillSwitch(db, events, tmp_path)
    await k.load()
    return k


class Rig:
    """A fully wired engine over scripted market, scripted LLM and settable clock."""

    def __init__(self, engine, data, llm, clock, broker, kill, db, events):
        self.engine, self.data, self.llm, self.clock = engine, data, llm, clock
        self.broker, self.kill, self.db, self.events = broker, kill, db, events

    async def tick(self, *, entry=False, review=False, watchdog=False):
        if entry:
            await self.engine.entry_cycle()
        await self.engine.exit_cycle()
        if review:
            await self.engine.review_cycle()
        if watchdog:
            await self.engine.watchdog_cycle()
        await self.engine.drain()

    async def positions(self, status=None):
        from sqlalchemy import select
        from app.db.models import Position
        async with self.db.session() as s:
            rows = list((await s.execute(select(Position).order_by(Position.entry_ts))).scalars())
        return [p for p in rows if status is None or p.status == status]

    async def event_kinds(self):
        from sqlalchemy import select
        from app.db.models import Event
        async with self.db.session() as s:
            return [e.kind for e in (await s.execute(select(Event).order_by(Event.id))).scalars()]


@pytest.fixture
async def rig(cfg, db, events, kill, tmp_path):
    data = ScriptedData(PRICES)
    responses = {
        "regime": REGIME, "scout": scout_pick("AAA"),
        "momentum": opinion("bullish", 0.75), "mean_reversion": opinion("neutral", 0.5),
        "volatility": opinion("bullish", 0.6), "skeptic": opinion("neutral", 0.4),
        "portfolio_manager": pm_enter(100.0), "position_manager": HOLD, "reviewer": LESSON,
    }
    llm = FakeLLM(db, responses)
    broker = PaperBroker(data, db, cfg.paper)
    clock = FakeClock()
    settings = Settings(data_dir=tmp_path, mode="paper", data_source="synthetic", openrouter_api_key="", _env_file=None)
    engine = Engine(settings, cfg, db, events, kill, clock, data, broker, Swarm(llm, cfg.agents), SpendTracker(db))
    engine.executor.poll = 0.0
    cfg.execution.entry_timeout_seconds = 0
    cfg.execution.exit_timeout_seconds = 0
    return Rig(engine, data, llm, clock, broker, kill, db, events)
