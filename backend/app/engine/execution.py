"""Order execution and the position ledger.

Entries are marketable limits that are cancelled if they do not fill: the desk
never chases. Exits reprice progressively until filled, because being out
matters more than the last few cents. Every order is persisted before it is
sent, so a crash can never leave an order the ledger does not know about.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

from sqlalchemy import select

from app.broker.base import Broker, MarketData
from app.config import AppConfig
from app.core.events import EventLog
from app.core.killswitch import HALTED, KillSwitch
from app.core.types import (
    Candidate, Instrument, OrderReason, OrderRequest, OrderResult, OrderStatus, PMDecision, Side,
    TradeProposal, utcnow,
)
from app.db.models import Order, Position
from app.db.session import Database
from app.risk.engine import validate_exit_order

POLL_SECONDS = 1.0
MAX_FAILED_EXITS = 3


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Executor:
    def __init__(self, broker: Broker, data: MarketData, db: Database, events: EventLog,
                 kill: KillSwitch, cfg: AppConfig, mode: str,
                 on_closed: Callable[[str], Awaitable[None]] | None = None, poll_seconds: float = POLL_SECONDS):
        self.broker, self.data, self.db, self.events, self.kill = broker, data, db, events, kill
        self.cfg, self.mode, self.on_closed, self.poll = cfg, mode, on_closed, poll_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._order_times: deque[float] = deque()
        self.broker_errors = 0   # consecutive; the watchdog reads this
        self.failed_exits = 0

    def orders_last_minute(self) -> int:
        cutoff = time.monotonic() - 60
        while self._order_times and self._order_times[0] < cutoff:
            self._order_times.popleft()
        return len(self._order_times)

    # ------------------------------------------------------------------ plumbing
    async def _record(self, req: OrderRequest, decision_id: str | None, position_id: str | None) -> None:
        async with self.db.session() as s:
            s.add(Order(id=req.client_order_id, decision_id=decision_id, position_id=position_id, mode=self.mode,
                        instrument_key=req.instrument.key, instrument=req.instrument.model_dump(mode="json"),
                        side=req.side.value, qty=req.qty, limit_price=req.limit_price, reason=req.reason.value,
                        status=OrderStatus.PENDING.value))
            await s.commit()

    async def _update(self, order_id: str, res: OrderResult) -> None:
        async with self.db.session() as s:
            o = await s.get(Order, order_id)
            o.status, o.filled_qty, o.avg_fill_price = res.status.value, res.filled_qty, res.avg_fill_price
            o.broker_order_id, o.message, o.raw, o.updated_ts = res.broker_order_id, res.message, res.raw, utcnow()
            await s.commit()

    async def _send_and_wait(self, req: OrderRequest, timeout: float, *, abort_if_killed: bool) -> OrderResult:
        """Place, poll until final or timeout, then cancel. Returns the final state
        (which may carry a partial fill)."""
        self._order_times.append(time.monotonic())
        try:
            res = await self.broker.place_order(req)
            self.broker_errors = 0
        except Exception as e:
            self.broker_errors += 1
            return OrderResult(broker_order_id="", status=OrderStatus.REJECTED, message=f"broker error: {e}")
        deadline = time.monotonic() + timeout
        while res.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.PARTIAL):
            if time.monotonic() >= deadline or (abort_if_killed and self.kill.entries_blocked):
                break
            await asyncio.sleep(self.poll)
            try:
                res = await self.broker.get_order(res.broker_order_id)
                self.broker_errors = 0
            except Exception:
                self.broker_errors += 1
        if res.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.PARTIAL):
            try:
                cancelled = await self.broker.cancel_order(res.broker_order_id)
                # keep whatever filled before the cancel landed
                filled = max(res.filled_qty, cancelled.filled_qty)
                price = cancelled.avg_fill_price or res.avg_fill_price
                res = OrderResult(broker_order_id=res.broker_order_id,
                                  status=OrderStatus.PARTIAL if filled else OrderStatus.CANCELLED,
                                  filled_qty=filled, avg_fill_price=price, message="timed out; cancelled", raw=cancelled.raw)
            except Exception as e:
                self.broker_errors += 1
                res.message = f"cancel failed: {e}"
        return res

    # ------------------------------------------------------------------ entries
    async def enter(self, p: TradeProposal, *, decision_id: str, candidate: Candidate, pm: PMDecision) -> Position | None:
        req = OrderRequest(client_order_id=new_id(), instrument=p.instrument, side=Side.BUY, qty=p.qty,
                           limit_price=p.limit_price, reason=OrderReason.ENTRY, opens_position=True)
        await self._record(req, decision_id, None)
        await self.events.emit("info", "order.submitted", f"BUY {p.qty} {p.instrument.key} @ {p.limit_price:.2f} limit",
                               {"order_id": req.client_order_id, "proposal": p.model_dump(mode="json")}, decision_id=decision_id)
        res = await self._send_and_wait(req, self.cfg.execution.entry_timeout_seconds, abort_if_killed=True)
        await self._update(req.client_order_id, res)
        if res.filled_qty <= 0:
            await self.events.emit("warn", "order.unfilled", f"entry {p.instrument.key} not filled: {res.status.value} {res.message}",
                                   {"order_id": req.client_order_id}, decision_id=decision_id)
            return None

        fee = float(res.raw.get("fee", 0.0)) if isinstance(res.raw, dict) else 0.0
        scale = res.filled_qty / p.qty
        pos = Position(
            id=new_id(), decision_id=decision_id, mode=self.mode, symbol=p.symbol,
            instrument=p.instrument.model_dump(mode="json"), instrument_key=p.instrument.key,
            direction=1 if pm.direction == "long" else -1, qty=res.filled_qty, entry_price=res.avg_fill_price,
            initial_stop=p.stop_price, stop_price=p.stop_price, target_price=p.target_price,
            und_stop=p.und_stop, und_target=p.und_target, max_hold_minutes=p.max_hold_minutes,
            thesis=pm.summary, invalidation=pm.invalidation, atr=candidate.atr,
            high_water=res.avg_fill_price, low_water=res.avg_fill_price, last_price=res.avg_fill_price,
            risk_usd=round(p.risk_usd * scale, 2), fees=fee,
        )
        async with self.db.session() as s:
            s.add(pos)
            o = await s.get(Order, req.client_order_id)
            o.position_id = pos.id
            await s.commit()
        await self.events.emit(
            "info", "position.opened",
            f"OPENED {pos.qty} {pos.instrument_key} @ {pos.entry_price:.2f} | stop {pos.stop_price:.2f} target {pos.target_price:.2f} | risk ${pos.risk_usd:.0f}",
            {"position_id": pos.id}, decision_id=decision_id, position_id=pos.id)
        return pos

    # ------------------------------------------------------------------ exits
    async def close(self, position_id: str, reason: OrderReason, detail: str) -> bool:
        lock = self._locks.setdefault(position_id, asyncio.Lock())
        if lock.locked():
            return False  # another path is already closing it
        async with lock:
            async with self.db.session() as s:
                pos = await s.get(Position, position_id)
                if pos is None or pos.status == "closed":
                    return False
                pos.status = "closing"
                await s.commit()
            inst = Instrument.model_validate(pos.instrument)
            remaining, proceeds, fees = pos.qty, 0.0, 0.0
            await self.events.emit("info", "exit.triggered", f"EXIT {pos.instrument_key}: {reason.value} ({detail})",
                                   {"reason": reason.value, "detail": detail}, decision_id=pos.decision_id, position_id=pos.id)

            ex = self.cfg.execution
            for attempt in range(ex.exit_max_reprices + 1):
                try:
                    bid = (await self.data.get_quote(inst)).bid
                except Exception as e:
                    await self.events.emit("error", "exit.no_quote", f"no quote for {pos.instrument_key}: {e}", position_id=pos.id)
                    await asyncio.sleep(self.poll)
                    continue
                last = attempt == ex.exit_max_reprices
                step = ex.option_exit_offset_pct / 100 if inst.is_option else ex.exit_limit_offset_bps / 10_000
                through = (0.10 if inst.is_option else 0.03) if last else step * (attempt + 1)
                limit = max(0.01, round(bid * (1 - through), 2))
                req = OrderRequest(client_order_id=new_id(), instrument=inst, side=Side.SELL, qty=remaining,
                                   limit_price=limit, reason=reason, opens_position=False)
                guard = validate_exit_order(req, remaining)
                if not guard.passed:  # cannot happen by construction; refuse loudly if it ever does
                    await self.events.emit("critical", "exit.refused", guard.detail, position_id=pos.id)
                    break
                await self._record(req, pos.decision_id, pos.id)
                res = await self._send_and_wait(req, ex.exit_timeout_seconds, abort_if_killed=False)
                await self._update(req.client_order_id, res)
                if res.filled_qty > 0:
                    remaining -= res.filled_qty
                    proceeds += res.filled_qty * res.avg_fill_price
                    fees += float(res.raw.get("fee", 0.0)) if isinstance(res.raw, dict) else 0.0
                if remaining <= 0:
                    break

            sold = pos.qty - remaining
            async with self.db.session() as s:
                pos = await s.get(Position, position_id)
                if sold > 0:
                    pos.realized_pnl += (proceeds - sold * pos.entry_price) * inst.multiplier
                    pos.fees += fees
                if remaining <= 0:
                    pos.status, pos.exit_ts = "closed", utcnow()
                    pos.exit_price = proceeds / sold
                    pos.exit_reason, pos.exit_detail = reason.value, detail
                    pos.realized_pnl = round(pos.realized_pnl - pos.fees, 2)
                else:
                    pos.status, pos.qty = "open", remaining  # the exit loop will try again
                await s.commit()

            if remaining > 0:
                self.failed_exits += 1
                await self.events.emit("critical", "exit.failed", f"could not fully exit {pos.instrument_key}: {remaining} left",
                                       {"remaining": remaining}, position_id=pos.id)
                if self.failed_exits >= MAX_FAILED_EXITS:
                    await self.kill.trip(HALTED, f"{self.failed_exits} failed exits in a row", "executor")
                return False
            self.failed_exits = 0
            r_mult = pos.realized_pnl / pos.risk_usd if pos.risk_usd > 0 else 0.0
            await self.events.emit(
                "info", "position.closed",
                f"CLOSED {pos.instrument_key} @ {pos.exit_price:.2f} | {reason.value} | P&L ${pos.realized_pnl:+.2f} ({r_mult:+.2f}R)",
                {"pnl": pos.realized_pnl, "r": round(r_mult, 2), "reason": reason.value},
                decision_id=pos.decision_id, position_id=pos.id)
        self._locks.pop(position_id, None)
        if self.on_closed:
            await self.on_closed(position_id)
        return True

    async def open_positions(self) -> list[Position]:
        async with self.db.session() as s:
            rows = await s.execute(select(Position).where(Position.mode == self.mode, Position.status != "closed"))
            return list(rows.scalars())
