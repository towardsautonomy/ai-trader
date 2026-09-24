"""Domain types shared by brokers, scanner, agents, risk and execution."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Right(StrEnum):
    CALL = "call"
    PUT = "put"


class OrderStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderReason(StrEnum):
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    PROFIT_CEILING = "profit_ceiling"
    THESIS_STOP = "thesis_stop"      # option closed because the underlying hit the thesis stop
    THESIS_TARGET = "thesis_target"
    AGENT_EXIT = "agent_exit"        # the position-manager agent chose to close
    TIME_STOP = "time_stop"
    DTE_EXIT = "dte_exit"
    EOD_FLATTEN = "eod_flatten"
    KILL_SWITCH = "kill_switch"
    MANUAL = "manual"


class Instrument(BaseModel):
    """An equity (symbol only) or a single option contract."""

    symbol: str
    right: Right | None = None
    strike: float | None = None
    expiry: date | None = None

    @property
    def is_option(self) -> bool:
        return self.right is not None

    @property
    def multiplier(self) -> int:
        return 100 if self.is_option else 1

    @property
    def key(self) -> str:
        if not self.is_option:
            return self.symbol
        return f"{self.symbol} {self.expiry:%y%m%d}{self.right.value[0].upper()}{self.strike:g}"

    def __hash__(self) -> int:
        return hash(self.key)


class Quote(BaseModel):
    instrument: Instrument
    bid: float
    ask: float
    last: float
    ts: datetime = Field(default_factory=utcnow)

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100 if self.mid > 0 else 100.0

    @property
    def age_seconds(self) -> float:
        return (utcnow() - self.ts).total_seconds()


class Bar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class OptionContract(BaseModel):
    instrument: Instrument
    bid: float
    ask: float
    last: float = 0.0
    delta: float | None = None
    iv: float | None = None
    open_interest: int = 0
    volume: int = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100 if self.mid > 0 else 100.0


class OrderRequest(BaseModel):
    client_order_id: str
    instrument: Instrument
    side: Side
    qty: int
    limit_price: float
    reason: OrderReason
    opens_position: bool  # True for entries; exits must never open exposure


class OrderResult(BaseModel):
    broker_order_id: str
    status: OrderStatus
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    message: str = ""
    raw: dict = Field(default_factory=dict)


class BrokerPosition(BaseModel):
    instrument: Instrument
    qty: int
    avg_price: float


class Account(BaseModel):
    equity: float
    cash: float
    buying_power: float


class Candidate(BaseModel):
    """One symbol the scout agent sent to the swarm, with its computed features."""

    symbol: str
    quote: Quote
    signals: dict[str, float]
    recent: dict = {}             # compact price path: closes, day open/high/low, relative volume
    atr: float
    hint_score: float = 0.0       # offline heuristic; a hint shown to agents, never a gate
    hint_setups: list[str] = []
    scout_reason: str = ""
    catalysts: dict = {}          # recent headlines and nearby earnings, when the data source has them


class Stance(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Opinion(BaseModel):
    agent: str
    model: str
    stance: Stance
    confidence: float = Field(ge=0, le=1)
    thesis: str
    evidence: list[str] = []
    invalidation: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str = ""
    raw: str = ""


class Vehicle(StrEnum):
    SHARES = "shares"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"


class PMDecision(BaseModel):
    """The portfolio manager's verdict: the whole trade plan is the agent's call.
    Code only clamps it to the risk envelope and records any clamping."""

    action: str = "skip"  # "enter" | "skip"
    direction: str = ""   # "long" | "short" thesis on the underlying
    vehicle: Vehicle | None = None
    contract_index: int | None = None  # into the option shortlist it was shown
    stop_price: float = 0.0            # on the underlying: where the thesis is wrong
    target_price: float = 0.0          # on the underlying
    size_fraction: float = Field(default=0.0, ge=0, le=1)  # of the max risk budget
    max_hold_minutes: int = 0
    confidence: float = Field(default=0.0, ge=0, le=1)
    summary: str = ""
    key_risks: list[str] = []
    invalidation: str = ""


class PositionAction(BaseModel):
    """The position-manager agent's call on one open trade. It may tighten a
    stop or move a target, never widen risk; code enforces that."""

    action: str = "hold"  # "hold" | "adjust" | "exit"
    new_stop: float | None = None
    new_target: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasoning: str = ""


class TradeProposal(BaseModel):
    symbol: str
    instrument: Instrument
    vehicle: Vehicle
    side: Side
    qty: int
    limit_price: float
    stop_price: float          # on the traded instrument (options: premium stop)
    target_price: float        # on the traded instrument
    und_stop: float = 0.0      # thesis levels on the underlying
    und_target: float = 0.0
    risk_usd: float            # shares: loss at stop; options: full premium (the true max loss)
    notional_usd: float
    quote_age_seconds: float
    spread_pct: float
    max_hold_minutes: int
    clamps: list[str] = []     # where code had to pull the agent's plan inside the envelope


class RiskCheck(BaseModel):
    rule: str
    passed: bool
    detail: str


class RiskVerdict(BaseModel):
    approved: bool
    checks: list[RiskCheck]

    @property
    def failures(self) -> list[RiskCheck]:
        return [c for c in self.checks if not c.passed]
