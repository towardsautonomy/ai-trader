# Build status (resume from here)

## Done and tested (`cd backend && ./check.sh`)
- [x] Config (config.yaml + .env), domain types, DB models with full audit trail, UTC-safe datetimes
- [x] Event log (DB + websocket), NYSE market clock (holidays, half-days, buffers, flatten window)
- [x] Kill switch: halt/flatten, durable, file markers, escalation-only, manual re-arm, auto-trips
- [x] Market data: synthetic, yfinance (verified against the live internet). Paper broker.
- [x] Feature scanner (measures only; hint score is an offline fallback)
- [x] Agents: 9 prompts built from worked examples; swarm with defensive parsing; OpenRouter client; offline stand-in
- [x] Planner (clamp + record), risk envelope, fast exits, tighten-only reviews, executor with repricing exits
- [x] Orchestrator: entry / exit / review / watchdog loops, reconciliation, lessons + track record feedback
- [x] Robinhood MCP adapter: OAuth client, tool discovery, tool map, verify gate (unit-tested against a fake session)
- [x] REST + WebSocket API, CLI (`./ait`), live-mode startup gates
- [x] README, docs/ROBINHOOD.md

## Not verifiable without the owner
- [ ] Real OpenRouter calls (needs AIT_OPENROUTER_API_KEY): run paper with real models and read the traces
- [ ] Robinhood login -> discover -> finish tool map -> verify (needs the owner's Robinhood login)
- [ ] yfinance option shortlists during market hours (bids/OI are empty when the market is closed)

- [x] Frontend terminal (Next.js, port 3400): dashboard, kill switch, decision/position traces with exact prompts,
      agents + lessons, log. Build/lint/tsc clean; flows verified in headless Chrome (screenshots in frontend/screenshots/)
- [x] Engine liveness in /api/status, backwards event paging + server-side search, shortlist stored on decisions,
      review/lesson model calls on the position trace, honest close endpoint

- [x] Frontend wired to those additions (backwards log paging + server search, shortlist step with the chosen contract
      and cost per contract, review/lesson prompt expanders, 404/409 close notices, wedged-engine banner)
- [x] PM is shown `cost_per_contract_usd`; shortlist affordability judged at the entry limit; taught by a worked example

- [x] Top-level `trader` control script (start/stop/status/logs/kill/rearm/test), remote-viewable terminal
      (frontend derives the API host at runtime; backend CORS from AIT_CORS_ORIGINS; API token generated per install)

- [x] Local models: OpenAI-compatible provider, `local/` prefix routing per agent, doctor checks; verified against
      Ollama on this box (llama3.2 warm 0.5-0.8 s; cold load ~95 s)

- [x] Local-model run end to end (2026-09-22, qwen2.5:14b on Ollama, `./trader start --demo --fresh --llm local/qwen2.5:14b`):
      all 7 agents answer with original text, correct shapes after tolerance fixes, ~15 s per candidate, zero transport
      errors. Finding: the 14B model anchors on the regime block (all specialists paraphrase it and copy its confidence),
      so the swarm collapses to one voice and skips everything. Guards added: copied-example detection, input-echo
      detection, prose-framed user message, tolerant PM/scout shapes. `./trader llm test` is the quick check.

Last verified 2026-09-22: `./check.sh` 279 passing; backend + production frontend run together with zero errors.

- [x] Robinhood login works with a remote browser: paste the redirected callback URL into the terminal (or ssh -L 8765).
- [x] 2026-09-23: logged in; the real server has 75 tools. The template tool map was replaced by an adapter written
      against the real tools (see docs/ROBINHOOD.md); `verify` passes (reads + order simulator) and pins a schema digest.
      Paper mode now uses Robinhood data once verified. Fixed: mcp 2.x snake_case fields (login crashed after OAuth),
      MCP connection owned by one task (closing from another task raised).
      The owner's agentic account is limited_margin, options level 2, balance $0.
- [ ] Unproven until a real trade: placed-order reply shape, option fills/avg price, non-empty position rows.
- [x] deepseek-r1:32b tried (2026-09-22): ~73 s per candidate warm, but the same single-voice collapse (all four
      specialists identical, bearish 1.00) and the PM reply failed on the vehicle field. Cold load exceeds the 120 s timeout.
- [x] Ollama upgraded to 0.34.3 (2026-09-22). qwen3.8:27b-q8_0 pulled; use the 16K-context variant
      `qwen3.8:27b-q8-16k` (created with a Modelfile, 29 GB) because the default 262K context fills 45 GB.
- [x] Per-agent thinking for local models: `agents.thinking` in config.yaml (PM + reviewer medium, skeptic +
      position manager low, specialists off), AIT_LOCAL_LLM_THINKING for the rest. Thinking calls get a 6000-token
      budget and 360 s timeout; the model's reasoning is kept in the call trace; a reply cut off by thinking is not retried.
- [x] Qwen3.8 27B Q8 result: the single-voice collapse is gone. Specialists disagree with concrete reasons, the skeptic
      finds real flaws, PM skips with reasons, zero unusable answers. Cost: ~4.5 min per candidate.
- [x] Ollama 0.34 serves one request at a time here, so parallel candidates queued and timed out. Now
      `engine.parallel_candidates: 1`, `max_candidates_per_cycle: 2` (~9-10 min per cycle).
- [x] OLLAMA_NUM_PARALLEL tested (2026-09-23, temporary user-level server): no effect. Ollama 0.34 logs "model
      architecture does not currently support parallel requests" for qwen35 (Qwen3.8). 4 concurrent requests: 72 s with 1
      slot vs 65 s with 4, ~20 tok/s either way. Real batching would need another server (e.g. vLLM) or another model.
- [x] `./trader start --data robinhood|yfinance|auto` picks the market data source (not with --demo).
- [x] Dashboard SETUP page (2026-09-23): trading mode + guarded paper/live switch (readiness must pass, typed phrase,
      one-time code from `./trader live-code` because the API token ships in the browser bundle; restart via re-exec;
      refused live start falls back to paper), live readiness checklist against the real systems, Robinhood panel,
      per-agent models/thinking. Status bar names the market data source, where orders go, the agentic account and model;
      DEMO badge + banner; long event messages clamp to two lines. Choice persisted in data/mode.json.
- [ ] Not yet exercised: an actual dashboard switch to live (needs a funded agentic account).
- [x] First full paper session on real Robinhood data (2026-09-23, qwen3.8 27B local): 62 scans, 60 deliberations,
      2 trades (COST shares +$11.60, IWM put +$1.28), 1 unbuildable, 57 PM skips, 510 model calls, zero errors.
      Found + fixed: day P&L reset at New York midnight (now keyed to the trading session: evenings/pre-market report
      the last session); trades-today used a rolling 18 h; paper fills off-tick (now on exchange ticks, against us);
      PM confidence anchored at 0.72 on 45/60 (confidence now defined in the prompt). Added HISTORY page (/api/history)
      and a header SWITCH TO LIVE/PAPER button.
- [ ] Open findings from that session: 22/60 candidates had no put to express a bearish view (18 calls-only, 4 empty)
      on a trend-down day; only ~60 candidates/day at ~6 min per cycle; PM sizes 0.3-0.6 of the 0.5% risk budget,
      so wins are ~$10-100; position manager exits early near breakeven (+0.09R, +0.01R); position-manager reviews
      average 170 s against a 120 s interval; regime/market note are lost on restart.
- [ ] No news/catalyst/earnings input exists yet. Robinhood exposes news and earnings tools, not wired in.

Last verified 2026-09-23: `./check.sh` 341 passing; `./trader robinhood verify` passing against the real server.

## Bugs found by tests / real runs and fixed (each has a regression test)
- naive vs aware datetimes from SQLite; clamped stop rounding past the 3% limit; adjusted target rounding onto the market
- option entries/exits priced with no room for slippage (never filled / 5 reprices)
- startup race on the day-start KV row; DB connection leaked when startup is refused
- re-arm after a drawdown FLATTEN re-tripped at once; position stuck in "closing" after a crash
- exit loop judged a stale stop while the review agent was tightening it; synthetic restart re-priced open positions

## Ideas, not started
- Replay/backtest harness that feeds recorded days through the same engine
- Defined-risk verticals once atomic multi-leg orders are confirmed on the Robinhood MCP
- Broker-side protective stop orders in addition to software stops
- News/catalyst agent if the Robinhood MCP exposes news
