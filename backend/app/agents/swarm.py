"""The agent swarm: builds each agent's payload, calls the model, and parses
the reply defensively. No trading judgment lives here: that is in the prompts."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from app.agents.llm import LLM, LLMResult
from app.config import AgentsCfg
from app.core.types import Candidate, Opinion, OptionContract, PMDecision, PositionAction, Stance

PROMPT_DIR = Path(__file__).parent / "prompts"
AGENTS = ["scout", "regime", "momentum", "mean_reversion", "volatility", "skeptic",
          "portfolio_manager", "position_manager", "reviewer"]


@lru_cache
def system_prompt(agent: str) -> str:
    return (PROMPT_DIR / f"{agent}.md").read_text() + "\n\n" + (PROMPT_DIR / "_glossary.md").read_text()


@lru_cache
def example_sentences(agent: str) -> tuple[str, ...]:
    """Free-text fields of every worked example in an agent's prompt."""
    import json
    import re
    out = []
    for block in re.findall(r"```json\n(.*?)\n```", (PROMPT_DIR / f"{agent}.md").read_text(), re.S):
        try:
            ex = json.loads(block)
        except json.JSONDecodeError:
            continue
        for key in ("thesis", "summary", "reasoning", "lesson", "market_note"):
            if isinstance(ex.get(key), str) and len(ex[key]) > 30:
                out.append(ex[key])
        for pick in ex.get("picks") or []:
            if isinstance(pick, dict) and len(str(pick.get("reason", ""))) > 30:
                out.append(str(pick["reason"]))
    return tuple(out)


def copied_example(agent: str, text: str) -> str | None:
    """Weak models answer by pasting a worked example instead of reading the data. Returns the
    example the text copies, or None. Near-verbatim only: sharing vocabulary is expected."""
    from difflib import SequenceMatcher
    text = (text or "").strip()
    if len(text) < 30:
        return None
    for ex in example_sentences(agent):
        if text in ex or ex in text or SequenceMatcher(None, text.lower(), ex.lower()).ratio() >= 0.9:
            return ex
    return None


def candidate_payload(c: Candidate, regime: dict, minutes_to_close: float | None) -> dict:
    return {
        "symbol": c.symbol, "price": round(c.quote.mid, 2), "signals": c.signals, "recent": c.recent,
        "hint_setups": c.hint_setups, "scout_reason": c.scout_reason, "regime": regime,
        "minutes_to_close": None if minutes_to_close is None else round(minutes_to_close),
        **({"catalysts": c.catalysts} if c.catalysts else {}),
    }


def shortlist_payload(shortlist: list[OptionContract], entry_offset_pct: float = 1.5) -> list[dict]:
    from datetime import date
    return [{
        # what one contract will actually cost at our entry limit: the unit the size budget must cover
        "cost_per_contract_usd": round(c.ask * (1 + entry_offset_pct / 100) * 100),
        "index": i, "type": c.instrument.right.value, "strike": c.instrument.strike,
        "expiry": c.instrument.expiry.isoformat(), "dte": (c.instrument.expiry - date.today()).days,
        "bid": c.bid, "ask": c.ask, "mid": round(c.mid, 2), "spread_pct": round(c.spread_pct, 2),
        "delta": None if c.delta is None else round(c.delta, 2),
        "iv": None if c.iv is None else round(c.iv, 2), "open_interest": c.open_interest, "volume": c.volume,
    } for i, c in enumerate(shortlist)]


def scout_items(raw) -> list[tuple[str, str]]:
    """(symbol, reason) pairs from the shapes models actually produce:
    [{"symbol","reason"}], {"AAPL": "reason"}, ["AAPL", ...], or a single string."""
    out: list[tuple[str, str]] = []
    if isinstance(raw, dict):
        raw = [{"symbol": k, "reason": v if isinstance(v, str) else str((v or {}).get("reason", ""))} for k, v in raw.items()]
    elif isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            out.append((str(item.get("symbol") or item.get("ticker") or "").upper().strip(), str(item.get("reason", ""))))
        elif isinstance(item, str):
            out.append((item.upper().strip(), ""))
    return out


def normalise_plan(d: dict) -> dict:
    """Tolerate the harmless variations models produce without weakening what matters:
    a skip carries only action/confidence/summary; empty or 'none' vehicles mean None;
    a lone string for key_risks becomes a one-item list. Wrong vehicles stay wrong."""
    d = {k: v for k, v in d.items() if v is not None}
    action = str(d.get("action", "")).lower().strip()
    if action in ("skip", "hold", "pass", "wait", "no_trade", "no trade", "none", "stand_aside", "stand aside"):
        return {"action": "skip", "confidence": d.get("confidence", 0.0), "summary": str(d.get("summary", ""))}
    d["action"] = str(d.get("action", "")).lower().strip()
    if str(d.get("vehicle", "")).lower().strip() in ("", "none", "null", "n/a"):
        d.pop("vehicle", None)
    if isinstance(d.get("key_risks"), str):
        d["key_risks"] = [d["key_risks"]] if d["key_risks"].strip() else []
    if str(d.get("contract_index", "")).strip() in ("", "none", "null", "n/a"):
        d.pop("contract_index", None)
    return d


def _opinion(agent: str, r: LLMResult) -> Opinion:
    base = dict(agent=agent, model=r.model, latency_ms=r.latency_ms, tokens_in=r.tokens_in,
                tokens_out=r.tokens_out, cost_usd=r.cost_usd, raw=r.raw)
    if not r.ok:
        return Opinion(**base, stance=Stance.NEUTRAL, confidence=0.0, thesis="(no opinion: model call failed)",
                       error=r.error or "no data")
    d = r.data
    try:
        stance = Stance(str(d.get("stance", "")).lower())
    except ValueError:
        return Opinion(**base, stance=Stance.NEUTRAL, confidence=0.0, thesis=str(d.get("thesis", "")),
                       error=f"unrecognised stance {d.get('stance')!r}")
    try:
        conf = min(1.0, max(0.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    evidence = d.get("evidence") or []
    thesis = str(d.get("thesis", ""))
    if copied_example(agent, thesis):
        return Opinion(**base, stance=Stance.NEUTRAL, confidence=0.0, thesis=thesis,
                       error="discarded: the thesis is a copy of a worked example from the prompt, not a reading of the data")
    return Opinion(**base, stance=stance, confidence=conf, thesis=thesis,
                   evidence=[str(e) for e in evidence] if isinstance(evidence, list) else [str(evidence)],
                   invalidation=str(d.get("invalidation", "")))


def opinion_brief(o: Opinion) -> dict:
    return {"agent": o.agent, "stance": o.stance.value, "confidence": o.confidence, "thesis": o.thesis,
            "evidence": o.evidence, "invalidation": o.invalidation, **({"error": o.error} if o.error else {})}


class Swarm:
    def __init__(self, llm: LLM, cfg: AgentsCfg):
        self.llm, self.cfg = llm, cfg

    async def _call(self, agent: str, payload: dict, **ids) -> LLMResult:
        return await self.llm.complete_json(agent, self.cfg.model_for(agent), system_prompt(agent), payload, **ids)

    async def scout(self, table: list[Candidate], *, open_symbols: list[str], recently_closed: list[dict],
                    regime: dict, max_picks: int, minutes_to_close: float | None) -> tuple[list[Candidate], str, LLMResult]:
        rows = [{"symbol": c.symbol, **c.signals, "hint_setups": c.hint_setups} for c in table]
        r = await self._call("scout", {
            "table": rows, "open_positions": open_symbols, "recently_closed": recently_closed,
            "regime": regime, "max_picks": max_picks,
            "minutes_to_close": None if minutes_to_close is None else round(minutes_to_close),
        })
        if not r.ok:
            return [], "", r
        by_symbol = {c.symbol: c for c in table}
        picks, seen = [], set()
        for sym, reason in scout_items(r.data.get("picks")):
            if copied_example("scout", reason):
                r.error = f"discarded pick {sym}: its reason is a copy of a worked example from the prompt"
                continue
            if sym in by_symbol and sym not in seen:  # ignore hallucinated or duplicate symbols
                seen.add(sym)
                picks.append(by_symbol[sym].model_copy(update={"scout_reason": reason}))
        return picks[:max_picks], str(r.data.get("market_note", "")), r

    async def regime(self, indices: list[Candidate], breadth: dict) -> tuple[dict, LLMResult]:
        r = await self._call("regime", {
            "indices": [{"symbol": c.symbol, **c.signals, "recent": c.recent} for c in indices], "breadth": breadth,
        })
        if not r.ok:
            return {"label": "unknown", "bias": "neutral", "confidence": 0.0,
                    "summary": f"regime unavailable: {r.error}", "what_works": ""}, r
        d = r.data
        return {"label": str(d.get("label", "unknown")), "bias": str(d.get("bias", "neutral")),
                "confidence": d.get("confidence", 0.0), "summary": str(d.get("summary", "")),
                "what_works": str(d.get("what_works", ""))}, r

    async def deliberate(self, c: Candidate, *, regime: dict, shortlist: list[OptionContract],
                         pm_context: dict, minutes_to_close: float | None,
                         decision_id: str) -> tuple[list[Opinion], PMDecision, str]:
        """Specialists in parallel -> skeptic -> portfolio manager. Returns
        (opinions, decision, pm_error)."""
        base = candidate_payload(c, regime, minutes_to_close)
        options = shortlist_payload(shortlist)

        async def specialist(name: str) -> Opinion:
            payload = {**base, "option_shortlist": options} if name == "volatility" else base
            return _opinion(name, await self._call(name, payload, decision_id=decision_id))

        opinions = list(await asyncio.gather(*(specialist(n) for n in self.cfg.specialists)))
        skeptic_payload = {**base, "opinions": [opinion_brief(o) for o in opinions],
                           "recently_closed": pm_context.get("portfolio", {}).get("recent_closed", [])}
        opinions.append(_opinion("skeptic", await self._call("skeptic", skeptic_payload, decision_id=decision_id)))

        r = await self._call("portfolio_manager", {
            **base, "opinions": [opinion_brief(o) for o in opinions], "option_shortlist": options, **pm_context,
        }, decision_id=decision_id)
        if not r.ok:
            return opinions, PMDecision(action="skip", summary=f"PM unavailable: {r.error}"), r.error or "no data"
        try:
            pm = PMDecision.model_validate(normalise_plan(r.data))
        except ValidationError as e:
            return opinions, PMDecision(action="skip", summary="PM reply failed validation"), str(e)
        if pm.action not in ("enter", "skip"):
            return opinions, PMDecision(action="skip", summary=pm.summary), f"unrecognised action {pm.action!r}"
        if pm.action == "enter" and copied_example("portfolio_manager", pm.summary):
            return opinions, PMDecision(action="skip", summary=pm.summary), "discarded: the plan's summary is a copy of a worked example from the prompt"
        return opinions, pm, ""

    async def review_position(self, payload: dict, position_id: str) -> tuple[PositionAction, LLMResult]:
        r = await self._call("position_manager", payload, position_id=position_id)
        if not r.ok:
            return PositionAction(action="hold", reasoning=f"review unavailable: {r.error}"), r
        try:
            act = PositionAction.model_validate({k: v for k, v in r.data.items() if v is not None})
        except ValidationError as e:
            r.error = f"invalid review: {e}"
            return PositionAction(action="hold", reasoning="review reply failed validation"), r
        if act.action != "hold" and copied_example("position_manager", act.reasoning):
            r.error = "discarded: the review's reasoning is a copy of a worked example from the prompt"
            return PositionAction(action="hold", reasoning=act.reasoning), r
        if act.action not in ("hold", "adjust", "exit"):
            r.error = f"unrecognised action {act.action!r}"
            return PositionAction(action="hold", reasoning=act.reasoning), r
        return act, r

    async def write_lesson(self, payload: dict, position_id: str) -> tuple[dict | None, LLMResult]:
        r = await self._call("reviewer", payload, position_id=position_id)
        if not r.ok or not r.data.get("lesson"):
            return None, r
        d = r.data
        tags = d.get("tags") if isinstance(d.get("tags"), list) else []
        return {"tags": [str(t) for t in tags][:8], "situation": str(d.get("situation", "")),
                "what_happened": str(d.get("what_happened", "")), "lesson": str(d["lesson"])}, r
