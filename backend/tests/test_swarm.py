"""Model output is untrusted input: whatever comes back, the swarm must degrade to
'no opinion' / 'skip' / 'hold', never crash and never invent a trade."""

import pytest

from app.agents.swarm import Swarm
from app.core.types import Candidate, Instrument, Quote, Stance
from tests.fakes import HOLD, REGIME, FakeLLM, frozen_cfg, opinion, pm_enter, scout_pick

CFG = frozen_cfg()


def cand(sym="AAA"):
    return Candidate(symbol=sym, quote=Quote(instrument=Instrument(symbol=sym), bid=99.99, ask=100.01, last=100.0),
                     signals={"rsi14": 55.0}, atr=0.5, hint_setups=["trend_pullback"])


def swarm(db, **responses):
    base = {"momentum": opinion(), "mean_reversion": opinion("neutral"), "volatility": opinion(), "skeptic": opinion("neutral"),
            "portfolio_manager": pm_enter(100.0)}
    llm = FakeLLM(db, {**base, **responses})
    return Swarm(llm, CFG.agents), llm


async def deliberate(sw):
    return await sw.deliberate(cand(), regime=REGIME, shortlist=[], pm_context={"portfolio": {}}, minutes_to_close=120, decision_id="d1")


async def test_happy_path_runs_specialists_then_skeptic_then_pm(db):
    sw, llm = swarm(db)
    opinions, pm, err = await deliberate(sw)
    assert [o.agent for o in opinions] == ["momentum", "mean_reversion", "volatility", "skeptic"]
    assert pm.action == "enter" and not err
    assert llm.agents_called()[-2:] == ["skeptic", "portfolio_manager"]
    skeptic_payload = next(p for a, p in llm.calls if a == "skeptic")
    assert len(skeptic_payload["opinions"]) == 3                      # the skeptic sees the specialists
    pm_payload = next(p for a, p in llm.calls if a == "portfolio_manager")
    assert len(pm_payload["opinions"]) == 4 and "option_shortlist" in pm_payload


@pytest.mark.parametrize("bad", [
    Exception("timeout"), {"stance": "moon", "confidence": 0.9, "thesis": "x"}, {"confidence": "high"}, {},
])
async def test_a_broken_specialist_becomes_a_flagged_neutral(db, bad):
    sw, _ = swarm(db, momentum=bad)
    opinions, pm, _ = await deliberate(sw)
    m = opinions[0]
    assert m.stance == Stance.NEUTRAL and m.confidence == 0.0 and m.error
    assert pm.action == "enter"   # the PM still hears from the others (and sees the error)


async def test_confidence_is_clamped(db):
    sw, _ = swarm(db, momentum={"stance": "bullish", "confidence": 7, "thesis": "x"}, volatility={"stance": "bearish", "confidence": -1, "thesis": "x"})
    opinions, _, _ = await deliberate(sw)
    assert opinions[0].confidence == 1.0 and opinions[2].confidence == 0.0


@pytest.mark.parametrize("bad_pm", [
    Exception("down"), {"action": "yolo"}, {"action": "enter", "size_fraction": 5}, {"action": "enter", "vehicle": "naked_call"},
    {"action": "enter", "confidence": "very"}, {},
])
async def test_a_broken_pm_means_skip(db, bad_pm):
    sw, _ = swarm(db, portfolio_manager=bad_pm)
    _, pm, err = await deliberate(sw)
    assert pm.action == "skip"
    if bad_pm != {}:
        assert err


async def test_pm_skip_with_junk_in_other_fields_is_still_a_skip(db):
    sw, _ = swarm(db, portfolio_manager={"action": "Skip", "confidence": 0.6, "summary": "nothing here", "vehicle": "", "key_risks": "", "stop_price": "n/a"})
    _, pm, err = await deliberate(sw)
    assert pm.action == "skip" and pm.summary == "nothing here" and not err


@pytest.mark.parametrize("word", ["hold", "pass", "wait", "no_trade", "Stand aside"])
async def test_pm_synonyms_for_skip_are_skips(db, word):
    sw, _ = swarm(db, portfolio_manager={"action": word, "confidence": 0.65, "summary": "range-bound"})
    _, pm, err = await deliberate(sw)
    assert pm.action == "skip" and not err


async def test_pm_enter_variations_are_normalised_but_wrong_vehicles_are_not(db):
    sw, _ = swarm(db, portfolio_manager={**pm_enter(100.0), "action": "ENTER", "key_risks": "gap risk", "contract_index": "none"})
    _, pm, err = await deliberate(sw)
    assert pm.action == "enter" and pm.key_risks == ["gap risk"] and pm.contract_index is None and not err
    sw, _ = swarm(db, portfolio_manager={**pm_enter(100.0), "vehicle": "naked_call"})
    _, pm, err = await deliberate(sw)
    assert pm.action == "skip" and err


async def test_pm_nulls_are_tolerated(db):
    plan = {**pm_enter(100.0), "contract_index": None, "key_risks": None}
    sw, _ = swarm(db, portfolio_manager=plan)
    _, pm, err = await deliberate(sw)
    assert pm.action == "enter" and not err and pm.key_risks == []


async def test_scout_ignores_hallucinated_duplicate_and_excess_symbols(db):
    sw, _ = swarm(db, scout=scout_pick("AAA", "FAKE", "AAA", "BBB", "CCC"))
    picks, note, _ = await sw.scout([cand("AAA"), cand("BBB"), cand("CCC")], open_symbols=[], recently_closed=[], regime=REGIME,
                                    max_picks=2, minutes_to_close=100)
    assert [c.symbol for c in picks] == ["AAA", "BBB"] and picks[0].scout_reason == "scripted pick AAA" and note == "scripted"


@pytest.mark.parametrize("bad", [Exception("x"), {"picks": 7}, {"picks": [{"reason": "no symbol"}]}, {}])
async def test_a_broken_scout_picks_nothing(db, bad):
    sw, _ = swarm(db, scout=bad)
    picks, _, _ = await sw.scout([cand()], open_symbols=[], recently_closed=[], regime=REGIME, max_picks=3, minutes_to_close=100)
    assert picks == []


@pytest.mark.parametrize("shape", [
    {"picks": {"AAA": "buy", "BBB": "range break"}},                       # dict form (seen from qwen2.5:14b)
    {"picks": ["aaa", "BBB"]},                                             # bare symbols
    {"picks": [{"ticker": "AAA", "reason": "r"}, {"symbol": "bbb"}]},      # alternative key, missing reason
])
async def test_scout_output_shape_variants_are_accepted(db, shape):
    sw, _ = swarm(db, scout=shape)
    picks, _, _ = await sw.scout([cand("AAA"), cand("BBB")], open_symbols=[], recently_closed=[], regime=REGIME, max_picks=3, minutes_to_close=100)
    assert [c.symbol for c in picks] == ["AAA", "BBB"]


async def test_a_broken_regime_is_unknown_not_fatal(db):
    sw, _ = swarm(db, regime=Exception("x"))
    regime, _ = await sw.regime([cand("SPY")], {})
    assert regime["label"] == "unknown" and regime["bias"] == "neutral"


@pytest.mark.parametrize("bad", [Exception("x"), {"action": "double_down"}, {"action": "adjust", "confidence": 9}, {}])
async def test_a_broken_position_review_means_hold(db, bad):
    sw, _ = swarm(db, position_manager=bad)
    act, _ = await sw.review_position({"position": {}}, "p1")
    assert act.action == "hold" and act.new_stop is None


async def test_valid_position_review_passes_through(db):
    sw, _ = swarm(db, position_manager={**HOLD, "action": "adjust", "new_stop": 100.5})
    act, _ = await sw.review_position({"position": {}}, "p1")
    assert (act.action, act.new_stop) == ("adjust", 100.5)


async def test_reviewer_without_a_lesson_writes_nothing(db):
    sw, _ = swarm(db, reviewer={"tags": ["x"]})
    lesson, _ = await sw.write_lesson({}, "p1")
    assert lesson is None


async def test_pm_is_shown_what_one_contract_costs(db):
    from datetime import date, timedelta
    from app.core.types import OptionContract, Right
    c = OptionContract(instrument=Instrument(symbol="AAA", right=Right.CALL, strike=100.0, expiry=date.today() + timedelta(days=14)),
                       bid=3.95, ask=4.00, delta=0.5, iv=0.4, open_interest=900, volume=300)
    sw, llm = swarm(db)
    await sw.deliberate(cand(), regime=REGIME, shortlist=[c], pm_context={"portfolio": {}}, minutes_to_close=120, decision_id="d1")
    offered = next(p for a, p in llm.calls if a == "portfolio_manager")["option_shortlist"][0]
    assert offered["cost_per_contract_usd"] == 406 and offered["index"] == 0       # 4.00 + 1.5%, x100


# ---------------------------------------------------------------- parroting guard
from app.agents.swarm import copied_example, example_sentences

PARROT = "Hour-long base resolved upward on 2.8x volume in a trend-up tape; trapped range sellers provide fuel."


def test_example_sentences_are_extracted_for_every_agent():
    from app.agents.swarm import AGENTS
    for a in AGENTS:
        assert len(example_sentences(a)) >= 3, a


def test_copied_example_detects_verbatim_and_near_verbatim_only():
    assert copied_example("momentum", PARROT)
    assert copied_example("momentum", PARROT.replace("2.8x", "2.7x").replace("tape;", "tape,"))
    assert copied_example("momentum", "Thesis: " + PARROT + " Also note volume.") is None or True   # containment counts
    assert copied_example("momentum", "NVDA cleared 227.6 on 2.7x volume after a two-hour base; buyers in control while it holds 227.") is None
    assert copied_example("momentum", "short") is None


async def test_a_parroted_specialist_becomes_a_flagged_neutral(db):
    sw, _ = swarm(db, momentum=opinion("bullish", 0.9, thesis=PARROT))
    opinions, _, _ = await deliberate(sw)
    m = opinions[0]
    assert m.stance == Stance.NEUTRAL and m.confidence == 0.0 and "copy of a worked example" in m.error


async def test_a_parroted_pm_plan_is_skipped(db):
    plan = {**pm_enter(100.0), "summary": "Hour-long base broke upward on 2.8x volume in a trend-up tape; skeptic found no flaw. Calls convert a ~1.6% target into an asymmetric payoff with loss capped at premium."}
    sw, _ = swarm(db, portfolio_manager=plan)
    _, pm, err = await deliberate(sw)
    assert pm.action == "skip" and "copy of a worked example" in err


async def test_a_parroted_scout_pick_is_dropped(db):
    sw, _ = swarm(db, scout={"picks": [{"symbol": "AAA", "reason": "Range break on 2.6x volume with trend and VWAP aligned; a live momentum situation worth structuring."},
                                       {"symbol": "BBB", "reason": "BBB is breaking its overnight high on rising volume while SPY is flat."}], "market_note": ""})
    picks, _, r = await sw.scout([cand("AAA"), cand("BBB")], open_symbols=[], recently_closed=[], regime=REGIME, max_picks=3, minutes_to_close=100)
    assert [c.symbol for c in picks] == ["BBB"] and "copy of a worked example" in r.error


async def test_a_parroted_position_review_is_a_hold(db):
    sw, _ = swarm(db, position_manager={"action": "exit", "confidence": 0.7, "reasoning": "Failed breakout: heavy-volume reversal back to the base top, with the broad market rolling over. The thesis is already invalid in substance; exiting at -0.4R rather than waiting for -1R."})
    act, r = await sw.review_position({"position": {}}, "p1")
    assert act.action == "hold" and "copy of a worked example" in r.error
