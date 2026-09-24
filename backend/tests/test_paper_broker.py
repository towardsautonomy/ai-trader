import pytest

from app.broker.base import BrokerError
from app.broker.paper import PaperBroker
from app.core.types import Instrument, OrderReason, OrderRequest, OrderStatus, Side
from tests.fakes import EXPIRY, ScriptedData, frozen_cfg

CFG = frozen_cfg()
AAA = Instrument(symbol="AAA")


def order(side=Side.BUY, qty=10, limit=101.0, inst=AAA, oid="o1"):
    return OrderRequest(client_order_id=oid, instrument=inst, side=side, qty=qty, limit_price=limit,
                        reason=OrderReason.ENTRY, opens_position=side == Side.BUY)


@pytest.fixture
async def broker(db):
    return PaperBroker(ScriptedData({"AAA": 100.0}), db, CFG.paper)


async def test_marketable_buy_fills_at_the_ask_plus_slippage(broker):
    r = await broker.place_order(order())
    assert r.status == OrderStatus.FILLED and r.filled_qty == 10
    assert r.avg_fill_price == 100.04   # ask + slippage (100.030002), rounded up to the cent: pessimistic, never at mid
    acct = await broker.get_account()
    assert acct.cash == pytest.approx(100_000 - 10 * r.avg_fill_price)
    assert [(p.instrument.key, p.qty) for p in await broker.get_positions()] == [("AAA", 10)]


async def test_passive_limit_rests_then_fills_when_the_market_comes_to_it(broker):
    r = await broker.place_order(order(limit=99.0))
    assert r.status == OrderStatus.OPEN
    assert (await broker.get_order("o1")).status == OrderStatus.OPEN
    broker.data.set("AAA", 98.5)
    assert (await broker.get_order("o1")).status == OrderStatus.FILLED


async def test_cancel_removes_a_resting_order(broker):
    await broker.place_order(order(limit=99.0))
    assert (await broker.cancel_order("o1")).status == OrderStatus.CANCELLED
    with pytest.raises(BrokerError):
        await broker.get_order("o1")
    assert not await broker.get_positions()


async def test_sell_can_never_open_a_short(broker):
    r = await broker.place_order(order(side=Side.SELL, limit=99.0))
    assert r.status == OrderStatus.REJECTED and "exceeds held" in r.message
    await broker.place_order(order(qty=5))
    r = await broker.place_order(order(side=Side.SELL, qty=6, limit=99.0, oid="o2"))
    assert r.status == OrderStatus.REJECTED


async def test_round_trip_pnl_includes_spread_and_slippage(broker):
    await broker.place_order(order(qty=100))
    await broker.place_order(order(side=Side.SELL, qty=100, limit=99.0, oid="o2"))
    acct = await broker.get_account()
    assert acct.equity == acct.cash < 100_000          # flat price, but crossing the spread twice costs money
    assert not await broker.get_positions()


async def test_insufficient_cash_is_rejected(broker):
    r = await broker.place_order(order(qty=2000))
    assert r.status == OrderStatus.REJECTED and "insufficient cash" in r.message


async def test_bad_orders_are_rejected(broker):
    assert (await broker.place_order(order(qty=0))).status == OrderStatus.REJECTED
    assert (await broker.place_order(order(limit=0.0))).status == OrderStatus.REJECTED


async def test_options_use_the_contract_multiplier_and_pay_commission(broker):
    opt = Instrument(symbol="AAA", right="call", strike=100.0, expiry=EXPIRY)
    q = await broker.data.get_quote(opt)
    r = await broker.place_order(order(qty=2, limit=q.ask * 1.05, inst=opt))
    assert r.status == OrderStatus.FILLED
    acct = await broker.get_account()
    assert acct.cash == pytest.approx(100_000 - r.avg_fill_price * 2 * 100 - 0.06, abs=0.01)


async def test_state_survives_restart(broker, db):
    await broker.place_order(order(qty=7))
    reborn = PaperBroker(broker.data, db, CFG.paper)
    await reborn.load()
    assert reborn.cash == broker.cash
    assert [(p.instrument.key, p.qty) for p in await reborn.get_positions()] == [("AAA", 7)]


async def test_fills_land_on_exchange_ticks_against_us_and_within_the_limit(db):
    from app.broker.paper import PaperBroker
    from app.config import PaperCfg
    from app.core.types import Instrument, OrderReason, OrderRequest, Right, Side
    from tests.fakes import EXPIRY, ScriptedData
    data = ScriptedData({"IWM": 282.0})
    b = PaperBroker(data, db, PaperCfg(option_slippage_pct=1.0, slippage_bps=2))
    await b.load()
    put = Instrument(symbol="IWM", right=Right.PUT, strike=282.0, expiry=EXPIRY)
    q = await data.get_quote(put)
    buy = await b.place_order(OrderRequest(client_order_id="b", instrument=put, side=Side.BUY, qty=1, limit_price=round(q.ask * 1.05, 2),
                                           reason=OrderReason.ENTRY, opens_position=True))
    tick = 0.05 if buy.avg_fill_price >= 3 else 0.01
    assert abs(buy.avg_fill_price / tick - round(buy.avg_fill_price / tick)) < 1e-9 and buy.avg_fill_price >= q.ask * 1.01 - 1e-9
    sell = await b.place_order(OrderRequest(client_order_id="s", instrument=put, side=Side.SELL, qty=1, limit_price=0.01,
                                            reason=OrderReason.AGENT_EXIT, opens_position=False))
    assert abs(sell.avg_fill_price / tick - round(sell.avg_fill_price / tick)) < 1e-9 and sell.avg_fill_price <= q.bid * 0.99 + 1e-9
    stock = await b.place_order(OrderRequest(client_order_id="e", instrument=Instrument(symbol="IWM"), side=Side.BUY, qty=3,
                                             limit_price=283.0, reason=OrderReason.ENTRY, opens_position=True))
    assert round(stock.avg_fill_price, 2) == stock.avg_fill_price
