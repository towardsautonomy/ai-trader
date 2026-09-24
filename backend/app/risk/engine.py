"""The hard risk envelope between the agent swarm and the broker.

Trading judgment (what, when, how much conviction, reward:risk, when to stand
aside after losses) belongs to the agents and is taught by example in their
prompts. This module holds only the limits that must hold no matter what an
agent says: bounded loss per trade and per day, defined-risk structures only,
the trading window, pacing, and the kill switch. It is deterministic, has no
I/O, and evaluates every rule for every proposal (no short-circuit) so the
audit trail shows each result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.config import AppConfig
from app.core.types import OrderRequest, RiskCheck, RiskVerdict, Side, TradeProposal, Vehicle, utcnow


@dataclass
class OpenPos:
    symbol: str
    instrument_key: str
    notional: float


@dataclass
class PortfolioState:
    equity: float
    cash: float
    day_start_equity: float
    equity_high_water: float
    open_positions: list[OpenPos] = field(default_factory=list)
    trades_today: int = 0
    order_times_last_minute: int = 0
    day_trades_5d: int = 0
    llm_spend_today: float = 0.0

    @property
    def day_pnl_pct(self) -> float:
        return (self.equity / self.day_start_equity - 1) * 100 if self.day_start_equity > 0 else 0.0

    @property
    def drawdown_pct(self) -> float:
        return (1 - self.equity / self.equity_high_water) * 100 if self.equity_high_water > 0 else 0.0

    @property
    def exposure(self) -> float:
        return sum(p.notional for p in self.open_positions)


class RiskEngine:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def evaluate(self, p: TradeProposal, st: PortfolioState, *, kill_blocked: bool,
                 entries_allowed: bool, now: datetime | None = None) -> RiskVerdict:
        r, now = self.cfg.risk, now or utcnow()
        checks: list[RiskCheck] = []

        def check(rule: str, ok: bool, detail: str) -> None:
            checks.append(RiskCheck(rule=rule, passed=bool(ok), detail=detail))

        check("kill_switch", not kill_blocked, "armed" if not kill_blocked else "kill switch is engaged")
        check("trading_window", entries_allowed, "inside entry window" if entries_allowed else "outside entry window")

        # --- structural: only defined-risk, long-only exposure may ever be opened
        check("long_only", p.side == Side.BUY, f"opening side={p.side.value}; short stock and short options are never opened")
        if p.instrument.is_option:
            o = self.cfg.options
            strat = p.vehicle.value
            check("options_enabled", o.enabled, "options trading enabled" if o.enabled else "options disabled in config")
            check("defined_risk_strategy", strat in o.allowed_strategies and p.vehicle != Vehicle.SHARES,
                  f"strategy={strat}; allowed={o.allowed_strategies}")
        check("sane_order", p.qty > 0 and p.limit_price > 0 and 0 < p.stop_price < p.limit_price < p.target_price,
              f"qty={p.qty} limit={p.limit_price:.4f} stop={p.stop_price:.4f} target={p.target_price:.4f}")

        # --- per-trade sizing
        if not p.instrument.is_option:
            max_risk = st.equity * r.max_risk_per_trade_pct / 100
            check("risk_per_trade", p.risk_usd <= max_risk * 1.001, f"risk at stop ${p.risk_usd:.2f} vs cap ${max_risk:.2f}")
            stop_pct = (1 - p.stop_price / p.limit_price) * 100 if p.limit_price > 0 else 100.0
            check("max_stop_distance", stop_pct <= self.cfg.equity_exits.max_stop_pct * 1.001,
                  f"stop {stop_pct:.2f}% from entry vs max {self.cfg.equity_exits.max_stop_pct}%")
        if p.instrument.is_option:
            cap = st.equity * r.max_option_premium_pct / 100
            check("option_premium_cap", p.notional_usd <= cap * 1.001,
                  f"premium ${p.notional_usd:.2f} (the max possible loss) vs cap ${cap:.2f}")
        else:
            cap = st.equity * r.max_position_notional_pct / 100
            check("position_notional_cap", p.notional_usd <= cap * 1.001, f"notional ${p.notional_usd:.2f} vs cap ${cap:.2f}")
        check("buying_power", p.notional_usd <= st.cash, f"notional ${p.notional_usd:.2f} vs cash ${st.cash:.2f}")

        # --- portfolio
        exposure_cap = st.equity * r.max_total_exposure_pct / 100
        check("total_exposure", st.exposure + p.notional_usd <= exposure_cap,
              f"exposure ${st.exposure + p.notional_usd:.2f} vs cap ${exposure_cap:.2f}")
        check("max_open_positions", len(st.open_positions) < r.max_open_positions,
              f"{len(st.open_positions)} open vs max {r.max_open_positions}")
        in_symbol = sum(1 for x in st.open_positions if x.symbol == p.symbol)
        check("positions_per_symbol", in_symbol < r.max_positions_per_symbol,
              f"{in_symbol} open in {p.symbol} vs max {r.max_positions_per_symbol}")

        # --- loss limits (the watchdog also trips the kill switch on these)
        check("daily_loss_limit", st.day_pnl_pct > -r.max_daily_loss_pct,
              f"day P&L {st.day_pnl_pct:+.2f}% vs limit -{r.max_daily_loss_pct}%")
        check("drawdown_limit", st.drawdown_pct < r.max_drawdown_pct,
              f"drawdown {st.drawdown_pct:.2f}% vs limit {r.max_drawdown_pct}%")

        # --- pacing
        check("orders_per_minute", st.order_times_last_minute < r.max_orders_per_minute,
              f"{st.order_times_last_minute} orders in last 60s vs max {r.max_orders_per_minute}")
        check("trades_per_day", st.trades_today < r.max_trades_per_day, f"{st.trades_today} today vs max {r.max_trades_per_day}")
        if r.max_day_trades_5d > 0 and not self.cfg.session.hold_overnight:
            check("day_trade_limit", st.day_trades_5d < r.max_day_trades_5d,
                  f"{st.day_trades_5d} day trades in 5 sessions vs max {r.max_day_trades_5d}")

        # --- market quality and spend
        check("quote_fresh", p.quote_age_seconds <= r.max_quote_age_seconds,
              f"quote age {p.quote_age_seconds:.1f}s vs max {r.max_quote_age_seconds}s")
        max_spread = self.cfg.options.max_spread_pct if p.instrument.is_option else self.cfg.universe.max_spread_pct
        check("spread", p.spread_pct <= max_spread, f"spread {p.spread_pct:.3f}% vs max {max_spread}%")
        check("llm_budget", st.llm_spend_today < r.max_llm_spend_per_day_usd,
              f"LLM spend ${st.llm_spend_today:.2f} vs cap ${r.max_llm_spend_per_day_usd:.2f}")

        return RiskVerdict(approved=all(c.passed for c in checks), checks=checks)


def validate_exit_order(req: OrderRequest, held_qty: int) -> RiskCheck:
    """Exits bypass entry rules (they must always be possible, even when halted)
    but may only ever reduce a position we hold."""
    ok = req.side == Side.SELL and not req.opens_position and 0 < req.qty <= held_qty
    return RiskCheck(rule="exit_reduces_only", passed=ok,
                     detail=f"sell {req.qty} of {held_qty} held" if ok else
                     f"refused: side={req.side.value} qty={req.qty} held={held_qty} opens={req.opens_position}")
