# The agents

Nine agents, one job each. They make every trading judgment; code only measures, enforces the envelope, and executes.

| Agent | Sees | Decides |
|---|---|---|
| regime | SPY, QQQ, breadth | what kind of day it is and what tends to work in it |
| scout | a feature table for the whole universe | which few symbols deserve a deliberation |
| momentum | one candidate | is there a trend worth joining |
| mean_reversion | one candidate | is there an overextension worth fading |
| volatility | one candidate + option shortlist | is the move big enough, and is options pricing worth it |
| skeptic | the candidate + the three opinions | the strongest honest case against them |
| portfolio_manager | all of that + portfolio, envelope, lessons, track records | enter or skip; vehicle, contract, stop, target, size, hold |
| position_manager | an open trade, its thesis, the current data | hold, tighten, extend, or exit |
| reviewer | a closed trade and its full history | the lesson, fed back to the portfolio manager |

Each candidate carries its price path, the scanner's measurements, the regime, minutes to the close, and `catalysts`:
recent headlines with their age and the nearest earnings report. Field meanings are in
`backend/app/agents/prompts/_glossary.md`, which every agent receives.

## Judgment lives in prompts, taught by example

`backend/app/agents/prompts/*.md` teach with worked examples, not rules: a real breakout next to a tired one, when to
stand aside, how a flawed-but-valid setup is sized, when a thesis is dead before the stop is hit, why news changes what
a chart means. There are no coded thresholds for conviction, consensus, reward-to-risk or loss streaks. To change how the
desk trades, edit a prompt; the prompt tests check every example still parses, validates, includes "stand aside" cases,
and never teaches a structure the system cannot trade.

Only the guarantees you would not want left to judgment are code: see [RISK_AND_SAFETY.md](RISK_AND_SAFETY.md).

## Models and thinking

Each agent's model is set in `backend/config.yaml` (`agents.default_model`, `agents.models`); `./scripts/trader start --llm X`
points all of them at one model. Names starting with `local/` run on this machine (Ollama, vLLM, llama.cpp, LM Studio:
anything OpenAI-compatible at `AIT_LOCAL_LLM_URL`); anything else goes to OpenRouter.

Local reasoning models can think before answering. Thinking is set per agent (`agents.thinking`): the seats where a
decision is made think, the specialists answer fast.

```yaml
thinking:
  portfolio_manager: medium
  skeptic: low
  position_manager: low
  reviewer: medium
```

Thinking calls get a 6,000-token reply budget and at least 360 s; the reasoning is stored with the call, so traces show
it. See [LOCAL_MODELS.md](LOCAL_MODELS.md) for speeds.

## When a model misbehaves

Weak models fail in recognisable ways. Each is caught, turned into "no opinion" (or skip, or hold) rather than a trade,
and recorded so traces and `./scripts/trader llm test` show it:

| Failure | Seen with | Handling |
|---|---|---|
| Copies a worked example from the prompt instead of reading the data | 8B models | detected against every example sentence; discarded |
| Echoes the input JSON instead of answering | 14B models in JSON mode | detected; one retry with the data framed in prose |
| Right idea, wrong shape (`"hold"` for skip, a dict instead of a list) | 14B models | tolerated where harmless; a wrong vehicle is still rejected |
| Thinking uses up the reply budget | thinking models | reported as such, not retried |
| All specialists paraphrase the regime with one confidence | 14B and 32B models | not detectable in code; `llm test` makes it visible. Use a stronger model |

Without any model provider the agents are an offline heuristic, labelled as such everywhere and refused in live mode.

## Feedback

After each close the reviewer writes a lesson (situation, what happened, lesson, tags). The portfolio manager sees the
most relevant recent lessons and each agent's realised hit rate, so the desk's guidance grows from its own history.
