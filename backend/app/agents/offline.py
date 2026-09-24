"""Offline stand-in for the LLM, used only when no OpenRouter key is configured.

It lets the whole pipeline run for demos and tests. Its "opinions" are crude
heuristics and are labelled as such everywhere (model = "offline-heuristic").
It is refused in live mode: real money is only ever traded on real model output.
"""

from __future__ import annotations

from app.agents.llm import LLMResult, log_call
from app.db.session import Database

MODEL = "offline-heuristic"


def _stance(score: float, threshold: float = 0.15) -> str:
    return "bullish" if score > threshold else "bearish" if score < -threshold else "neutral"


def _specialist(agent: str, p: dict) -> dict:
    s = p["signals"]
    if agent == "momentum":
        score = s["ema_trend_pct"] / max(s["atr_pct"], 1e-9) * 0.5 + max(s["breakout_up_atr"], 0) - max(s["breakout_dn_atr"], 0)
        why = f"trend {s['ema_trend_pct']:+.2f}%, breakout up {s['breakout_up_atr']:+.2f} / down {s['breakout_dn_atr']:+.2f} ATR, rvol {s['rvol']:.1f}"
    elif agent == "mean_reversion":
        score = -s["zscore20"] / 3 if abs(s["zscore20"]) > 1.8 else 0.0
        why = f"z-score {s['zscore20']:+.2f}, RSI {s['rsi14']:.0f}, VWAP dev {s['vwap_dev_pct']:+.2f}%"
    else:  # volatility
        score = s["roc12_pct"] / max(s["atr_pct"] * 3, 1e-9)
        n = len(p.get("option_shortlist") or [])
        why = f"{n} liquid contracts; ATR {s['atr_pct']:.2f}% per bar"
    return {"stance": _stance(score), "confidence": round(min(0.85, 0.45 + abs(score) * 0.25), 2),
            "thesis": f"[offline heuristic] {why}", "evidence": [why], "invalidation": "n/a (offline mode)"}


def _pm(p: dict) -> dict:
    votes = [(1 if o["stance"] == "bullish" else -1 if o["stance"] == "bearish" else 0) * o["confidence"]
             for o in p["opinions"] if o["agent"] != "skeptic"]
    net = sum(votes)
    if abs(net) < 0.9:
        return {"action": "skip", "confidence": 0.5, "summary": f"[offline heuristic] no agreement (net {net:+.2f})"}
    price, atr = p["price"], p["price"] * p["signals"]["atr_pct"] / 100
    long = net > 0
    out = {"action": "enter", "direction": "long" if long else "short", "size_fraction": 0.5,
           "stop_price": round(price - 2.0 * atr if long else price + 2.0 * atr, 2),
           "target_price": round(price + 4.0 * atr if long else price - 4.0 * atr, 2),
           "max_hold_minutes": 120, "confidence": round(min(0.8, 0.5 + abs(net) * 0.1), 2),
           "summary": f"[offline heuristic] specialists net {net:+.2f}", "key_risks": ["offline mode: not a real model decision"],
           "invalidation": "stop level"}
    want = "call" if long else "put"
    idx = next((c["index"] for c in p.get("option_shortlist") or [] if c["type"] == want), None)
    if not long and idx is None:
        return {"action": "skip", "confidence": 0.5, "summary": "[offline heuristic] bearish but no put available"}
    if idx is not None and (not long or abs(net) > 1.4):
        out.update(vehicle=f"long_{want}", contract_index=idx, size_fraction=1.0)
    else:
        out.update(vehicle="shares", contract_index=None)
    return out


def respond(agent: str, p: dict) -> dict:
    if agent == "scout":
        held = set(p.get("open_positions") or [])
        rows = [r for r in p["table"] if r["symbol"] not in held and r.get("hint_setups")]
        return {"picks": [{"symbol": r["symbol"], "reason": f"[offline heuristic] {', '.join(r['hint_setups'])}"}
                          for r in rows[: p["max_picks"]]], "market_note": "offline mode"}
    if agent == "regime":
        up = p["breadth"].get("pct_above_vwap", 50)
        bias = "bullish" if up > 60 else "bearish" if up < 40 else "neutral"
        return {"label": "offline", "bias": bias, "confidence": 0.4,
                "summary": f"[offline heuristic] {up:.0f}% of universe above VWAP", "what_works": ""}
    if agent in ("momentum", "mean_reversion", "volatility"):
        return _specialist(agent, p)
    if agent == "skeptic":
        return {"stance": "neutral", "confidence": 0.3, "thesis": "[offline heuristic] no objection generated",
                "evidence": [], "invalidation": "n/a"}
    if agent == "portfolio_manager":
        return _pm(p)
    if agent == "position_manager":
        pos = p["position"]
        if pos["pnl_r"] >= 1.0 and not pos["is_option"] and pos["stop"] < pos["entry"]:
            return {"action": "adjust", "new_stop": pos["entry"], "new_target": None, "confidence": 0.5,
                    "reasoning": "[offline heuristic] +1R reached: stop to entry"}
        return {"action": "hold", "new_stop": None, "new_target": None, "confidence": 0.5,
                "reasoning": "[offline heuristic] no change"}
    if agent == "reviewer":
        t = p["trade"]
        return {"tags": ["offline"], "situation": f"{t['symbol']} {t['vehicle']}",
                "what_happened": f"closed {t['exit_reason']} at {t['pnl_r']:+.2f}R",
                "lesson": "[offline heuristic] no lesson: reviewer needs a real model"}
    raise ValueError(f"unknown agent {agent!r}")


class OfflineLLM:
    def __init__(self, db: Database):
        self.db = db

    async def complete_json(self, agent: str, model: str, system: str, payload: dict, *,
                            decision_id: str | None = None, position_id: str | None = None) -> LLMResult:
        import json
        data = respond(agent, payload)
        r = LLMResult(data=data, model=MODEL, raw=json.dumps(data))
        await log_call(self.db, agent, r, json.dumps(payload, default=str), decision_id, position_id)
        return r
