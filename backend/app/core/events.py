"""Audit log + live feed. Everything the engine does goes through emit()."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.types import utcnow
from app.db.models import Event
from app.db.session import Database

log = logging.getLogger("ait")
_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}


class EventLog:
    def __init__(self, db: Database):
        self.db = db
        self._subscribers: set[asyncio.Queue[dict]] = set()

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    async def emit(
        self,
        level: str,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
        decision_id: str | None = None,
        position_id: str | None = None,
    ) -> None:
        log.log(_LEVELS.get(level, logging.INFO), "[%s] %s", kind, message)
        ev = Event(
            ts=utcnow(), level=level, kind=kind, message=message, data=data or {},
            decision_id=decision_id, position_id=position_id,
        )
        async with self.db.session() as s:
            s.add(ev)
            await s.commit()
        payload = event_to_dict(ev)
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # slow consumer; it can refetch from /api/events
                pass


def event_to_dict(ev: Event) -> dict:
    return {
        "id": ev.id, "ts": ev.ts.isoformat(), "level": ev.level, "kind": ev.kind,
        "message": ev.message, "data": ev.data,
        "decision_id": ev.decision_id, "position_id": ev.position_id,
    }
