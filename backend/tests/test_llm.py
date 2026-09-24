import json

import httpx
import pytest
from sqlalchemy import select

from app.agents.llm import RouterLLM, SpendTracker, echoes_input, extract_json, frame_user_message, local_llm, openrouter
from app.db.models import LLMCall


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure! Here is my answer:\n{"a": {"b": 2}}\nHope that helps.', {"a": {"b": 2}}),
    ('  \n{"a": "brace } inside"}  ', {"a": "brace } inside"}),
    ("not json at all", None),
    ("[1, 2, 3]", None),          # must be an object
    ('{"a": 1', None),
    ("", None),
    ('<think>\nLet me weigh {"stance": "bearish"} first...\n</think>\n{"stance": "bullish"}', {"stance": "bullish"}),  # reasoning models
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def client(db, handler, attempts=2, local=False):
    llm = local_llm("http://127.0.0.1:11434/v1", "", db) if local else openrouter("k", db, timeout=5)
    llm.max_attempts = attempts
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=llm._http.headers)
    return llm


def reply(content, cost=0.002):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost}})


async def test_success_is_parsed_costed_and_logged(db):
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return reply('{"stance": "bullish"}')

    r = await client(db, handler).complete_json("momentum", "m/x", "SYSTEM", {"symbol": "AAA"}, decision_id="d1")
    assert r.ok and r.data == {"stance": "bullish"} and r.cost_usd == 0.002 and (r.tokens_in, r.tokens_out) == (100, 20)
    assert seen["model"] == "m/x" and seen["response_format"] == {"type": "json_object"}
    assert seen["messages"][0] == {"role": "system", "content": "SYSTEM"} and '"symbol":"AAA"' in seen["messages"][1]["content"]
    assert "Do not repeat the data" in seen["messages"][1]["content"]
    async with db.session() as s:
        row = (await s.execute(select(LLMCall))).scalar_one()
    assert row.agent == "momentum" and row.decision_id == "d1" and "SYSTEM" in row.prompt and row.error == ""


async def test_retries_once_on_server_error_then_succeeds(db):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else reply('{"ok": true}')

    r = await client(db, handler).complete_json("a", "m", "s", {})
    assert r.ok and len(calls) == 2


async def test_retries_on_non_json_and_accumulates_cost(db):
    replies = iter([reply("I think it goes up"), reply('{"ok": 1}')])
    r = await client(db, lambda req: next(replies)).complete_json("a", "m", "s", {})
    assert r.ok and r.cost_usd == pytest.approx(0.004)


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(500),
    lambda req: httpx.Response(401, json={"error": "bad key"}),
    lambda req: reply("still not json"),
    lambda req: httpx.Response(200, json={"unexpected": "shape"}),
    lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("timeout")),
])
async def test_failures_never_raise_and_are_logged(db, handler):
    r = await client(db, handler).complete_json("a", "m", "s", {})
    assert not r.ok and r.data is None and r.error
    async with db.session() as s:
        assert (await s.execute(select(LLMCall))).scalar_one().error


async def test_spend_tracker_sums_todays_calls(db):
    llm = client(db, lambda req: reply('{"ok": 1}', cost=0.25))
    for _ in range(3):
        await llm.complete_json("a", "m", "s", {})
    assert await SpendTracker(db).today() == pytest.approx(0.75)


async def test_falls_back_when_a_provider_rejects_json_mode_and_remembers(db):
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append("response_format" in body)
        return httpx.Response(400, json={"error": "response_format unsupported"}) if "response_format" in body else reply('{"ok": 1}')

    llm = client(db, handler)
    assert (await llm.complete_json("a", "m", "s", {})).ok
    assert (await llm.complete_json("a", "m", "s", {})).ok
    assert bodies == [True, False, False]                     # second call does not even try JSON mode


async def test_local_provider_sends_no_auth_no_cost_flag_and_records_zero_cost(db):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}],
                                         "usage": {"prompt_tokens": 50, "completion_tokens": 9, "cost": 99.0}})   # a local server never bills; ignore any cost field

    r = await client(db, handler, local=True).complete_json("momentum", "llama3.2", "s", {})
    assert r.ok and r.cost_usd == 0.0 and r.tokens_in == 50
    assert seen["auth"] is None and "usage" not in seen["body"] and seen["url"] == "http://127.0.0.1:11434/v1/chat/completions"


async def test_router_dispatches_by_prefix_and_strips_it(db):
    calls = []

    def handler(req):
        calls.append((str(req.url.host), json.loads(req.content)["model"]))
        return reply('{"ok": 1}')

    hosted, local = client(db, handler), client(db, handler, local=True)
    router = RouterLLM(hosted, local, db)
    assert router.name == "openrouter+local"
    assert (await router.complete_json("a", "local/llama3.2", "s", {})).ok
    assert (await router.complete_json("a", "anthropic/claude-haiku-4.5", "s", {})).ok
    assert calls == [("127.0.0.1", "llama3.2"), ("openrouter.ai", "anthropic/claude-haiku-4.5")]


async def test_router_fails_clearly_when_a_provider_is_missing(db):
    router = RouterLLM(None, client(db, lambda r: reply('{"ok": 1}'), local=True), db)
    r = await router.complete_json("a", "anthropic/claude-haiku-4.5", "s", {})
    assert not r.ok and "AIT_OPENROUTER_API_KEY" in r.error
    assert (await router.complete_json("a", "local/x", "s", {})).ok
    async with db.session() as s:
        assert (await s.execute(select(LLMCall))).scalars().first().error      # the failure is on record too


def test_user_message_names_the_expected_output_keys():
    from app.agents.swarm import system_prompt
    msg = frame_user_message(system_prompt("momentum"), {"symbol": "X"})
    assert "exactly these keys: stance, confidence, thesis, evidence, invalidation" in msg
    assert '{"symbol":"X"}' in msg


def test_echo_detection():
    payload = {"symbol": "NVDA", "price": 1, "signals": {}, "recent": {}, "regime": {}, "minutes_to_close": 9}
    assert echoes_input({**payload, "extra": 1}, payload)
    assert echoes_input({"symbol": "NVDA", "signals": {}, "recent": {}}, payload)
    assert not echoes_input({"stance": "bullish", "confidence": 0.7, "thesis": "t"}, payload)
    assert not echoes_input({"symbol": "NVDA", "stance": "bullish"}, payload)   # one shared key is fine
    assert not echoes_input({"anything": 1}, {})


async def test_an_echoed_reply_is_a_failure_and_is_retried(db):
    payload = {"symbol": "NVDA", "price": 1, "signals": {}, "recent": {}}
    replies = iter([reply(json.dumps(payload)), reply('{"stance": "bullish"}')])
    r = await client(db, lambda req: next(replies)).complete_json("momentum", "m", "s", payload)
    assert r.ok and r.data == {"stance": "bullish"}
    r = await client(db, lambda req: reply(json.dumps(payload))).complete_json("momentum", "m", "s", payload)
    assert not r.ok and "echoed" in r.error


async def test_local_thinking_setting_is_sent_and_reasoning_kept_in_trace(db):
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"stance": "bearish"}',
                                                                  "reasoning": "RSI 35 and below VWAP {not json}"},
                                                      "finish_reason": "stop"}], "usage": {}})

    llm = client(db, handler, local=True)
    llm.reasoning_effort = "low"
    r = await llm.complete_json("momentum", "qwen", "s", {})
    assert r.ok and r.data == {"stance": "bearish"}
    assert seen["reasoning_effort"] == "low" and seen["max_tokens"] == 6000
    assert "RSI 35 and below VWAP" in r.raw
    async with db.session() as s:
        row = (await s.execute(select(LLMCall))).scalar_one()
    assert "RSI 35 and below VWAP" in row.response


def test_thinking_off_keeps_the_small_reply_budget(db):
    from app.agents.llm import local_llm as mk
    assert mk("http://x/v1", "", db, thinking="none").max_tokens == 1200
    assert mk("http://x/v1", "", db).max_tokens == 1200
    assert mk("http://x/v1", "", db, thinking="high").max_tokens == 6000


async def test_reply_cut_off_by_thinking_is_not_retried(db):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "reasoning": "hmm " * 50},
                                                      "finish_reason": "length"}], "usage": {}})

    r = await client(db, handler, local=True).complete_json("a", "m", "s", {})
    assert not r.ok and "budget" in r.error and len(calls) == 1


async def test_server_rejecting_thinking_control_falls_back(db):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(400) if "reasoning_effort" in bodies[-1] else reply('{"ok": 1}')

    llm = client(db, handler, local=True)
    llm.reasoning_effort = "none"
    r = await llm.complete_json("a", "m", "s", {})
    assert r.ok and "reasoning_effort" not in bodies[-1] and "response_format" in bodies[-1]


async def test_router_gives_thinking_only_to_the_listed_agents_on_the_local_route(db):
    bodies = {}

    def handler(req):
        b = json.loads(req.content)
        bodies[b["messages"][0]["content"]] = b
        return reply('{"ok": 1}')

    local, hosted = client(db, handler, local=True), client(db, handler)
    router = RouterLLM(hosted, local, db, thinking={"portfolio_manager": "medium", "momentum_hosted": "high"})
    await router.complete_json("portfolio_manager", "local/q", "pm", {})
    await router.complete_json("momentum", "local/q", "mom", {})
    await router.complete_json("momentum_hosted", "vendor/x", "hosted", {})
    assert bodies["pm"]["reasoning_effort"] == "medium" and bodies["pm"]["max_tokens"] == 6000
    assert "reasoning_effort" not in bodies["mom"] and bodies["mom"]["max_tokens"] == 1200
    assert "reasoning_effort" not in bodies["hosted"]
