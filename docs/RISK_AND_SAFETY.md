# Risk and safety

Agents decide; code guarantees. A loss limit that is only a suggestion limits nothing, so the guarantees below are
enforced in code (`backend/config.yaml` → `risk`, `equity_exits`, `options`, `session`; enforced in
`app/risk/engine.py`, `app/engine/planner.py`, `app/engine/exits.py`).

## The envelope

| Guarantee | How (defaults) |
|---|---|
| Options always have limited loss | Only long calls and long puts can be opened. Premium per trade ≤ 1% of equity, and that premium is the maximum loss. A hard stop at −35% of premium backs it up, and options are sold 2 days before expiry. Short options and short stock are rejected structurally. |
| One share trade cannot hurt much | Size comes from the stop so the loss at the stop is ≤ 0.5% of equity; a stop can be no further than 3% from entry; one position ≤ 25% of equity. |
| One day cannot hurt much | −2% on the day → kill switch HALT. −6% from the equity high-water mark → FLATTEN. |
| Nothing overnight | No entries in the first 5 or last 20 minutes; everything is sold 10 minutes before the close. Holidays and half-days come from the NYSE calendar. |
| Profits get taken | The agent's target, plus a hard profit ceiling that always sells: +8% on shares, +100% on option premium. Maximum hold 240 min (shares), 300 min (options). |
| The AI can only reduce risk on open trades | Stops only tighten. Targets stay under the ceiling. |
| Bad data and dead models do not trade | Stale quotes are rejected; a failed model call means skip or hold, never a guess; repeated failures trip HALT. |
| No margin | Total exposure ≤ 100% of equity. |
| The ledger matches the broker | Reconciled every 10 s; three mismatches in a row trip HALT. |
| Spend is bounded | New entries stop when the day's hosted-model spend reaches the cap ($15). Local models cost $0. |

"Day" means the trading session: the evening after a session and the next pre-market still report that session. Tests
run against a frozen copy of the config, so tuning `config.yaml` never weakens them.

## Kill switch

Two levels: **HALT** stops new entries (open trades keep their stops and targets), **FLATTEN** halts and sells
everything. It survives restarts, never re-arms itself, and has four independent ways in:

```bash
./trader kill               # HALT: drops a marker file, works even if the API is wedged or down
./trader kill --flatten     # FLATTEN
touch backend/data/KILL_FLATTEN
# or HALT / FLATTEN in the terminal header (Shift+K), or POST /api/kill
./trader rearm              # deliberate: type REARM
```

It trips itself on the daily loss limit, the drawdown limit, a broker error streak, a market-data outage, a model
failure streak, repeated failed exits, and a ledger/broker mismatch.

## Live mode gates

Robinhood's agentic MCP has no paper mode: every order sent through it is real. The paper broker is therefore the
default, and live mode must clear all of these at every start (`app/runtime.py`):

1. A confirmation: `AIT_LIVE_CONFIRM=I-ACCEPT-REAL-MONEY-RISK`, or a switch made on the dashboard (below).
2. Every agent has a real model (never the offline heuristic).
3. Market data from Robinhood; synthetic data and an ignored clock are refused.
4. Logged in to Robinhood, exactly one account marked agent-tradable, and its options level known (below level 2,
   options are switched off and only shares trade).
5. The adapter passed `./trader robinhood verify`, and Robinhood's tool schemas are unchanged since then.

## Switching modes from the dashboard

SETUP shows a live-readiness checklist evaluated against the real systems: not the demo, real models, logged in,
verified, schemas unchanged, one agentic account, account funded, broker positions match the live ledger, kill switch
armed, and (advisory) a paper track record.

The switch to live requires all blocking checks to pass, the typed phrase, and a one-time code from `./trader
live-code` (6 digits, 10 minutes, single use, burned by any wrong guess). The code exists because the dashboard's API
token is compiled into the browser bundle: anyone who can load the page can call the API, but only someone with a shell
on the machine can print a code.

The choice is saved in `backend/data/mode.json` and wins over `.env`. The engine re-executes itself so every startup
gate runs again. If a gate refuses, it falls back to paper and the dashboard shows why. Leaving live is refused while
live positions are open, so nothing real is left unmanaged.

## Known limits

- Deliberation takes minutes on local models: this is minutes-scale intraday trading, not HFT. Exits are fast (5 s).
- Stops are enforced by the engine, not held at the broker. A gap, or the engine being down, can fill through them.
  Robinhood supports stop orders on stocks and sell-to-close stop orders on options; broker-side protective stops are
  a planned addition.
- Defined-risk spreads are not supported: legging into one would break the limited-loss guarantee.
- Pattern-day-trader rules are the owner's responsibility (`risk.max_day_trades_5d`).
- No trading system is guaranteed to make money. The envelope bounds how much a bad day costs; it does not make days good.
