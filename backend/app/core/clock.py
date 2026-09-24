"""Trading window. Agents are only active inside the regular NYSE session;
holidays and early closes come from the exchange calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import exchange_calendars as xcals
import pandas as pd

from app.config import SessionCfg
from app.core.types import utcnow


@dataclass(frozen=True)
class SessionState:
    is_open: bool
    entries_allowed: bool
    flatten_now: bool
    phase: str  # closed | opening_buffer | open | closing_buffer | flatten
    session_open: datetime | None
    session_close: datetime | None
    next_open: datetime | None
    seconds_to_close: float | None


class MarketClock:
    def __init__(self, cfg: SessionCfg, always_open: bool = False):
        self.cfg = cfg
        self.always_open = always_open  # paper + synthetic only
        self._cal = xcals.get_calendar("XNYS")

    def state(self, now: datetime | None = None) -> SessionState:
        now = now or utcnow()
        if self.always_open:
            return SessionState(True, True, False, "open", None, None, None, None)

        ts = pd.Timestamp(now).tz_convert("UTC")
        minute = ts.floor("min")
        if not self._cal.is_open_on_minute(minute):
            nxt = self._cal.next_open(minute).to_pydatetime()
            return SessionState(False, False, False, "closed", None, None, nxt, None)

        session = self._cal.minute_to_session(minute)
        open_ = self._cal.session_open(session).to_pydatetime()
        close = self._cal.session_close(session).to_pydatetime()
        to_close = (close - now).total_seconds()
        since_open = (now - open_).total_seconds()

        flatten = (
            not self.cfg.hold_overnight
            and to_close <= self.cfg.flatten_before_close_minutes * 60
        )
        if flatten:
            phase, entries = "flatten", False
        elif to_close <= self.cfg.no_entry_last_minutes * 60:
            phase, entries = "closing_buffer", False
        elif since_open < self.cfg.no_entry_first_minutes * 60:
            phase, entries = "opening_buffer", False
        else:
            phase, entries = "open", True
        return SessionState(True, entries, flatten, phase, open_, close, None, to_close)

    def seconds_until_open(self, now: datetime | None = None) -> float:
        st = self.state(now)
        if st.is_open or st.next_open is None:
            return 0.0
        return max(0.0, (st.next_open - (now or utcnow())).total_seconds())

    def trading_day(self, now: datetime | None = None) -> tuple[str, datetime]:
        """The session that "today" refers to, and when it opened: the current session while the
        market is open, otherwise the most recent one. So the evening after a session, and the
        pre-market next morning, still report that session's P&L and trades; the next session's
        numbers start at its open. (Midnight is not a trading boundary.)"""
        now = now or utcnow()
        ts = pd.Timestamp(now).tz_convert("UTC")
        if self.always_open:  # the demo has no sessions: calendar days in exchange time
            ny = ts.tz_convert("America/New_York").normalize()
            return ny.strftime("%Y-%m-%d"), ny.tz_convert("UTC").to_pydatetime()
        session = self._cal.minute_to_session(ts.floor("min"), direction="previous")
        return session.strftime("%Y-%m-%d"), self._cal.session_open(session).to_pydatetime()

    def session_date(self, now: datetime | None = None) -> str:
        """Trading day label; used to key daily limits and the day's starting equity."""
        return self.trading_day(now)[0]


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)
