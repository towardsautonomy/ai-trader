"""LLM access. Two providers behind one OpenAI-compatible client:

  OpenRouter   hosted models, priced per token (cost comes back in the response)
  local        anything serving the OpenAI chat API on this machine: Ollama, vLLM,
               llama.cpp, LM Studio. Chosen per agent by the `local/` model prefix,
               e.g. `local/llama3.2` -> model `llama3.2` at AIT_LOCAL_LLM_URL.

Every call is persisted (prompt, response, cost) so any decision can be audited
down to exactly what the agent saw."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

import httpx
from sqlalchemy import func, select

from app.core.types import utcnow
from app.db.models import LLMCall
from app.db.session import Database

OPENROUTER_URL = "https://openrouter.ai/api/v1"
LOCAL_PREFIX = "local/"


@dataclass
class LLMResult:
    data: dict | None
    model: str
    raw: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.error


class LLM(Protocol):
    async def complete_json(self, agent: str, model: str, system: str, payload: dict, *,
                            decision_id: str | None = None, position_id: str | None = None) -> LLMResult: ...


def extract_json(text: str) -> dict | None:
    """Models sometimes wrap JSON in prose, code fences or a <think> block; take the outermost object."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def output_keys(system: str) -> list[str]:
    """Top-level keys of the output template: the last fenced JSON block of the prompt."""
    blocks = re.findall(r"```json\n(.*?)\n```", system, re.S)
    if not blocks:
        return []
    try:
        return list(json.loads(blocks[-1]).keys())
    except json.JSONDecodeError:
        return []


def frame_user_message(system: str, payload: dict) -> str:
    """A bare JSON object as the user turn invites models (especially in JSON mode) to
    continue or echo it. Wrap it in prose and restate exactly what to reply with."""
    keys = output_keys(system)
    want = f" with exactly these keys: {', '.join(keys)}" if keys else ""
    return ("The situation, as data:\n" + json.dumps(payload, default=str, separators=(",", ":"))
            + f"\n\nDo not repeat the data. Reply with your verdict as one JSON object{want}.")


def echoes_input(data: dict, payload: dict) -> bool:
    """True when the reply is (mostly) the input handed back."""
    if not payload:
        return False
    shared = set(data) & set(payload)
    return len(shared) >= max(2, len(payload) // 2)


@dataclass
class SpendTracker:
    db: Database
    _cache: tuple[float, float] = field(default=(0.0, 0.0))

    async def today(self) -> float:
        now = time.monotonic()
        if now - self._cache[0] < 5:
            return self._cache[1]
        since = utcnow() - timedelta(hours=18)  # covers one trading day in any US timezone
        async with self.db.session() as s:
            total = (await s.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                     .where(LLMCall.ts >= since))).scalar_one()
        self._cache = (now, float(total))
        return float(total)


class OpenAICompatLLM:
    """Chat-completions client for OpenRouter or a local server. `name` labels the
    provider in status; `tracks_cost` says whether the API reports dollars."""

    def __init__(self, name: str, base_url: str, api_key: str, db: Database, timeout: float = 40,
                 max_attempts: int = 2, tracks_cost: bool = True, reasoning_effort: str = ""):
        self.name, self.db, self.timeout, self.max_attempts, self.tracks_cost = name, db, timeout, max_attempts, tracks_cost
        self.reasoning_effort = reasoning_effort.strip().lower()
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._no_json_mode: set[str] = set()  # models whose server rejected response_format
        headers = {"X-Title": "ai-trader"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(timeout=timeout, headers=headers)

    @staticmethod
    def thinks(effort: str) -> bool:
        return effort not in ("", "none")

    @property
    def max_tokens(self) -> int:
        return self.budget(self.reasoning_effort)

    @staticmethod
    def budget(effort: str) -> int:
        # Thinking tokens count against max_tokens; a budget sized for the answer alone
        # leaves a thinking model with an empty reply.
        return 6000 if OpenAICompatLLM.thinks(effort) else 1200

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete_json(self, agent: str, model: str, system: str, payload: dict, *,
                            decision_id: str | None = None, position_id: str | None = None,
                            thinking: str | None = None) -> LLMResult:
        user = frame_user_message(system, payload)
        effort = self.reasoning_effort if thinking is None else thinking.strip().lower()
        # a thinking model writes thousands of tokens first; give it time to finish
        timeout = max(self.timeout, 360) if self.thinks(effort) else self.timeout
        body = {
            "model": model, "temperature": 0.2, "max_tokens": self.budget(effort),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if effort:
            body["reasoning_effort"] = effort
        if self.tracks_cost:
            body["usage"] = {"include": True}  # OpenRouter extension: cost in the response
        result = LLMResult(data=None, model=model)
        started = time.monotonic()
        for attempt in range(self.max_attempts):
            try:
                if model in self._no_json_mode:
                    body.pop("response_format", None)
                resp = await self._http.post(self._url, json=body, timeout=timeout)
                if resp.status_code == 400 and "reasoning_effort" in body:
                    # a server without thinking controls: carry on at its default
                    body.pop("reasoning_effort")
                    self.reasoning_effort = ""
                    result.error = "http 400 (retrying without reasoning_effort)"
                    continue
                if resp.status_code == 400 and "response_format" in body:
                    # Some servers reject JSON mode. The prompts already demand a single JSON
                    # object and extract_json tolerates wrapping, so carry on without it.
                    self._no_json_mode.add(model)
                    result.error = "http 400 (retrying without response_format)"
                    continue
                if resp.status_code >= 500 or resp.status_code == 429:
                    result.error = f"http {resp.status_code}"
                    continue
                resp.raise_for_status()
                j = resp.json()
                usage = j.get("usage") or {}
                result.tokens_in += int(usage.get("prompt_tokens") or 0)
                result.tokens_out += int(usage.get("completion_tokens") or 0)
                result.cost_usd += float(usage.get("cost") or 0.0) if self.tracks_cost else 0.0
                msg = j["choices"][0]["message"]
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
                # keep the model's reasoning in the trace; extract_json ignores <think> blocks
                result.raw = f"<think>\n{reasoning}\n</think>\n{content}" if reasoning else content
                result.data = extract_json(content)
                if result.data is None and not content and j["choices"][0].get("finish_reason") == "length":
                    result.error = "reply budget used up before an answer (thinking too long?)"
                    break  # the same request would run out again
                if result.data is None:
                    result.error = "response was not a JSON object"
                elif echoes_input(result.data, payload):
                    result.data, result.error = None, "response echoed the input instead of answering"
                else:
                    result.error = ""
                    break
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                result.error = f"{type(e).__name__}: {e}"
        result.latency_ms = int((time.monotonic() - started) * 1000)
        await log_call(self.db, agent, result, system + "\n\n" + user, decision_id, position_id)
        return result


def openrouter(api_key: str, db: Database, timeout: float = 40) -> OpenAICompatLLM:
    return OpenAICompatLLM("openrouter", OPENROUTER_URL, api_key, db, timeout=timeout, tracks_cost=True)


def local_llm(base_url: str, api_key: str, db: Database, timeout: float = 120, thinking: str = "") -> OpenAICompatLLM:
    """Local servers are slower and free: longer timeout, no cost accounting."""
    return OpenAICompatLLM("local", base_url, api_key, db, timeout=timeout, tracks_cost=False, reasoning_effort=thinking)


class RouterLLM:
    """Dispatch by model name: `local/<model>` goes to the local server, the rest to OpenRouter.
    A model with no provider configured returns a failed result (never a guess)."""

    def __init__(self, hosted: OpenAICompatLLM | None, local: OpenAICompatLLM | None, db: Database,
                 thinking: dict[str, str] | None = None):
        self.hosted, self.local, self.db = hosted, local, db
        self.thinking = thinking or {}  # per-agent effort; applied on the local route only

    @property
    def name(self) -> str:
        return "+".join(p.name for p in (self.hosted, self.local) if p)

    def provider_for(self, model: str) -> OpenAICompatLLM | None:
        return self.local if model.startswith(LOCAL_PREFIX) else self.hosted

    async def complete_json(self, agent: str, model: str, system: str, payload: dict, *,
                            decision_id: str | None = None, position_id: str | None = None) -> LLMResult:
        provider = self.provider_for(model)
        if provider is None:
            where = "AIT_LOCAL_LLM_URL" if model.startswith(LOCAL_PREFIX) else "AIT_OPENROUTER_API_KEY"
            r = LLMResult(data=None, model=model, error=f"no provider for {model!r}: set {where}")
            await log_call(self.db, agent, r, "", decision_id, position_id)
            return r
        thinking = self.thinking.get(agent) if provider is self.local else None
        return await provider.complete_json(agent, model.removeprefix(LOCAL_PREFIX), system, payload,
                                            decision_id=decision_id, position_id=position_id, thinking=thinking)

    async def aclose(self) -> None:
        for p in (self.hosted, self.local):
            if p:
                await p.aclose()


async def log_call(db: Database, agent: str, r: LLMResult, prompt: str,
                   decision_id: str | None, position_id: str | None) -> None:
    async with db.session() as s:
        s.add(LLMCall(agent=agent, model=r.model, decision_id=decision_id, position_id=position_id,
                      prompt=prompt, response=r.raw, latency_ms=r.latency_ms, tokens_in=r.tokens_in,
                      tokens_out=r.tokens_out, cost_usd=r.cost_usd, error=r.error))
        await s.commit()
