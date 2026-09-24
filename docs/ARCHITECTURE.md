# Architecture

Two processes: a Python backend that trades (FastAPI + SQLite, `backend/`) and a Next.js terminal that watches it
(`frontend/`). `scripts/trader` starts, stops and supervises both.

```
                    ┌──────────────── backend (one process) ────────────────┐
 Robinhood MCP ◄────┤ market data  ─► scanner ─► agent swarm ─► planner ─►   │
 (or yfinance,      │                              │            risk envelope │
  synthetic)        │ broker (paper or Robinhood) ◄┴── executor ◄──┘         │
                    │ exit loop · review loop · watchdog · kill switch       │
 Ollama / vLLM /    │ SQLite: every decision, opinion, prompt, order, event  │
 OpenRouter ◄───────┤ REST + WebSocket API                                   │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                       terminal UI (browser)
```

## The four loops

The engine (`app/engine/orchestrator.py`) runs four independent loops. None of them does anything while the market is
closed, except the watchdog honouring kill markers.

| Loop | Every | Uses models | What it does |
|---|---|---|---|
| entry | 120 s | yes | scan the universe, regime + scout agents, deliberate the picks, plan, check, execute |
| exit | 5 s | no | enforce the stop, target and thesis levels the agents set, plus the hard backstops |
| review | 120 s | yes | position-manager agent reviews each open trade: hold, tighten, extend, or exit |
| watchdog | 10 s | no | equity snapshots, automatic kill-switch trips, ledger/broker reconciliation |

An entry cycle, in order:

1. **Market data** for the universe (`config.yaml` → `universe`): quotes and 78 five-minute bars.
2. **Scanner** (`app/scanner/scanner.py`) computes features: RSI, rate of change, EMA trend, VWAP deviation, z-score,
   relative volume, breakout distance in ATRs. It measures; it never decides.
3. **Regime agent** reads SPY, QQQ and breadth: what kind of day is it.
4. **Scout agent** picks the few symbols worth a closer look (`engine.max_candidates_per_cycle`).
5. For each pick: **catalysts** (recent headlines, nearby earnings) and an **option shortlist** of liquid, affordable
   contracts near delta 0.5 are attached.
6. **Momentum, mean-reversion and volatility** specialists give independent opinions in parallel; the **skeptic** then
   argues the strongest honest case against them.
7. **Portfolio manager** decides: enter or skip, shares or a long call or put, which contract, stop, target, size, hold.
8. **Planner** (`app/engine/planner.py`) turns that into an order: arithmetic, then clamps into the envelope, logging
   every clamp. Plans it cannot build are discarded with the reason.
9. **Risk envelope** (`app/risk/engine.py`) evaluates every rule and records each result.
10. **Executor** (`app/engine/execution.py`) sends a marketable limit order and never chases an unfilled entry.

After every close the **reviewer** agent writes a lesson. Lessons and each agent's realised hit rate go back into the
portfolio manager's context.

## Where things live

```
backend/
  config.yaml                 every tunable; the `risk` section is the hard envelope
  app/agents/prompts/*.md     the trading judgment, taught by worked examples
  app/agents/swarm.py         runs the agents, validates and repairs their replies
  app/agents/llm.py           OpenAI-compatible client (OpenRouter, Ollama, vLLM), router, spend tracking
  app/engine/orchestrator.py  the four loops
  app/engine/planner.py       option shortlist, plan -> clamped order
  app/engine/exits.py         exit rules for the 5 s loop
  app/engine/execution.py     orders, fills, the position ledger
  app/risk/engine.py          the envelope
  app/core/                   kill switch, market clock, events, types
  app/broker/paper.py         paper broker: real quotes in, pessimistic simulated fills out
  app/broker/robinhood_mcp.py Robinhood MCP client (OAuth), market data + broker
  app/marketdata/             yfinance and synthetic sources
  app/readiness.py            live-readiness checks and the Robinhood panel data
  app/modeswitch.py           paper/live choice from the dashboard, one-time live codes
  app/api/server.py           REST + WebSocket API
  app/cli.py                  `./ait`: run, kill, rearm, doctor, llm, robinhood, live-code
  app/runtime.py              assembles the system; every live-mode gate
frontend/src/
  app/                        pages: terminal, history, agents, log, setup, decision and position traces
  components/                 panels, kill switch, setup panels
scripts/                      trader (the control script), vllm.sh, start/stop/status/kill shortcuts
```

## Persistence

One SQLite file per data directory (`backend/data/ai_trader.db`; the demo uses `backend/data/demo/`). Everything that
explains a decision is stored:

| Table | Holds |
|---|---|
| `decisions` | each deliberation: candidate data, regime, context shown to the PM, PM reply, plan, risk verdict, outcome |
| `agent_opinions` | every specialist and skeptic opinion |
| `llm_calls` | every model call: exact prompt, raw reply (including thinking), latency, tokens, cost, error |
| `orders`, `positions` | orders, fills, positions with entry/exit, stops, reasons, realised P&L |
| `position_reviews`, `lessons` | the position manager's reviews and the reviewer's lessons |
| `events` | the event stream shown in the UI |
| `equity_snapshots`, `kv` | equity curve; day-start equity, high-water mark, kill state, paper book, verify digest |

Also in the data directory: `robinhood_tokens.json` (OAuth, mode 600), `robinhood_tools.json` (discovered schemas),
`mode.json` (paper/live choice), `KILL` / `KILL_FLATTEN` markers. The whole directory is git-ignored.

## API

REST under `/api`, plus a WebSocket at `/ws` that pushes every event. POST endpoints require `X-API-Token` when
`AIT_API_TOKEN` is set.

| Endpoint | |
|---|---|
| `GET /api/status` | mode, data source, broker, models, account, session, limits, loop heartbeats, regime |
| `GET /api/positions`, `/api/positions/{id}` | open or closed positions; full trace of one |
| `GET /api/history` | closed trades with exact fills, P&L per session, totals |
| `GET /api/decisions`, `/api/decisions/{id}` | deliberations; full trace of one |
| `GET /api/llm_calls/{id}` | one model call: prompt and reply |
| `GET /api/events`, `/api/orders`, `/api/equity`, `/api/stats`, `/api/agents`, `/api/lessons`, `/api/config` | |
| `GET /api/robinhood`, `/api/live/readiness` | connection facts; the live checklist |
| `POST /api/kill`, `/api/kill/rearm` | kill switch |
| `POST /api/positions/{id}/close` | close one position now |
| `POST /api/mode` | switch paper/live (live needs readiness, the phrase and a one-time code) |

Sample responses: [api_samples/](api_samples/).
