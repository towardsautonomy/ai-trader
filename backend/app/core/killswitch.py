"""Kill switch.

States:
  armed     normal operation
  halted    no new entries; open entry orders cancelled; exits keep managing positions
  flatten   halted + every position is liquidated immediately

State is durable (DB + marker file) so a restart never silently re-arms, and the
marker file means the switch works even when the API is wedged or the server is
down: `touch data/KILL` (halt) or `touch data/KILL_FLATTEN`.
Re-arming is always a deliberate manual action.
"""

from __future__ import annotations

from pathlib import Path

from app.core.events import EventLog
from app.core.types import utcnow
from app.db.session import Database

ARMED, HALTED, FLATTEN = "armed", "halted", "flatten"
_SEVERITY = {ARMED: 0, HALTED: 1, FLATTEN: 2}
REARM_PHRASE = "REARM"
_KEY = "killswitch"


class KillSwitch:
    def __init__(self, db: Database, events: EventLog, data_dir: Path):
        self.db = db
        self.events = events
        self.halt_file = data_dir / "KILL"
        self.flatten_file = data_dir / "KILL_FLATTEN"
        self.state = ARMED
        self.reason = ""
        self.source = ""
        self.since = None

    async def load(self) -> None:
        saved = await self.db.kv_get(_KEY)
        if saved:
            self.state, self.reason = saved["state"], saved["reason"]
            self.source, self.since = saved["source"], saved["since"]
        await self.check_files()

    @property
    def entries_blocked(self) -> bool:
        return self.state != ARMED

    @property
    def must_flatten(self) -> bool:
        return self.state == FLATTEN

    def snapshot(self) -> dict:
        return {"state": self.state, "reason": self.reason, "source": self.source, "since": self.since}

    async def trip(self, state: str, reason: str, source: str) -> bool:
        """Escalate to `state`. Never de-escalates. Returns True if state changed."""
        if state not in (HALTED, FLATTEN):
            raise ValueError(f"invalid kill state {state!r}")
        if _SEVERITY[state] <= _SEVERITY[self.state]:
            return False
        self.state, self.reason, self.source = state, reason, source
        self.since = utcnow().isoformat()
        await self.db.kv_set(_KEY, self.snapshot())
        (self.flatten_file if state == FLATTEN else self.halt_file).touch()
        await self.events.emit(
            "critical", "killswitch.tripped",
            f"KILL SWITCH -> {state.upper()} by {source}: {reason}", self.snapshot(),
        )
        return True

    async def check_files(self) -> bool:
        """Pick up marker files dropped by the CLI or by hand."""
        if self.flatten_file.exists():
            return await self.trip(FLATTEN, "KILL_FLATTEN marker file present", "file")
        if self.halt_file.exists():
            return await self.trip(HALTED, "KILL marker file present", "file")
        return False

    async def rearm(self, confirm: str, source: str) -> None:
        if confirm != REARM_PHRASE:
            raise PermissionError(f"re-arm requires confirm phrase {REARM_PHRASE!r}")
        previous = self.snapshot()
        self.halt_file.unlink(missing_ok=True)
        self.flatten_file.unlink(missing_ok=True)
        self.state, self.reason, self.source, self.since = ARMED, "", "", None
        await self.db.kv_set(_KEY, self.snapshot())
        # Re-arming acknowledges the drawdown: measure the next one from here, or the
        # watchdog would flatten again within seconds. (The daily loss limit is NOT
        # reset: re-arming on a day that already hit it halts again, by design.)
        for mode in ("paper", "live"):
            await self.db.kv_delete(f"equity_high_water:{mode}")
        await self.events.emit(
            "warn", "killswitch.rearmed", f"Kill switch re-armed by {source}", {"previous": previous}
        )
