"""Interfaces. The engine depends only on these, so paper and live share every
line of decision, risk and exit code."""

from __future__ import annotations

from typing import Protocol

from app.core.types import (
    Account, Bar, BrokerPosition, Instrument, OptionContract, OrderRequest, OrderResult, Quote,
)


class MarketData(Protocol):
    name: str

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    async def get_quote(self, instrument: Instrument) -> Quote: ...

    async def get_bars(self, symbols: list[str], interval: str, lookback: int) -> dict[str, list[Bar]]: ...

    async def get_option_chain(self, symbol: str, min_dte: int, max_dte: int) -> list[OptionContract]: ...


class Broker(Protocol):
    name: str

    async def get_account(self) -> Account: ...

    async def get_positions(self) -> list[BrokerPosition]: ...

    async def place_order(self, req: OrderRequest) -> OrderResult: ...

    async def get_order(self, broker_order_id: str) -> OrderResult: ...

    async def cancel_order(self, broker_order_id: str) -> OrderResult: ...


class BrokerError(Exception):
    pass
