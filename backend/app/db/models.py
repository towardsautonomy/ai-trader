"""Persistence. Every action the engine takes is reconstructible from these tables:

position -> decision -> (agent_opinions, risk checks) -> orders -> events
"""

from __future__ import annotations

from datetime import datetime

from datetime import timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.core.types import utcnow


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo. Store UTC, and always hand back aware datetimes, so
    time arithmetic can never mix naive and aware values."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to a UTC column")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Decision(Base):
    """One swarm deliberation on one candidate, whether or not it traded."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    cycle_id: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    # deliberating -> skipped_by_pm | unbuildable | rejected_by_risk | executed | unfilled | failed
    status: Mapped[str] = mapped_column(String(24), index=True)
    candidate: Mapped[dict] = mapped_column(JSON)  # scanner signals snapshot
    regime: Mapped[dict] = mapped_column(JSON, default=dict)
    scout_reason: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # portfolio/track-record/lessons shown to the PM
    pm: Mapped[dict] = mapped_column(JSON, default=dict)  # PMDecision
    proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # TradeProposal
    risk: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # RiskVerdict
    outcome: Mapped[str] = mapped_column(Text, default="")  # one-line human summary

    opinions: Mapped[list[AgentOpinion]] = relationship(back_populates="decision")


class AgentOpinion(Base):
    __tablename__ = "agent_opinions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(12))
    confidence: Mapped[float] = mapped_column(Float)
    thesis: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    invalidation: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[str] = mapped_column(Text, default="")

    decision: Mapped[Decision] = relationship(back_populates="opinions")


class Position(Base):
    """A position the engine opened and manages, with its exit plan."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    instrument: Mapped[dict] = mapped_column(JSON)
    instrument_key: Mapped[str] = mapped_column(String(48))
    direction: Mapped[int] = mapped_column(Integer)  # thesis direction on the underlying
    qty: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Levels on the traded instrument. Set by the portfolio manager, moved by the
    # position-manager agent (tighten-only for stops), enforced by the fast exit loop.
    initial_stop: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    # Thesis levels on the underlying (options only; 0 for shares).
    und_stop: Mapped[float] = mapped_column(Float, default=0.0)
    und_target: Mapped[float] = mapped_column(Float, default=0.0)
    max_hold_minutes: Mapped[int] = mapped_column(Integer, default=0)
    thesis: Mapped[str] = mapped_column(Text, default="")
    invalidation: Mapped[str] = mapped_column(Text, default="")
    atr: Mapped[float] = mapped_column(Float, default=0.0)
    high_water: Mapped[float] = mapped_column(Float)  # best price seen since entry
    low_water: Mapped[float] = mapped_column(Float)   # worst price seen since entry
    last_price: Mapped[float] = mapped_column(Float)
    risk_usd: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open|closing|closed
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_ts: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(24), default="")
    exit_detail: Mapped[str] = mapped_column(Text, default="")
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)


class PositionReview(Base):
    """One position-manager agent review of one open trade."""

    __tablename__ = "position_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    model: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(12))
    reasoning: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    requested: Mapped[dict] = mapped_column(JSON, default=dict)  # what the agent asked for
    applied: Mapped[dict] = mapped_column(JSON, default=dict)    # what code allowed (tighten-only)
    context: Mapped[dict] = mapped_column(JSON, default=dict)    # price/P&L snapshot it saw
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")


class Lesson(Base):
    """Post-trade review. Lessons are fed back to the agents as worked examples,
    so the system's guidance grows from its own history rather than from rules."""

    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    vehicle: Mapped[str] = mapped_column(String(12))
    outcome_r: Mapped[float] = mapped_column(Float)  # realised P&L in units of initial risk
    tags: Mapped[list] = mapped_column(JSON, default=list)
    situation: Mapped[str] = mapped_column(Text)
    what_happened: Mapped[str] = mapped_column(Text)
    lesson: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64), default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class LLMCall(Base):
    """Every model call, for cost tracking and for auditing exactly what an agent saw."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    decision_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    position_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # client_order_id
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decision_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    position_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(8))
    broker_order_id: Mapped[str] = mapped_column(String(64), default="")
    instrument_key: Mapped[str] = mapped_column(String(48))
    instrument: Mapped[dict] = mapped_column(JSON)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), index=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Event(Base):
    """Append-only audit log; also the websocket feed."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(8))  # info|warn|error|critical
    kind: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    position_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(8))
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    day_pnl: Mapped[float] = mapped_column(Float)


class KV(Base):
    """Small durable state: kill switch, paper account, day-start equity."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


Index("ix_positions_mode_status", Position.mode, Position.status)
