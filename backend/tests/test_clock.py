"""Agents act only inside the trading window. Dates are fixed so the calendar logic is pinned."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import SessionCfg
from app.core.clock import MarketClock

ET = ZoneInfo("America/New_York")
CLOCK = MarketClock(SessionCfg(no_entry_first_minutes=5, no_entry_last_minutes=20, flatten_before_close_minutes=10))


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


@pytest.mark.parametrize("when,phase,is_open,entries,flatten", [
    (at(2026, 9, 21, 9, 29), "closed", False, False, False),          # Monday, one minute early
    (at(2026, 9, 21, 9, 31), "opening_buffer", True, False, False),
    (at(2026, 9, 21, 9, 36), "open", True, True, False),
    (at(2026, 9, 21, 12, 0), "open", True, True, False),
    (at(2026, 9, 21, 15, 41), "closing_buffer", True, False, False),
    (at(2026, 9, 21, 15, 51), "flatten", True, False, True),
    (at(2026, 9, 21, 16, 1), "closed", False, False, False),
    (at(2026, 9, 21, 3, 0), "closed", False, False, False),           # overnight
    (at(2026, 9, 19, 12, 0), "closed", False, False, False),          # Saturday
    (at(2026, 11, 26, 12, 0), "closed", False, False, False),         # Thanksgiving
    (at(2026, 12, 25, 12, 0), "closed", False, False, False),         # Christmas
    (at(2026, 11, 27, 12, 45), "closing_buffer", True, False, False),   # half day closes 13:00
    (at(2026, 11, 27, 12, 52), "flatten", True, False, True),
    (at(2026, 11, 27, 13, 30), "closed", False, False, False),        # half day: shut while a normal day is open
])
def test_session_phases(when, phase, is_open, entries, flatten):
    s = CLOCK.state(when)
    assert (s.phase, s.is_open, s.entries_allowed, s.flatten_now) == (phase, is_open, entries, flatten)


def test_closed_state_reports_next_open():
    s = CLOCK.state(at(2026, 9, 19, 12, 0))  # Saturday
    assert s.next_open.astimezone(ET) == at(2026, 9, 21, 9, 30)
    assert CLOCK.seconds_until_open(at(2026, 9, 21, 9, 0)) == 1800


def test_hold_overnight_disables_the_flatten_phase():
    c = MarketClock(SessionCfg(hold_overnight=True))
    s = c.state(at(2026, 9, 21, 15, 55))
    assert not s.flatten_now and not s.entries_allowed


def test_always_open_is_only_for_synthetic_demo():
    s = MarketClock(SessionCfg(), always_open=True).state(at(2026, 12, 25, 3, 0))
    assert s.is_open and s.entries_allowed and not s.flatten_now


def test_session_date_is_exchange_local():
    assert CLOCK.session_date(datetime(2026, 9, 22, 1, 0, tzinfo=ZoneInfo("UTC"))) == "2026-09-21"


@pytest.mark.parametrize("utc,label", [
    ("2026-09-23T15:00:00Z", "2026-09-23"),   # during the session
    ("2026-09-23T21:30:00Z", "2026-09-23"),   # the evening after the close
    ("2026-09-24T05:00:00Z", "2026-09-23"),   # after midnight New York: still yesterday's session
    ("2026-09-24T12:00:00Z", "2026-09-23"),   # pre-market
    ("2026-09-24T13:30:00Z", "2026-09-24"),   # the next session starts at its open
    ("2026-09-26T16:00:00Z", "2026-09-25"),   # Saturday: Friday's session
])
def test_trading_day_follows_sessions_not_midnight(utc, label):
    from datetime import datetime
    from app.config import SessionCfg
    from app.core.clock import MarketClock
    c = MarketClock(SessionCfg())
    day, start = c.trading_day(datetime.fromisoformat(utc.replace("Z", "+00:00")))
    assert day == label and start.strftime("%Y-%m-%d %H:%M") == f"{label} 13:30"
