"""Paper broker: simulated fills against whichever MarketData source is active.

Robinhood's agentic MCP has no paper environment, so this is how the system is
validated before real money: real quotes in, simulated fills out. Fills are
deliberately pessimistic (cross the spread, pay slippage and commission).
"""

from __future__ import annotations

from app.broker.base import BrokerError, MarketData
from app.broker.robinhood_mcp import to_tick
from app.config import PaperCfg
from app.core.types import (
    Account, BrokerPosition, Instrument, OrderRequest, OrderResult, OrderStatus, Side,
)
from app.db.session import Database

# Standard listed-option ticks ($0.05 at $3 and above, $0.01 below); Robinhood reports these per contract.
OPTION_TICKS = {"above_tick": "0.05", "below_tick": "0.01", "cutoff_price": "3.00"}
EQUITY_TICKS = {"above_tick": "0.01", "below_tick": "0.0001", "cutoff_price": "1.00"}

_KEY = "paper_account"


class PaperBroker:
    name = "paper"

    def __init__(self, data: MarketData, db: Database, cfg: PaperCfg):
        self.data, self.db, self.cfg = data, db, cfg
        self.cash = cfg.starting_cash
        self.positions: dict[str, dict] = {}  # key -> {instrument, qty, avg_price}
        self.orders: dict[str, dict] = {}     # open (unfilled) orders only
        self.fees_paid = 0.0

    async def load(self) -> None:
        saved = await self.db.kv_get(_KEY)
        if saved:
            self.cash, self.positions = saved["cash"], saved["positions"]
            self.fees_paid = saved.get("fees_paid", 0.0)

    async def _save(self) -> None:
        await self.db.kv_set(_KEY, {"cash": self.cash, "positions": self.positions, "fees_paid": self.fees_paid})

    async def get_account(self) -> Account:
        value = 0.0
        for p in self.positions.values():
            inst = Instrument.model_validate(p["instrument"])
            try:
                px = (await self.data.get_quote(inst)).mid
            except Exception:
                px = p["avg_price"]
            value += px * p["qty"] * inst.multiplier
        return Account(equity=self.cash + value, cash=self.cash, buying_power=self.cash)

    async def get_positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(instrument=Instrument.model_validate(p["instrument"]), qty=p["qty"], avg_price=p["avg_price"])
            for p in self.positions.values()
        ]

    def _slip(self, inst: Instrument) -> float:
        return self.cfg.option_slippage_pct / 100 if inst.is_option else self.cfg.slippage_bps / 10_000

    async def _try_fill(self, req: OrderRequest) -> OrderResult | None:
        q = await self.data.get_quote(req.instrument)
        slip = self._slip(req.instrument)
        if req.side == Side.BUY:
            px = q.ask * (1 + slip)
            if q.ask <= 0 or px > req.limit_price:
                return None
        else:
            px = q.bid * (1 - slip)
            if q.bid <= 0 or px < req.limit_price:
                return None
        # onto a price a real exchange could print, in the direction that costs us, never past the limit
        side = "buy" if req.side == Side.BUY else "sell"
        px = to_tick(px, OPTION_TICKS if req.instrument.is_option else EQUITY_TICKS, side)
        px = min(px, req.limit_price) if side == "buy" else max(px, req.limit_price)
        mult = req.instrument.multiplier
        fee = self.cfg.commission_per_contract * req.qty if req.instrument.is_option else 0.0
        key = req.instrument.key
        held = self.positions.get(key)

        if req.side == Side.BUY:
            cost = px * req.qty * mult + fee
            if cost > self.cash:
                return OrderResult(broker_order_id=req.client_order_id, status=OrderStatus.REJECTED,
                                   message=f"insufficient cash: need {cost:.2f}, have {self.cash:.2f}")
            self.cash -= cost
            if held:
                total = held["qty"] + req.qty
                held["avg_price"] = (held["avg_price"] * held["qty"] + px * req.qty) / total
                held["qty"] = total
            else:
                self.positions[key] = {"instrument": req.instrument.model_dump(mode="json"), "qty": req.qty, "avg_price": px}
        else:
            if not held or held["qty"] < req.qty:  # the simulator never lets a sell open a short
                return OrderResult(broker_order_id=req.client_order_id, status=OrderStatus.REJECTED,
                                   message=f"sell {req.qty} exceeds held {held['qty'] if held else 0}")
            self.cash += px * req.qty * mult - fee
            held["qty"] -= req.qty
            if held["qty"] == 0:
                del self.positions[key]
        self.fees_paid += fee
        await self._save()
        return OrderResult(broker_order_id=req.client_order_id, status=OrderStatus.FILLED,
                           filled_qty=req.qty, avg_fill_price=px, raw={"fee": fee, "quote": q.model_dump(mode="json")})

    async def place_order(self, req: OrderRequest) -> OrderResult:
        if req.qty <= 0 or req.limit_price <= 0:
            return OrderResult(broker_order_id=req.client_order_id, status=OrderStatus.REJECTED, message="bad qty/price")
        result = await self._try_fill(req)
        if result:
            return result
        self.orders[req.client_order_id] = {"req": req}
        return OrderResult(broker_order_id=req.client_order_id, status=OrderStatus.OPEN)

    async def get_order(self, broker_order_id: str) -> OrderResult:
        o = self.orders.get(broker_order_id)
        if not o:
            raise BrokerError(f"unknown or already-final order {broker_order_id}")
        result = await self._try_fill(o["req"])
        if result:
            del self.orders[broker_order_id]
            return result
        return OrderResult(broker_order_id=broker_order_id, status=OrderStatus.OPEN)

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        self.orders.pop(broker_order_id, None)
        return OrderResult(broker_order_id=broker_order_id, status=OrderStatus.CANCELLED)
