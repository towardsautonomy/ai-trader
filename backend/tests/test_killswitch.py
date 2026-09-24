import pytest

from app.core.events import EventLog
from app.core.killswitch import ARMED, FLATTEN, HALTED, KillSwitch


async def test_starts_armed(kill):
    assert kill.state == ARMED and not kill.entries_blocked and not kill.must_flatten


async def test_halt_blocks_entries_but_does_not_flatten(kill):
    assert await kill.trip(HALTED, "test", "unit")
    assert kill.entries_blocked and not kill.must_flatten
    assert (kill.reason, kill.source) == ("test", "unit")


async def test_escalates_but_never_deescalates(kill):
    await kill.trip(FLATTEN, "big", "unit")
    assert not await kill.trip(HALTED, "smaller", "unit")   # no downgrade
    assert kill.state == FLATTEN and kill.reason == "big"
    assert not await kill.trip(FLATTEN, "again", "unit")    # idempotent


async def test_invalid_state_is_rejected(kill):
    with pytest.raises(ValueError):
        await kill.trip("armed", "nope", "unit")


async def test_survives_restart(db, events, tmp_path, kill):
    await kill.trip(HALTED, "persist me", "unit")
    reborn = KillSwitch(db, EventLog(db), tmp_path)
    await reborn.load()
    assert reborn.state == HALTED and reborn.reason == "persist me"


async def test_marker_files_trip_the_switch(db, events, tmp_path):
    (tmp_path / "KILL").touch()
    k = KillSwitch(db, events, tmp_path)
    await k.load()
    assert k.state == HALTED and k.source == "file"
    (tmp_path / "KILL_FLATTEN").touch()
    assert await k.check_files()
    assert k.state == FLATTEN


async def test_marker_file_wins_even_if_db_says_armed(db, events, tmp_path):
    """The CLI can kill with the server down: the file alone must be enough."""
    k = KillSwitch(db, events, tmp_path)
    await k.load()
    assert k.state == ARMED
    (tmp_path / "KILL_FLATTEN").write_text("from cli")
    fresh = KillSwitch(db, events, tmp_path)
    await fresh.load()
    assert fresh.must_flatten


async def test_rearm_requires_the_phrase_and_clears_everything(kill, tmp_path):
    await kill.trip(FLATTEN, "x", "unit")
    assert (tmp_path / "KILL_FLATTEN").exists()
    with pytest.raises(PermissionError):
        await kill.rearm("yes please", "unit")
    assert kill.state == FLATTEN
    await kill.rearm("REARM", "unit")
    assert kill.state == ARMED and not (tmp_path / "KILL_FLATTEN").exists() and not (tmp_path / "KILL").exists()
    assert not await kill.check_files()


async def test_trips_and_rearms_are_audited(kill, db):
    from sqlalchemy import select
    from app.db.models import Event
    await kill.trip(HALTED, "audit", "unit")
    await kill.rearm("REARM", "unit")
    async with db.session() as s:
        kinds = [e.kind for e in (await s.execute(select(Event))).scalars()]
    assert kinds == ["killswitch.tripped", "killswitch.rearmed"]


async def test_rearm_resets_the_drawdown_reference_so_it_does_not_retrip(kill, db):
    await db.kv_set("equity_high_water:paper", {"equity": 107_000})
    await kill.trip(FLATTEN, "drawdown", "watchdog")
    await kill.rearm("REARM", "unit")
    assert await db.kv_get("equity_high_water:paper") is None
