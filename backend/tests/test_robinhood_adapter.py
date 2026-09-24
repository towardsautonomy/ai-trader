"""The Robinhood adapter against a fake MCP session that replays reply shapes captured
from the real server on 2026-09-23 (values made up). `ait robinhood verify` covers the live server."""

import uuid
from datetime import date, timedelta

import pytest

from app.broker.base import BrokerError
from app.broker.robinhood_mcp import (
    USED_TOOLS, RobinhoodBroker, near_the_money, ref_uuid, schema_digest, to_tick,
)
from app.core.types import Instrument, OrderReason, OrderRequest, OrderStatus, Right, Side

EXP = (date.today() + timedelta(days=10)).isoformat()
TICKS = {"above_tick": "0.05", "below_tick": "0.01", "cutoff_price": "3.00"}


def wrap(d):
    return {"data": d, "guide": "text for AI agents"}


def acct(num, agentic, level="option_level_2", **kw):
    return {"account_number": num, "type": "margin", "agentic_allowed": agentic, "option_level": level, "state": "active",
            "deactivated": False, **kw}


def opt_row(oid, strike, typ="call", exp=EXP):
    return {"id": oid, "chain_id": "ch1", "chain_symbol": "NVDA", "expiration_date": exp, "strike_price": f"{strike:.4f}",
            "type": typ, "state": "active", "tradability": "tradable", "min_ticks": TICKS}


class FakeMCP:
    """Answers by tool name; a callable reply gets the args."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    async def call(self, tool, args):
        self.calls.append((tool, args))
        r = self.replies[tool]
        r = r(args) if callable(r) else r
        if isinstance(r, Exception):
            raise r
        return wrap(r)

    def args(self, tool):
        return [a for t, a in self.calls if t == tool]


ACCOUNTS = {"accounts": [acct("MAIN1234", False), acct("AGNT0179", True, type="limited_margin")]}


def broker(replies):
    mcp = FakeMCP({"get_accounts": ACCOUNTS, **replies})
    return RobinhoodBroker(mcp), mcp


async def test_only_the_single_agentic_account_is_ever_used():
    b, mcp = broker({"get_portfolio": {"total_value": "5000.50", "cash": "1200", "buying_power": {"buying_power": "1100.0000"}}})
    a = await b.get_account()
    assert (a.equity, a.cash, a.buying_power) == (5000.5, 1200.0, 1100.0)
    assert mcp.args("get_portfolio") == [{"account_number": "AGNT0179"}] and await b.options_allowed()


@pytest.mark.parametrize("accounts", [[acct("A", False)], [acct("A", True), acct("B", True)], [acct("A", True, deactivated=True)]])
async def test_zero_or_several_agentic_accounts_is_refused(accounts):
    b, _ = broker({"get_accounts": {"accounts": accounts}})
    with pytest.raises(BrokerError, match="exactly one"):
        await b.get_account()


async def test_options_level_below_two_disables_options():
    b, _ = broker({"get_accounts": {"accounts": [acct("A", True, level="option_level_0")]}})
    assert not await b.options_allowed()


async def test_quotes_are_parsed_with_venue_time_and_batched():
    row = lambda s: {"quote": {"symbol": s, "bid_price": "400.10", "ask_price": "400.12", "last_trade_price": "400.11",
                               "venue_bid_time": "2026-09-23T07:04:49.419123456Z", "venue_ask_time": "2026-09-23T07:04:49.5Z"},
                     "close": {"symbol": s, "price": "399"}}
    b, mcp = broker({"get_equity_quotes": lambda a: {"results": [row(s) for s in a["symbols"]]}})
    syms = [f"S{i}" for i in range(60)]
    q = await b.get_quotes(syms)
    assert len(q) == 60 and (q["S0"].bid, q["S0"].ask, q["S0"].last) == (400.10, 400.12, 400.11)
    assert q["S0"].ts.isoformat().startswith("2026-09-23T07:04:49.5") and len(mcp.args("get_equity_quotes")) == 2


async def test_bars_use_robinhood_intervals_skip_interpolated_and_keep_lookback():
    bar = lambda i, interp=False: {"begins_at": f"2026-09-22T{13 + i // 12:02d}:{(i % 12) * 5:02d}:00Z", "open_price": "1",
                                   "high_price": "2", "low_price": "0.5", "close_price": str(1 + i), "volume": 100,
                                   "interpolated": interp}
    b, mcp = broker({"get_equity_historicals": lambda a: {"results": [
        {"symbol": s, "interval": a["interval"], "bars": [bar(i, interp=(i == 5)) for i in range(20)]} for s in a["symbols"]]}})
    out = await b.get_bars([f"S{i}" for i in range(12)], "5m", 10)
    calls = mcp.args("get_equity_historicals")
    assert len(calls) == 2 and calls[0]["interval"] == "5minute" and calls[0]["bounds"] == "regular" and "start_time" in calls[0]
    assert len(out) == 12 and len(out["S0"]) == 10 and out["S0"][-1].close == 20.0
    full = await b.get_bars(["S0"], "5m", 100)
    assert len(full["S0"]) == 19  # the interpolated bar is dropped
    with pytest.raises(BrokerError, match="no Robinhood equivalent"):
        await b.get_bars(["S0"], "7m", 10)


def test_near_the_money_keeps_a_band_of_strikes_per_expiry_and_type():
    rows = [opt_row(f"{t}{k}", k, t) for t in ("call", "put") for k in range(80, 121)]
    near = near_the_money(rows, 100.0, per_side=3, band=0.10)
    calls = sorted(float(r["strike_price"]) for r in near if r["type"] == "call")
    assert calls == [98, 99, 100, 101, 102, 103] and len(near) == 12
    assert near_the_money(rows, 0.0) == []


async def test_option_chain_picks_the_standard_chain_and_quotes_near_strikes():
    chains = {"chains": [
        {"id": "adj", "symbol": "NVDA", "can_open_position": True, "cash_component": "12.5", "trade_value_multiplier": "100.0000", "expiration_dates": [EXP]},
        {"id": "ch1", "symbol": "NVDA", "can_open_position": True, "cash_component": None, "trade_value_multiplier": "100.0000",
         "expiration_dates": [(date.today() + timedelta(days=1)).isoformat(), EXP]}]}
    instruments = {"instruments": [opt_row("c100", 100), opt_row("c150", 150), opt_row("p95", 95, "put")]}
    quote = lambda a: {"results": [{"quote": {"instrument_id": i, "bid_price": "2.00", "ask_price": "2.10", "delta": "0.52",
                                              "implied_volatility": "0.45", "open_interest": 900, "volume": 40}} for i in a["instrument_ids"]]}
    b, mcp = broker({"get_option_chains": chains, "get_option_instruments": instruments, "get_option_quotes": quote,
                     "get_equity_quotes": {"results": [{"quote": {"symbol": "NVDA", "bid_price": "99.9", "ask_price": "100.1", "last_trade_price": "100"}}]}})
    chain = await b.get_option_chain("NVDA", 5, 35)
    assert mcp.args("get_option_instruments")[0]["chain_id"] == "ch1" and mcp.args("get_option_instruments")[0]["expiration_dates"] == EXP
    assert sorted(c.instrument.key for c in chain) == sorted(["NVDA " + date.fromisoformat(EXP).strftime("%y%m%d") + "C100",
                                                            "NVDA " + date.fromisoformat(EXP).strftime("%y%m%d") + "P95"])
    c = chain[0]
    assert (c.bid, c.ask, c.delta, c.iv, c.open_interest) == (2.0, 2.1, 0.52, 0.45, 900)


def test_option_prices_land_on_the_tick_in_the_marketable_direction():
    assert to_tick(3.42, TICKS, "buy") == 3.45 and to_tick(3.42, TICKS, "sell") == 3.40
    assert to_tick(1.234, TICKS, "buy") == 1.24 and to_tick(1.234, TICKS, "sell") == 1.23
    assert to_tick(2.50, TICKS, "buy") == 2.50 and to_tick(0.004, TICKS, "sell") == 0.01


def test_idempotency_key_is_a_stable_uuid():
    assert ref_uuid("abc") == ref_uuid("abc") != ref_uuid("abd") and uuid.UUID(ref_uuid("abc"))


def req(inst, side=Side.BUY, qty=2, px=3.42, opens=True):
    return OrderRequest(client_order_id="cid1", instrument=inst, side=side, qty=qty, limit_price=px,
                        reason=OrderReason.ENTRY if opens else OrderReason.STOP_LOSS, opens_position=opens)


async def test_equity_order_is_a_limit_with_string_numbers_and_a_ref_id():
    order = {"id": "o1", "state": "confirmed", "cumulative_quantity": "0", "average_price": None}
    b, mcp = broker({"place_equity_order": order, "get_equity_orders": {"orders": [{**order, "state": "filled", "cumulative_quantity": "2.00000", "average_price": "100.02"}]}})
    res = await b.place_order(req(Instrument(symbol="NVDA"), px=100.0))
    sent = mcp.args("place_equity_order")[0]
    assert sent == {"account_number": "AGNT0179", "symbol": "NVDA", "side": "buy", "type": "limit", "quantity": "2",
                    "limit_price": "100.00", "time_in_force": "gfd", "market_hours": "regular_hours", "ref_id": ref_uuid("cid1")}
    assert res.broker_order_id == "o1" and res.status == OrderStatus.OPEN
    done = await b.get_order("o1")
    assert (done.status, done.filled_qty, done.avg_fill_price) == (OrderStatus.FILLED, 2, 100.02)
    assert mcp.args("get_equity_orders") == [{"account_number": "AGNT0179", "order_id": "o1"}]


async def test_option_order_is_one_leg_on_the_tick_with_the_right_position_effect():
    inst = Instrument(symbol="NVDA", right=Right.CALL, strike=100.0, expiry=date.fromisoformat(EXP))
    placed = {"order": {"id": "oo1", "state": "queued", "processed_quantity": "0"}}
    filled = {"orders": [{"id": "oo1", "state": "filled", "processed_quantity": "2", "price": "3.45",
                          "legs": [{"executions": [{"price": "3.40", "quantity": "1"}, {"price": "3.44", "quantity": "1"}]}]}]}
    b, mcp = broker({"get_option_instruments": {"instruments": [opt_row("c100", 100)]}, "place_option_order": placed,
                     "get_option_orders": filled, "get_equity_orders": {"orders": []}})
    await b.place_order(req(inst))
    sent = mcp.args("place_option_order")[0]
    assert sent["price"] == "3.45" and sent["quantity"] == "2" and sent["type"] == "limit" and sent["ref_id"] == ref_uuid("cid1")
    assert sent["legs"] == [{"option_id": "c100", "side": "buy", "position_effect": "open"}]
    await b.place_order(req(inst, side=Side.SELL, px=3.42, opens=False))
    sent = mcp.args("place_option_order")[1]
    assert sent["price"] == "3.40" and sent["legs"][0] == {"option_id": "c100", "side": "sell", "position_effect": "close"}
    done = await b.get_order("oo1")
    assert (done.status, done.filled_qty, done.avg_fill_price) == (OrderStatus.FILLED, 2, pytest.approx(3.42))
    assert mcp.args("get_equity_orders") == []  # it knew this was an option order


async def test_after_a_restart_an_unknown_order_id_is_looked_up_in_both_books():
    b, mcp = broker({"get_equity_orders": {"orders": []}, "get_option_orders": {"orders": [{"id": "x", "state": "cancelled"}]},
                     "cancel_option_order": {}})
    assert (await b.get_order("x")).status == OrderStatus.CANCELLED
    assert (await b.cancel_order("x")).status == OrderStatus.CANCELLED and mcp.args("cancel_option_order") == []  # already final
    b2, _ = broker({"get_equity_orders": {"orders": []}, "get_option_orders": {"orders": []}})
    with pytest.raises(BrokerError, match="not found"):
        await b2.get_order("nope")


async def test_cancel_goes_to_the_right_book():
    states = iter(["confirmed", "cancelled"])
    b, mcp = broker({"get_equity_orders": lambda a: {"orders": [{"id": "e1", "state": next(states)}]}, "cancel_equity_order": {}})
    assert (await b.cancel_order("e1")).status == OrderStatus.CANCELLED
    assert mcp.args("cancel_equity_order") == [{"account_number": "AGNT0179", "order_id": "e1"}]


async def test_positions_merge_equity_and_options_and_a_short_never_matches():
    b, mcp = broker({
        "get_equity_positions": {"positions": [{"symbol": "AAPL", "quantity": "3.00000000", "average_buy_price": "190.5"},
                                               {"symbol": "OLD", "quantity": "0.00000000"}]},
        "get_option_positions": {"positions": [{"option_id": "c100", "quantity": "2.0000", "type": "long", "average_price": "345.0000"},
                                               {"option_id": "c100", "quantity": "1.0000", "type": "short", "average_price": "100"}]},
        "get_option_instruments": {"instruments": [opt_row("c100", 100)]}})
    pos = await b.get_positions()
    assert [(p.instrument.key, p.qty) for p in pos][:1] == [("AAPL", 3)]
    long_ = pos[1]
    assert long_.instrument.right == Right.CALL and long_.qty == 2 and long_.avg_price == pytest.approx(3.45)
    assert pos[2].qty == -1  # a short would fail reconciliation and halt the engine
    assert mcp.args("get_option_positions")[0]["nonzero"] is True


def test_schema_digest_pins_the_used_tools_and_notices_changes():
    tools = [{"name": n, "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}} for n in USED_TOOLS]
    d = schema_digest(tools + [{"name": "unrelated", "input_schema": {}}])
    assert d == schema_digest(tools)  # other tools do not matter
    tools[0]["input_schema"]["properties"]["b"] = {"type": "string"}
    assert schema_digest(tools) != d
    with pytest.raises(BrokerError, match="no longer exposes"):
        schema_digest(tools[1:])


def test_callback_parsing_accepts_full_url_query_or_request_line_path():
    from app.broker.robinhood_mcp import _parse_callback
    for s in ["http://127.0.0.1:8765/callback?code=abc&state=xyz", "/callback?code=abc&state=xyz", "code=abc&state=xyz", "?code=abc&state=xyz"]:
        r = _parse_callback(s)
        assert (r.code, r.state) == ("abc", "xyz"), s
    assert _parse_callback("http://127.0.0.1:8765/callback?error=denied") is None
    assert _parse_callback("just some text") is None


# ---- the MCP session layer, driven with the mcp library's real result types (2.x snake_case fields)
class _Session:
    def __init__(self, pages=None, result=None):
        self.pages, self.result, self.seen = list(pages or []), result, []

    async def list_tools(self, *, params=None):
        self.seen.append(params.cursor if params else None)
        return self.pages.pop(0)

    async def call_tool(self, name, args):
        return self.result


def _mcp_with(tmp_path, session):
    from app.broker.robinhood_mcp import RobinhoodMCP
    m = RobinhoodMCP("https://example.invalid/mcp", tmp_path)
    m._session = session
    return m


async def test_list_tools_reads_real_tool_objects_across_pages(tmp_path):
    from mcp import types
    tool = lambda n: types.Tool(name=n, description=f"{n} tool", input_schema={"type": "object"})
    s = _Session(pages=[types.ListToolsResult(tools=[tool("get_quotes")], next_cursor="p2"),
                        types.ListToolsResult(tools=[tool("place_order")])])
    tools = await _mcp_with(tmp_path, s).list_tools()
    assert [t["name"] for t in tools] == ["get_quotes", "place_order"]
    assert tools[0]["input_schema"] == {"type": "object"} and s.seen == [None, "p2"]


async def test_call_prefers_structured_content_and_raises_on_tool_error(tmp_path):
    from mcp import types
    from app.broker.base import BrokerError
    ok = types.CallToolResult(content=[types.TextContent(type="text", text="ignored")], structured_content={"bid": 1.5})
    assert await _mcp_with(tmp_path, _Session(result=ok)).call("get_quotes", {}) == {"bid": 1.5}
    text = types.CallToolResult(content=[types.TextContent(type="text", text='{"bid": 2}')])
    assert await _mcp_with(tmp_path, _Session(result=text)).call("get_quotes", {}) == {"bid": 2}
    bad = types.CallToolResult(content=[types.TextContent(type="text", text="market closed")], is_error=True)
    with pytest.raises(BrokerError, match="market closed"):
        await _mcp_with(tmp_path, _Session(result=bad)).call("place_order", {})


async def test_catalysts_merge_recent_headlines_and_the_nearest_earnings_report():
    from datetime import datetime, timezone
    fresh = datetime.now(timezone.utc).isoformat()
    old = "2020-01-01T00:00:00-04:00"
    today = date.today()
    cal = {"results": [
        {"symbol": "NVDA", "eps": {"estimate": "1.36", "actual": None}, "report": {"date": (today + timedelta(days=9)).isoformat(), "timing": "pm"}},
        {"symbol": "NVDA", "eps": {"estimate": "1.20", "actual": "1.31"}, "report": {"date": (today - timedelta(days=1)).isoformat(), "timing": "am"}},
        {"symbol": "AAPL", "eps": {}, "report": {"date": today.isoformat(), "timing": "am"}}]}
    b, mcp = broker({"get_equity_news": {"symbol": "NVDA", "articles": [
        {"title": "Fresh", "publisher": "Benzinga", "published_at": fresh, "preview_text": "x" * 400},
        {"title": "Stale", "publisher": "X", "published_at": old}]}, "get_earnings_calendar": cal})
    c = await b.get_catalysts("NVDA")
    assert [h["title"] for h in c["headlines"]] == ["Fresh"] and len(c["headlines"][0]["preview"]) == 240
    assert c["earnings"]["days_from_today"] == -1 and c["earnings"]["eps_actual"] == 1.31
    await b.get_catalysts("AAPL")
    assert len(mcp.args("get_earnings_calendar")) == 1  # one calendar call per day
    assert "earnings" not in await b.get_catalysts("TSLA")


async def test_catalysts_reach_the_agents_and_a_failure_does_not_block_deliberation(rig):
    from tests.fakes import scout_pick
    calls = []

    async def catalysts(symbol):
        calls.append(symbol)
        return {"headlines": [{"title": "Guidance raised", "age_minutes": 12}]}
    rig.engine.data.get_catalysts = catalysts
    await rig.tick(entry=True)
    momentum_payloads = [p for a, p in rig.llm.calls if a == "momentum"]
    assert calls and momentum_payloads and momentum_payloads[0]["catalysts"]["headlines"][0]["title"] == "Guidance raised"

    async def boom(symbol):
        raise ConnectionError("news down")
    rig.engine.data.get_catalysts = boom
    rig.llm.calls.clear()
    await rig.tick(entry=True)
    assert any(a == "portfolio_manager" for a, _ in rig.llm.calls)
