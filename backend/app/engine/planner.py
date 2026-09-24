"""Turn the portfolio manager's plan into a concrete order, inside the envelope.

The agent decides; this module does arithmetic. Where a plan reaches outside
the hard limits it is pulled back in and the clamp is recorded on the proposal,
so the audit trail shows any difference between what the agent asked for and
what was sent. A plan that cannot be made safe is discarded with a reason.
"""

from __future__ import annotations

from datetime import date
from math import ceil, floor

from app.config import AppConfig
from app.core.types import (
    Candidate, Instrument, OptionContract, PMDecision, Quote, Right, Side, TradeProposal, Vehicle,
)


class Unbuildable(Exception):
    pass


def option_limit(ask: float, cfg: AppConfig) -> float:
    """Entry limit for an option: a little above the ask so a marketable order actually fills."""
    return ceil(ask * (1 + cfg.execution.option_entry_offset_pct / 100) * 100 - 1e-9) / 100


def contract_cost(ask: float, cfg: AppConfig) -> float:
    """Worst-case dollars for one contract. Sizing and affordability always use this."""
    return option_limit(ask, cfg) * 100


DEEP_OI_MULTIPLE = 5


def build_shortlist(chain: list[OptionContract], cfg: AppConfig, today: date | None = None,
                    max_premium_usd: float | None = None) -> list[OptionContract]:
    """Liquid, affordable contracts near the target delta, half calls and half puts.
    This is a tradability filter (can we get in and out at a fair price, within the
    premium cap?), not a trade selector: the portfolio manager picks from the list."""
    o, today = cfg.options, today or date.today()
    liquid = [
        c for c in chain
        if c.bid > 0 and (max_premium_usd is None or contract_cost(c.ask, cfg) <= max_premium_usd) and c.ask > c.bid * 0.999 and c.spread_pct <= o.max_spread_pct
        and c.open_interest >= o.min_open_interest
        # volume restarts at zero every morning: deep open interest is liquidity too
        and (c.volume >= o.min_volume or c.open_interest >= DEEP_OI_MULTIPLE * o.min_open_interest)
        and o.min_dte <= (c.instrument.expiry - today).days <= o.max_dte
        and (c.delta is None or abs(abs(c.delta) - o.target_delta) <= o.delta_tolerance)
    ]
    out: list[OptionContract] = []
    for right in (Right.CALL, Right.PUT):
        side = [c for c in liquid if c.instrument.right == right]
        side.sort(key=lambda c: (abs(abs(c.delta if c.delta is not None else o.target_delta) - o.target_delta), c.spread_pct))
        out.extend(side[: max(1, o.shortlist_size // 2)])
    return out


def _round_px(px: float) -> float:
    return round(px, 2)


def build_proposal(pm: PMDecision, c: Candidate, shortlist: list[OptionContract], *, equity: float,
                   cash: float, cfg: AppConfig, option_quote: Quote | None = None) -> TradeProposal:
    if pm.action != "enter":
        raise Unbuildable("PM did not choose to enter")
    if pm.vehicle is None or pm.direction not in ("long", "short"):
        raise Unbuildable(f"plan is missing vehicle or direction ({pm.vehicle}, {pm.direction!r})")
    if pm.size_fraction <= 0:
        raise Unbuildable("size_fraction is zero")

    clamps: list[str] = []
    und = c.quote.mid
    long = pm.direction == "long"
    expected = {Vehicle.SHARES: True, Vehicle.LONG_CALL: True, Vehicle.LONG_PUT: False}[pm.vehicle]
    if long != expected:
        raise Unbuildable(f"{pm.vehicle.value} cannot express a {pm.direction} thesis (no short stock, no short options)")

    # Thesis levels on the underlying must bracket the current price the right way round.
    lo, hi = (pm.stop_price, pm.target_price) if long else (pm.target_price, pm.stop_price)
    if not 0 < lo < und < hi:
        raise Unbuildable(f"stop {pm.stop_price} / target {pm.target_price} do not bracket price {und:.2f} for a {pm.direction} thesis")

    if pm.vehicle == Vehicle.SHARES:
        ex = cfg.equity_exits
        entry = _round_px(c.quote.ask * (1 + cfg.execution.entry_limit_offset_bps / 10_000))
        stop, target = pm.stop_price, pm.target_price
        floor_stop = entry * (1 - ex.max_stop_pct / 100)
        if stop < floor_stop:
            clamps.append(f"stop {stop:.2f} is beyond the {ex.max_stop_pct}% maximum; pulled to {floor_stop:.2f}")
            stop = floor_stop
        ceiling = entry * (1 + ex.take_profit_ceiling_pct / 100)
        if target > ceiling:
            clamps.append(f"target {target:.2f} is beyond the {ex.take_profit_ceiling_pct}% ceiling; pulled to {ceiling:.2f}")
            target = ceiling
        # Round toward the entry so rounding can never push a level outside the envelope.
        stop, target = ceil(stop * 100 - 1e-9) / 100, floor(target * 100 + 1e-9) / 100
        if not stop < entry < target:
            raise Unbuildable(f"after pricing the entry at {entry}, stop {stop} / target {target} no longer bracket it")

        budget = equity * cfg.risk.max_risk_per_trade_pct / 100 * pm.size_fraction
        qty = floor(budget / (entry - stop))
        max_notional = min(equity * cfg.risk.max_position_notional_pct / 100, cash)
        if qty * entry > max_notional:
            clamps.append(f"size cut from {qty} to {floor(max_notional / entry)} shares by the position/cash cap")
            qty = floor(max_notional / entry)
        if qty < 1:
            raise Unbuildable("risk budget is too small for a single share at this stop distance")
        return TradeProposal(
            symbol=c.symbol, instrument=Instrument(symbol=c.symbol), vehicle=pm.vehicle, side=Side.BUY, qty=qty,
            limit_price=entry, stop_price=stop, target_price=target, risk_usd=round(qty * (entry - stop), 2),
            notional_usd=round(qty * entry, 2), quote_age_seconds=c.quote.age_seconds, spread_pct=c.quote.spread_pct,
            max_hold_minutes=_hold(pm.max_hold_minutes, ex.max_hold_minutes, clamps), clamps=clamps,
        )

    # --- long call / long put
    o = cfg.options
    if pm.contract_index is None or not 0 <= pm.contract_index < len(shortlist):
        raise Unbuildable(f"contract_index {pm.contract_index} is not in the shortlist of {len(shortlist)}")
    contract = shortlist[pm.contract_index]
    want = Right.CALL if pm.vehicle == Vehicle.LONG_CALL else Right.PUT
    if contract.instrument.right != want:
        raise Unbuildable(f"contract {pm.contract_index} is a {contract.instrument.right.value}, but the vehicle is {pm.vehicle.value}")
    ask = option_quote.ask if option_quote else contract.ask
    bid = option_quote.bid if option_quote else contract.bid
    if ask <= 0 or bid <= 0:
        raise Unbuildable("option has no two-sided market")
    entry = option_limit(ask, cfg)
    premium_cap = min(equity * cfg.risk.max_option_premium_pct / 100 * pm.size_fraction, cash)
    qty = floor(premium_cap / (entry * 100))
    if qty < 1:
        raise Unbuildable(f"size_fraction {pm.size_fraction:g} gives a premium budget of ${premium_cap:.0f}, which does not cover "
                          f"one contract at ${entry * 100:.0f} (limit {entry} = ask {ask} + {cfg.execution.option_entry_offset_pct}%)")
    mid = (bid + ask) / 2
    return TradeProposal(
        symbol=c.symbol, instrument=contract.instrument, vehicle=pm.vehicle, side=Side.BUY, qty=qty,
        limit_price=entry,
        stop_price=_round_px(entry * (1 - o.stop_loss_pct / 100)),            # hard premium stop
        target_price=_round_px(entry * (1 + o.take_profit_ceiling_pct / 100)),  # hard profit ceiling
        und_stop=pm.stop_price, und_target=pm.target_price,
        risk_usd=round(qty * entry * 100, 2), notional_usd=round(qty * entry * 100, 2),
        quote_age_seconds=(option_quote or c.quote).age_seconds,
        spread_pct=(ask - bid) / mid * 100 if mid > 0 else 100.0,
        max_hold_minutes=_hold(pm.max_hold_minutes, o.max_hold_minutes, clamps), clamps=clamps,
    )


def _hold(requested: int, cap: int, clamps: list[str]) -> int:
    if requested <= 0:
        return cap
    if requested > cap:
        clamps.append(f"max hold {requested}m is beyond the {cap}m cap")
        return cap
    return requested
