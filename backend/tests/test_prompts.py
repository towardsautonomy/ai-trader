"""Prompts are the trading logic, so they get regression tests too: every example an
agent is taught from must itself be a valid reply that the code can parse."""

import json
import re

import pytest

from app.agents.swarm import AGENTS, PROMPT_DIR, system_prompt
from app.core.types import PMDecision, PositionAction, Stance

FENCE = re.compile(r"```json\n(.*?)\n```", re.S)
SPECIALISTS = ["momentum", "mean_reversion", "volatility", "skeptic"]


def examples(agent):
    blocks = FENCE.findall((PROMPT_DIR / f"{agent}.md").read_text())
    return blocks[:-1], blocks[-1]   # worked examples, then the output template


def test_every_agent_has_a_prompt_and_there_are_no_strays():
    files = {p.stem for p in PROMPT_DIR.glob("*.md")} - {"_glossary"}
    assert files == set(AGENTS)


@pytest.mark.parametrize("agent", AGENTS)
def test_prompt_teaches_by_example(agent):
    text = system_prompt(agent)
    worked, _ = examples(agent)
    assert len(worked) >= 4, "each agent needs at least four worked examples"
    assert text.count("**Example") == len(worked)
    assert "Reading the data" in text                      # glossary is appended
    assert "single JSON object" in text


@pytest.mark.parametrize("agent", AGENTS)
def test_every_worked_example_is_valid_json(agent):
    for block in examples(agent)[0]:
        assert isinstance(json.loads(block), dict)


@pytest.mark.parametrize("agent", SPECIALISTS)
def test_specialist_examples_parse_as_opinions_and_include_standing_aside(agent):
    parsed = [json.loads(b) for b in examples(agent)[0]]
    for ex in parsed:
        Stance(ex["stance"])
        assert 0 <= ex["confidence"] <= 1 and ex["thesis"] and "invalidation" in ex and isinstance(ex["evidence"], list)
    assert any(ex["stance"] == "neutral" for ex in parsed), "must show when NOT to have a view"


def test_pm_examples_validate_and_show_both_entering_and_skipping():
    plans = [PMDecision.model_validate({k: v for k, v in json.loads(b).items() if v is not None}) for b in examples("portfolio_manager")[0]]
    actions = [p.action for p in plans]
    assert actions.count("skip") >= 2 and actions.count("enter") >= 2
    entered = [p for p in plans if p.action == "enter"]
    assert {p.vehicle.value for p in entered} >= {"shares", "long_call", "long_put"}
    assert any(p.size_fraction < 0.6 for p in entered) and any(p.size_fraction > 0.8 for p in entered)  # sizing is taught, both ways
    for p in entered:
        long = p.direction == "long"
        assert (p.stop_price < p.target_price) == long
        assert (p.vehicle.value == "long_put") == (not long)   # examples never teach an impossible structure


def test_position_manager_examples_validate_and_cover_every_action():
    acts = [PositionAction.model_validate(json.loads(b)) for b in examples("position_manager")[0]]
    assert {a.action for a in acts} == {"hold", "adjust", "exit"}
    assert all(a.reasoning for a in acts)


def test_scout_examples_include_picking_nothing():
    parsed = [json.loads(b) for b in examples("scout")[0]]
    assert any(ex["picks"] == [] for ex in parsed) and any(ex["picks"] for ex in parsed)


def test_reviewer_examples_separate_decision_quality_from_outcome():
    parsed = [json.loads(b) for b in examples("reviewer")[0]]
    tags = {t for ex in parsed for t in ex["tags"]}
    assert {"good_loss", "lucky_win"} <= tags
    assert all(ex["situation"] and ex["what_happened"] and ex["lesson"] for ex in parsed)


def test_no_prompt_promises_something_the_system_cannot_do():
    for agent in AGENTS:
        text = system_prompt(agent).lower()
        for banned in ("sell a call", "sell a put", "short the stock", "credit spread", "iron condor", "margin"):
            assert banned not in text, f"{agent} prompt mentions {banned!r}"
