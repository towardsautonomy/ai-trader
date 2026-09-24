"""Robinhood Agentic Trading via its official MCP server.

The server (https://agent.robinhood.com/mcp/trading) speaks standard MCP over
streamable HTTP with OAuth 2.1 (dynamic client registration + PKCE), so this
daemon authenticates once in a browser and then refreshes tokens on its own.

Robinhood does not publish the tool schemas outside an authenticated session,
so they were learned from the live server after login (`discover` dumps them).
`verify` exercises the read-only calls plus a simulated order and pins a digest of
the schemas used here; live mode refuses to start if they change afterwards.

IMPORTANT: this server has no paper environment. Every order placed through it
is a real order in your Agentic account.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
import webbrowser
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta, timezone
from math import ceil, floor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession, types
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from app.broker.base import BrokerError
from app.core.types import (
    Account, Bar, BrokerPosition, Instrument, OptionContract, OrderRequest, OrderResult, OrderStatus, Quote, Right,
)

# Robinhood answers the MCP session-termination request with 400 on every disconnect; harmless noise.
logging.getLogger("mcp.client.streamable_http").addFilter(lambda r: "Session termination failed" not in r.getMessage())

CALLBACK_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/callback"


class FileTokenStorage(TokenStorage):
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _write(self, d: dict) -> None:
        self.path.write_text(json.dumps(d))
        os.chmod(self.path, 0o600)

    async def get_tokens(self) -> OAuthToken | None:
        d = self._read().get("tokens")
        return OAuthToken.model_validate(d) if d else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._write({**self._read(), "tokens": tokens.model_dump(mode="json")})

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        d = self._read().get("client")
        return OAuthClientInformationFull.model_validate(d) if d else None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        self._write({**self._read(), "client": info.model_dump(mode="json")})


def _parse_callback(url_or_query: str) -> AuthorizationCodeResult | None:
    s = url_or_query.strip()
    q = parse_qs(urlparse(s).query if "://" in s or s.startswith("/") else s.lstrip("?"))
    if "code" not in q:
        return None
    return AuthorizationCodeResult(code=q["code"][0], state=q.get("state", [None])[0], iss=q.get("iss", [None])[0])


async def _wait_for_callback() -> AuthorizationCodeResult:
    """After approval Robinhood redirects the browser to http://127.0.0.1:8765/callback.
    That works when the browser runs on this machine. From a remote browser the redirect
    lands on the wrong localhost and fails to load; the code is still in its address bar,
    so the URL can be pasted here instead. Whichever arrives first wins."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[AuthorizationCodeResult] = loop.create_future()

    def deliver(res: AuthorizationCodeResult | None) -> None:
        if res and not fut.done():
            fut.set_result(res)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readline()).decode()
        body = b"AI Trader: Robinhood login complete. You can close this tab."
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        await writer.drain()
        writer.close()
        deliver(_parse_callback(line.split(" ")[1]) if " " in line else None)

    server = None
    try:
        server = await asyncio.start_server(handle, "127.0.0.1", CALLBACK_PORT)
    except OSError as e:
        print(f"(could not listen on 127.0.0.1:{CALLBACK_PORT}: {e}; paste the redirect URL instead)")
    print("After approving, the browser is sent to http://127.0.0.1:8765/callback?...\n"
          "  - browser on THIS machine: that completes the login by itself.\n"
          "  - browser elsewhere: that page will fail to load. Copy the full URL from the address bar\n"
          "    and paste it here, then press Enter.\n")

    def read_paste() -> None:
        import sys
        while not fut.done():
            line = sys.stdin.readline()
            if not line:
                return
            res = _parse_callback(line)
            if res:
                loop.call_soon_threadsafe(deliver, res)
            elif line.strip():
                print("that does not contain a code=... parameter; paste the whole redirect URL")

    import threading
    threading.Thread(target=read_paste, daemon=True).start()
    try:
        return await asyncio.wait_for(fut, timeout=600)
    finally:
        if server:
            server.close()


class RobinhoodMCP:
    """A persistent, authenticated MCP session."""

    def __init__(self, url: str, data_dir: Path, interactive: bool = False):
        self.url, self.data_dir, self.interactive = url, data_dir, interactive
        self.token_path = data_dir / "robinhood_tokens.json"
        self._session: ClientSession | None = None
        self._runner: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def logged_in(self) -> bool:
        return self.token_path.exists() and "tokens" in json.loads(self.token_path.read_text())

    def _auth(self) -> OAuthClientProvider:
        async def redirect(auth_url: str) -> None:
            if not self.interactive:
                raise BrokerError("Robinhood session expired: run `ait robinhood login` again")
            print(f"\nOpen this URL to authorise AI Trader with Robinhood:\n\n  {auth_url}\n")
            webbrowser.open(auth_url)

        return OAuthClientProvider(
            server_url=self.url,
            client_metadata=OAuthClientMetadata(
                client_name="AI Trader", redirect_uris=[REDIRECT_URI], grant_types=["authorization_code", "refresh_token"],
                response_types=["code"], token_endpoint_auth_method="none", scope="internal"),
            storage=FileTokenStorage(self.token_path), redirect_handler=redirect, callback_handler=_wait_for_callback,
        )

    async def connect(self) -> None:
        """The MCP client's streams live in a task group that must be entered and exited by the
        same task, so one long-lived task owns the connection; any task may send requests."""
        async with self._lock:
            if self._session:
                return
            ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
            self._stop = asyncio.Event()
            self._runner = asyncio.create_task(self._run(ready, self._stop), name="robinhood-mcp")
            await ready

    async def _run(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        try:
            async with AsyncExitStack() as stack:
                http = await stack.enter_async_context(create_mcp_http_client(auth=self._auth()))
                streams = await stack.enter_async_context(streamable_http_client(self.url, http_client=http))
                session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
                await session.initialize()
                self._session = session
                ready.set_result(session)
                await stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else BrokerError(f"connection aborted: {e!r}"))
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            self._session = None

    async def close(self) -> None:
        runner, self._runner = self._runner, None
        if runner:
            self._stop.set()
            await asyncio.gather(runner, return_exceptions=True)
        self._session = None

    async def list_tools(self) -> list[dict]:
        await self.connect()
        tools, cursor = [], None
        while True:  # the server may page its tool list
            res = await self._session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
            tools += [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in res.tools]
            cursor = res.next_cursor
            if not cursor:
                return tools

    async def call(self, tool: str, args: dict) -> Any:
        for attempt in (1, 2):
            try:
                await self.connect()
                res = await self._session.call_tool(tool, args)
                break
            except BrokerError:
                raise
            except Exception as e:  # dropped session: reconnect once
                await self.close()
                if attempt == 2:
                    raise BrokerError(f"{tool} failed: {type(e).__name__}: {e}") from e
        text = "\n".join(getattr(c, "text", "") for c in res.content if getattr(c, "text", None))
        if res.is_error:
            raise BrokerError(f"{tool}: {text[:500]}")
        if res.structured_content:
            return res.structured_content
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}


# ---------------------------------------------------------------------- the tools this adapter uses
# Discovered from the real server on 2026-09-23 (`ait robinhood discover`). `verify` pins a digest
# of these tools' input schemas; live mode refuses to start if the server's schemas change after.
USED_TOOLS = [
    "get_accounts", "get_portfolio", "get_equity_positions", "get_option_positions",
    "get_equity_quotes", "get_equity_historicals", "get_option_chains", "get_option_instruments", "get_option_quotes",
    "review_equity_order", "place_equity_order", "get_equity_orders", "cancel_equity_order",
    "place_option_order", "get_option_orders", "cancel_option_order",
]
INTERVALS = {"1m": "minute", "5m": "5minute", "10m": "10minute", "30m": "30minute", "1h": "hour"}
OPTION_LEVELS = ("option_level_2", "option_level_3")  # long calls/puts need level 2
QUOTE_BATCH, BARS_BATCH = 50, 10


def schema_digest(tools: list[dict]) -> str:
    """Fingerprint of the input schemas this adapter relies on; missing tools raise."""
    by_name = {t["name"]: t.get("input_schema") for t in tools}
    missing = [n for n in USED_TOOLS if n not in by_name]
    if missing:
        raise BrokerError(f"the Robinhood server no longer exposes {missing}")
    blob = json.dumps({n: by_name[n] for n in USED_TOOLS}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _ts(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # nanosecond timestamps
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def ref_uuid(client_order_id: str) -> str:
    """Robinhood's idempotency key must be a UUID; derive it so a retry re-sends the same key."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-trader/{client_order_id}"))


def to_tick(price: float, min_ticks: dict | None, side: str) -> float:
    """Option prices must sit on the contract's tick (e.g. 0.05 above $3, 0.01 below).
    Buys round up and sells round down, so the order stays at least as marketable."""
    mt = min_ticks or {}
    cutoff = _f(mt.get("cutoff_price"), 0)
    tick = _f(mt.get("above_tick") if cutoff and price >= cutoff else mt.get("below_tick"), 0.01) or 0.01
    n = price / tick
    n = ceil(n - 1e-9) if side == "buy" else floor(n + 1e-9)
    return round(max(n, 1) * tick, 4)


def near_the_money(rows: list[dict], spot: float, per_side: int = 15, band: float = 0.10) -> list[dict]:
    """Only strikes that can plausibly carry the delta the desk trades (about 0.3-0.7): within
    `band` of spot and at most `per_side` strikes above and below it per expiry and type.
    Keeps index ETFs with $1 strikes and daily expiries to a few quote calls."""
    if not spot:
        return []
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if abs(_f(r.get("strike_price")) / spot - 1) <= band:
            groups.setdefault((r.get("expiration_date"), r.get("type")), []).append(r)
    out = []
    for g in groups.values():
        below = sorted((r for r in g if _f(r.get("strike_price")) <= spot), key=lambda r: -_f(r.get("strike_price")))
        above = sorted((r for r in g if _f(r.get("strike_price")) > spot), key=lambda r: _f(r.get("strike_price")))
        out += below[:per_side] + above[:per_side]
    return out


# ---------------------------------------------------------------------- broker + data
_STATUS = {"filled": OrderStatus.FILLED, "partially_filled": OrderStatus.PARTIAL, "cancelled": OrderStatus.CANCELLED,
           "canceled": OrderStatus.CANCELLED, "pending_cancelled": OrderStatus.OPEN, "rejected": OrderStatus.REJECTED,
           "failed": OrderStatus.REJECTED, "voided": OrderStatus.REJECTED, "queued": OrderStatus.OPEN,
           "confirmed": OrderStatus.OPEN, "unconfirmed": OrderStatus.PENDING, "new": OrderStatus.OPEN,
           "pending": OrderStatus.PENDING, "open": OrderStatus.OPEN}


class RobinhoodBroker:
    """Broker + MarketData over Robinhood's agentic MCP tools. Only the one account the
    server marks agentic_allowed is ever used; anything else is refused."""

    name = "robinhood"

    def __init__(self, mcp: RobinhoodMCP):
        self.mcp = mcp
        self._account: dict | None = None
        self._options: dict[str, dict] = {}          # instrument key -> option instrument row
        self._by_option_id: dict[str, Instrument] = {}
        self._order_kind: dict[str, str] = {}        # broker order id -> "equity" | "option"

    async def _data(self, tool: str, args: dict) -> Any:
        raw = await self.mcp.call(tool, args)
        return raw.get("data", raw) if isinstance(raw, dict) else raw

    # --- account
    async def agentic_account(self) -> dict:
        if self._account is None:
            rows = (await self._data("get_accounts", {})).get("accounts") or []
            ok = [a for a in rows if a.get("agentic_allowed") and a.get("state", "active") == "active"
                  and not a.get("deactivated")]
            if len(ok) != 1:
                raise BrokerError(f"expected exactly one agentic_allowed Robinhood account, found {len(ok)}")
            self._account = ok[0]
        return self._account

    async def _acct(self) -> str:
        return (await self.agentic_account())["account_number"]

    async def options_allowed(self) -> bool:
        return (await self.agentic_account()).get("option_level") in OPTION_LEVELS

    async def get_account(self) -> Account:
        d = await self._data("get_portfolio", {"account_number": await self._acct()})
        bp = d.get("buying_power")
        return Account(equity=_f(d.get("total_value")), cash=_f(d.get("cash")),
                       buying_power=_f(bp.get("buying_power") if isinstance(bp, dict) else bp))

    async def _pages(self, tool: str, args: dict, key: str) -> list[dict]:
        rows, cursor = [], None
        for _ in range(20):
            d = await self._data(tool, {**args, **({"cursor": cursor} if cursor else {})})
            rows += d.get(key) or []
            nxt = d.get("next_cursor") or d.get("next")
            cursor = (parse_qs(urlparse(nxt).query).get("cursor", [None])[0] if nxt and "://" in str(nxt) else nxt)
            if not cursor:
                break
        return rows

    async def get_positions(self) -> list[BrokerPosition]:
        acct = await self._acct()
        out = []
        for row in await self._pages("get_equity_positions", {"account_number": acct}, "positions"):
            qty = _f(row.get("quantity"))
            if qty:
                sym = row.get("symbol") or row.get("instrument_symbol")
                out.append(BrokerPosition(instrument=Instrument(symbol=sym), qty=int(qty),
                                          avg_price=_f(row.get("average_buy_price") or row.get("average_cost"))))
        opts = await self._pages("get_option_positions", {"account_number": acct, "nonzero": True}, "positions")
        for row in opts:
            qty = _f(row.get("quantity"))
            if not qty:
                continue
            oid = row.get("option_id") or str(row.get("option", "")).rstrip("/").rsplit("/", 1)[-1]
            inst = await self._instrument_for_id(oid)
            short = str(row.get("type", "long")).lower() == "short"
            mult = _f(row.get("trade_value_multiplier"), 100) or 100
            out.append(BrokerPosition(instrument=inst, qty=-int(qty) if short else int(qty),  # a short never matches the ledger
                                      avg_price=_f(row.get("average_price")) / mult))
        return out

    # --- market data
    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for i in range(0, len(symbols), QUOTE_BATCH):
            d = await self._data("get_equity_quotes", {"symbols": symbols[i:i + QUOTE_BATCH]})
            for row in d.get("results") or []:
                q = row.get("quote") or row
                sym = q.get("symbol")
                if not sym:
                    continue
                ts = max(filter(None, [_ts(q.get("venue_bid_time")), _ts(q.get("venue_ask_time")),
                                       _ts(q.get("venue_last_trade_time"))]), default=None)
                out[sym] = Quote(instrument=Instrument(symbol=sym), bid=_f(q.get("bid_price")), ask=_f(q.get("ask_price")),
                                 last=_f(q.get("last_trade_price")), **({"ts": ts} if ts else {}))
        return out

    async def get_quote(self, instrument: Instrument) -> Quote:
        if not instrument.is_option:
            q = (await self.get_quotes([instrument.symbol])).get(instrument.symbol)
            if q is None:
                raise BrokerError(f"no quote for {instrument.symbol}")
            return q
        row = await self._option_row(instrument)
        [c] = await self._quote_options([row]) or [None]
        if c is None:
            raise BrokerError(f"no quote for {instrument.key}")
        return Quote(instrument=instrument, bid=c.bid, ask=c.ask, last=c.last)

    async def get_bars(self, symbols: list[str], interval: str, lookback: int) -> dict[str, list[Bar]]:
        if interval not in INTERVALS:
            raise BrokerError(f"interval {interval!r} has no Robinhood equivalent")
        # enough calendar days to cover `lookback` regular-session bars across a weekend + holiday
        start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        batches = [symbols[i:i + BARS_BATCH] for i in range(0, len(symbols), BARS_BATCH)]
        replies = await asyncio.gather(*(self._data("get_equity_historicals", {
            "symbols": b, "start_time": start, "interval": INTERVALS[interval], "bounds": "regular"}) for b in batches))
        out: dict[str, list[Bar]] = {}
        for d in replies:
            for series in d.get("results") or []:
                bars = [Bar(ts=_ts(b["begins_at"]), open=_f(b.get("open_price")), high=_f(b.get("high_price")),
                            low=_f(b.get("low_price")), close=_f(b.get("close_price")), volume=_f(b.get("volume")))
                        for b in series.get("bars") or [] if not b.get("interpolated") and _ts(b.get("begins_at"))]
                out[series.get("symbol")] = bars[-lookback:]
        return out

    # --- catalysts (news + earnings), read-only context for the agents
    async def _earnings_by_symbol(self) -> dict[str, dict]:
        """One market-wide calendar call per day: reports from 3 days ago to 10 days ahead."""
        today = date.today()
        if getattr(self, "_earnings_day", None) != today:
            d = await self._data("get_earnings_calendar", {"start_date": (today - timedelta(days=3)).isoformat(), "days": 14})
            by: dict[str, dict] = {}
            for r in d.get("results") or []:
                rep = r.get("report") or {}
                if not r.get("symbol") or not rep.get("date"):
                    continue
                days = (date.fromisoformat(rep["date"]) - today).days
                cur = by.get(r["symbol"])
                if cur is None or abs(days) < abs(cur["days_from_today"]):
                    eps = r.get("eps") or {}
                    by[r["symbol"]] = {"date": rep["date"], "timing": rep.get("timing"), "days_from_today": days,
                                       "eps_estimate": _f(eps.get("estimate"), None) if eps.get("estimate") else None,
                                       "eps_actual": _f(eps.get("actual"), None) if eps.get("actual") else None}
            self._earnings, self._earnings_day = by, today
        return self._earnings

    async def get_catalysts(self, symbol: str, max_headlines: int = 6, max_age_hours: float = 36) -> dict:
        news, earnings = await asyncio.gather(self._data("get_equity_news", {"symbol": symbol, "limit": 10}),
                                              self._earnings_by_symbol())
        now = datetime.now(timezone.utc)
        heads = []
        for a in news.get("articles") or []:
            ts = _ts(a.get("published_at"))
            age = (now - ts).total_seconds() / 60 if ts else None
            if age is not None and age > max_age_hours * 60:
                continue
            heads.append({"title": a.get("title"), "publisher": a.get("publisher"),
                          "age_minutes": round(age) if age is not None else None,
                          "preview": (a.get("preview_text") or "")[:240]})
        out: dict = {"headlines": heads[:max_headlines]}
        if symbol in earnings:
            out["earnings"] = earnings[symbol]
        return out

    # --- options
    async def _chain(self, symbol: str) -> dict | None:
        chains = (await self._data("get_option_chains", {"underlying_symbol": symbol})).get("chains") or []
        # the standard chain: this symbol, 100x, no cash component (adjusted chains after corporate actions have one)
        std = [c for c in chains if c.get("symbol") == symbol and c.get("can_open_position")
               and not c.get("cash_component") and _f(c.get("trade_value_multiplier")) == 100]
        return std[0] if std else None

    def _remember(self, row: dict) -> Instrument:
        right = Right.CALL if str(row.get("type")).lower() == "call" else Right.PUT
        inst = Instrument(symbol=row.get("chain_symbol"), right=right, strike=_f(row.get("strike_price")),
                          expiry=date.fromisoformat(str(row.get("expiration_date"))[:10]))
        self._options[inst.key] = row
        self._by_option_id[row["id"]] = inst
        return inst

    async def _instrument_for_id(self, option_id: str) -> Instrument:
        if option_id not in self._by_option_id:
            rows = await self._pages("get_option_instruments", {"ids": option_id}, "instruments")
            if not rows:
                raise BrokerError(f"unknown option instrument {option_id}")
            self._remember(rows[0])
        return self._by_option_id[option_id]

    async def _option_row(self, inst: Instrument) -> dict:
        if inst.key not in self._options:
            rows = await self._pages("get_option_instruments", {
                "chain_symbol": inst.symbol, "expiration_dates": inst.expiry.isoformat(),
                "strike_price": f"{inst.strike:.4f}", "type": inst.right.value}, "instruments")
            if not rows:
                raise BrokerError(f"no Robinhood option instrument for {inst.key}")
            self._remember(rows[0])
        return self._options[inst.key]

    async def _quote_options(self, rows: list[dict]) -> list[OptionContract]:
        out = []
        for i in range(0, len(rows), QUOTE_BATCH):
            batch = {r["id"]: r for r in rows[i:i + QUOTE_BATCH]}
            d = await self._data("get_option_quotes", {"instrument_ids": list(batch)})
            for res in d.get("results") or []:
                q = res.get("quote") or res
                row = batch.get(q.get("instrument_id"))
                if not row:
                    continue
                out.append(OptionContract(
                    instrument=self._remember(row), bid=_f(q.get("bid_price")), ask=_f(q.get("ask_price")),
                    last=_f(q.get("last_trade_price") or q.get("mark_price")),
                    delta=_f(q.get("delta"), None) if q.get("delta") is not None else None,
                    iv=_f(q.get("implied_volatility"), None) if q.get("implied_volatility") is not None else None,
                    open_interest=int(_f(q.get("open_interest"))), volume=int(_f(q.get("volume")))))
        return out

    async def get_option_chain(self, symbol: str, min_dte: int, max_dte: int) -> list[OptionContract]:
        chain = await self._chain(symbol)
        if not chain:
            return []
        today = date.today()
        exps = sorted(e for e in chain.get("expiration_dates") or []
                      if min_dte <= (date.fromisoformat(e) - today).days <= max_dte)[:4]
        if not exps:
            return []
        spot = (await self.get_quote(Instrument(symbol=symbol))).mid
        rows = await self._pages("get_option_instruments", {
            "chain_id": chain["id"], "expiration_dates": ",".join(exps), "state": "active", "tradability": "tradable"},
            "instruments")
        return await self._quote_options(near_the_money(rows, spot))

    # --- orders
    def _result(self, o: dict, fallback_id: str = "") -> OrderResult:
        state = str(o.get("state", "")).lower()
        filled = _f(o.get("cumulative_quantity") or o.get("processed_quantity") or o.get("filled_quantity"))
        execs = [e for leg in (o.get("legs") or [o]) for e in (leg.get("executions") or [])]
        if execs and sum(_f(e.get("quantity")) for e in execs):
            avg = sum(_f(e.get("price")) * _f(e.get("quantity")) for e in execs) / sum(_f(e.get("quantity")) for e in execs)
        else:
            avg = _f(o.get("average_price")) or (_f(o.get("price")) if filled else 0.0)
        return OrderResult(broker_order_id=str(o.get("id") or fallback_id), status=_STATUS.get(state, OrderStatus.OPEN),
                           filled_qty=int(filled), avg_fill_price=avg,
                           message=str(o.get("reject_reason") or o.get("cancel_reason") or o.get("message") or ""), raw=o)

    @staticmethod
    def _order_of(d: Any) -> dict:
        if isinstance(d, dict):
            for k in ("order", "orders"):
                v = d.get(k)
                if isinstance(v, dict):
                    return v
                if isinstance(v, list) and v:
                    return v[0]
            return d
        return {}

    async def equity_order_args(self, req: OrderRequest) -> dict:
        return {"account_number": await self._acct(), "symbol": req.instrument.symbol, "side": req.side.value,
                "type": "limit", "quantity": str(int(req.qty)), "limit_price": f"{req.limit_price:.2f}",
                "time_in_force": "gfd", "market_hours": "regular_hours"}

    async def place_order(self, req: OrderRequest) -> OrderResult:
        i = req.instrument
        if i.is_option:
            row = await self._option_row(i)
            price = to_tick(req.limit_price, row.get("min_ticks"), req.side.value)
            d = await self._data("place_option_order", {
                "account_number": await self._acct(), "quantity": str(int(req.qty)), "type": "limit",
                "price": f"{price:.2f}", "time_in_force": "gfd", "market_hours": "regular_hours",
                "ref_id": ref_uuid(req.client_order_id),
                "legs": [{"option_id": row["id"], "side": req.side.value,
                          "position_effect": "open" if req.opens_position else "close"}]})
            kind = "option"
        else:
            d = await self._data("place_equity_order", {**await self.equity_order_args(req), "ref_id": ref_uuid(req.client_order_id)})
            kind = "equity"
        res = self._result(self._order_of(d))
        if not res.broker_order_id:
            raise BrokerError(f"order reply had no id: {str(d)[:300]}")
        self._order_kind[res.broker_order_id] = kind
        return res

    async def get_order(self, broker_order_id: str) -> OrderResult:
        acct = await self._acct()
        kinds = [self._order_kind[broker_order_id]] if broker_order_id in self._order_kind else ["equity", "option"]
        for kind in kinds:
            d = await self._data(f"get_{kind}_orders", {"account_number": acct, "order_id": broker_order_id})
            rows = d.get("orders") or []
            if rows:
                self._order_kind[broker_order_id] = kind
                return self._result(rows[0], broker_order_id)
        raise BrokerError(f"order {broker_order_id} not found in the agentic account")

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        current = await self.get_order(broker_order_id)  # also learns whether it is an equity or option order
        if current.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return current
        kind = self._order_kind[broker_order_id]
        await self._data(f"cancel_{kind}_order", {"account_number": await self._acct(), "order_id": broker_order_id})
        return await self.get_order(broker_order_id)
