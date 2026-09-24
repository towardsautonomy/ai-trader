# Testing

```bash
cd backend && ./check.sh        # the backend suite (~20 s)
./scripts/trader test                   # the suite + frontend lint and typecheck
./scripts/trader llm test local/<model> NVDA   # one real deliberation on live data with a real model
./scripts/trader robinhood verify       # the Robinhood adapter against the real server (no orders)
```

What the suite covers:

- **Risk envelope**: every rule rejects its own violation; property tests assert no approved trade breaches a cap.
- **Planner and exits**: clamping, sizing, every exit trigger; no agent review can increase risk.
- **Scenarios** (`tests/test_scenarios.py`): the real engine end to end on a scripted market: take-profit with a full
  audit trail, stop-loss, long-put thesis stop, clamps, stale plans, tighten-only stops, end-of-day flatten, market
  closed, every kill-switch path and automatic trip, budget, slots, lessons, catalysts, the market read across restarts.
- **Agent I/O**: malformed, copied, echoed or missing model output always degrades to no-opinion, skip or hold.
- **Prompts**: every worked example parses and validates, prompts include stand-aside cases, and never teach a
  structure the system cannot trade.
- **Model client**: retries, JSON-mode fallback, thinking budgets, reasoning kept in traces.
- **Robinhood adapter**: replies replayed in the shapes captured from the real server; the single agentic account rule,
  order formats, ticks, idempotency keys, both order books, positions, catalysts, schema pinning, the MCP session with
  the library's real types.
- **Paper broker**: pessimistic fills on real ticks, never past the limit, no shorts, persistence.
- **Clock**: sessions, half-days, and the trading day (evenings and pre-market belong to the last session).
- **Startup and modes**: every live gate, the dashboard mode switch, one-time codes, the fallback to paper.
- **API**: status, history, kill switch, close, readiness.

Tests use a frozen config (`tests/fixtures/config.yaml`), so tuning `config.yaml` never breaks or weakens them.
