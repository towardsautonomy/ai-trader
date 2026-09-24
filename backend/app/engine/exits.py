"""Exit logic: pure functions, no I/O.

`check_exit` runs every few seconds with no LLM in the path. It enforces the
levels the agents set, plus the hard backstops (premium stop, profit ceiling,
time, DTE, end of day, kill switch). `apply_review` applies the position-manager
agent's requested adjustment under one invariant: risk may shrink, never grow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.config import AppConfig
from app.core.types import OrderReason, PositionAction


@dataclass
class PosView:
    """The fields of a position that exit logic needs (decoupled from the ORM)."""

    is_option: bool
    direction: int            # +1 long thesis, -1 short thesis (on the underlying)
    entry_price: float
    stop_price: float         # on the traded instrument
    target_price: float       # on the traded instrument
    und_stop: float
    und_target: float
    entry_ts: datetime
    max_hold_minutes: int
    expiry: date | None = None


def check_exit(pos: PosView, price: float, und_price: float | None, *, now: datetime, cfg: AppConfig,
               kill_flatten: bool = False, session_flatten: bool = False) -> tuple[OrderReason, str] | None:
    """`price` is the bid of the traded instrument (what we could sell at)."""
    if kill_flatten:
        return OrderReason.KILL_SWITCH, "kill switch is in FLATTEN"
    if session_flatten:
        return OrderReason.EOD_FLATTEN, "end-of-day flatten window"

    gain_pct = (price / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0.0
    if price <= pos.stop_price:
        return OrderReason.STOP_LOSS, f"bid {price:.2f} <= stop {pos.stop_price:.2f} ({gain_pct:+.1f}%)"

    ceiling = cfg.options.take_profit_ceiling_pct if pos.is_option else cfg.equity_exits.take_profit_ceiling_pct
    if gain_pct >= ceiling:
        return OrderReason.PROFIT_CEILING, f"gain {gain_pct:+.1f}% reached the hard {ceiling}% ceiling"
    if price >= pos.target_price:
        return OrderReason.TAKE_PROFIT, f"bid {price:.2f} >= target {pos.target_price:.2f} ({gain_pct:+.1f}%)"

    if pos.is_option and und_price is not None and pos.und_stop > 0:
        d = pos.direction
        if (und_price - pos.und_stop) * d <= 0:
            return OrderReason.THESIS_STOP, f"underlying {und_price:.2f} crossed the thesis stop {pos.und_stop:.2f}"
        if pos.und_target > 0 and (und_price - pos.und_target) * d >= 0:
            return OrderReason.THESIS_TARGET, f"underlying {und_price:.2f} reached the thesis target {pos.und_target:.2f}"

    if pos.is_option and pos.expiry is not None:
        dte = (pos.expiry - now.date()).days
        if dte <= cfg.options.exit_at_dte:
            return OrderReason.DTE_EXIT, f"{dte} days to expiry <= {cfg.options.exit_at_dte}"

    held = (now - pos.entry_ts).total_seconds() / 60
    if pos.max_hold_minutes > 0 and held >= pos.max_hold_minutes:
        return OrderReason.TIME_STOP, f"held {held:.0f}m >= planned {pos.max_hold_minutes}m"
    return None


def apply_review(pos: PosView, act: PositionAction, price: float, und_price: float | None,
                 cfg: AppConfig) -> tuple[dict, list[str]]:
    """Return (changes to apply, notes). Stops only ever tighten; targets stay under the ceiling."""
    changes: dict = {}
    notes: list[str] = []
    if act.action != "adjust":
        return changes, notes

    if pos.is_option:
        # The agent speaks in underlying prices; the premium stop and ceiling are hard and not adjustable.
        d, ref = pos.direction, und_price
        if ref is None:
            return changes, ["no underlying price: adjustment ignored"]
        if act.new_stop is not None:
            tighter = (act.new_stop - pos.und_stop) * d > 0
            safe_side = (ref - act.new_stop) * d > 0
            if tighter and safe_side:
                changes["und_stop"] = act.new_stop
            else:
                notes.append(f"stop {act.new_stop} refused: " + ("it would loosen the stop" if not tighter else "it is through the current price"))
        if act.new_target is not None:
            if (act.new_target - ref) * d > 0:
                changes["und_target"] = act.new_target
            else:
                notes.append(f"target {act.new_target} refused: it is not beyond the current price")
        return changes, notes

    # Validate the rounded values: a level a fraction of a cent from the market must not round onto it.
    if act.new_stop is not None:
        stop = round(act.new_stop, 2)
        if pos.stop_price < stop < price:
            changes["stop_price"] = stop
        else:
            notes.append(f"stop {act.new_stop} refused: " + ("it would loosen the stop" if stop <= pos.stop_price else "it is at or above the current price"))
    if act.new_target is not None:
        target = round(act.new_target, 2)
        ceiling = round(pos.entry_price * (1 + cfg.equity_exits.take_profit_ceiling_pct / 100), 2)
        if target <= price:
            notes.append(f"target {act.new_target} refused: it is not above the current price")
        elif target > ceiling:
            changes["target_price"] = ceiling
            notes.append(f"target {act.new_target} pulled to the {cfg.equity_exits.take_profit_ceiling_pct}% ceiling {ceiling}")
        else:
            changes["target_price"] = target
    return changes, notes
