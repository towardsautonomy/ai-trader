# Getting started

From a fresh clone to paper trading on real Robinhood data. Every step before "Going live" is safe: nothing reaches a
broker until the last section.

## 1. Prerequisites

| Need | Version used | Why |
|---|---|---|
| Linux or macOS | Ubuntu 24 | the scripts are bash |
| [uv](https://docs.astral.sh/uv/) | 0.8+ | Python 3.12 backend and its dependencies |
| Node.js + npm | 22 | the terminal UI (Next.js) |
| A model | see below | the agents |
| A Robinhood account with Agentic trading | optional until real data / live | quotes, option chains, news; orders in live mode |

For models, pick one:

- **Local (free, private):** [Ollama](https://ollama.com) 0.34 or newer and a GPU with ~32 GB free for the recommended
  model. See [LOCAL_MODELS.md](LOCAL_MODELS.md).
- **Hosted:** an [OpenRouter](https://openrouter.ai) API key.
- **Neither:** the agents fall back to a labelled offline heuristic. Fine for trying the UI, useless for trading, and
  refused in live mode.

## 2. Install and see it run (two minutes, no keys)

```bash
git clone git@github.com:towardsautonomy/ai-trader.git && cd ai-trader
cp backend/.env.example backend/.env        # defaults are safe: paper mode
./scripts/trader start --demo                        # synthetic market, clock ignored: trades within seconds
```

`start` installs dependencies and builds the UI on first run (about a minute), then prints the terminal's URL
(`http://<this-machine>:3400`). The DEMO badge means nothing is real. `./scripts/trader stop` stops both halves.

## 3. Give the agents a real model

Local, with Ollama running:

```bash
./scripts/trader llm pull qwen3.8:27b-q8_0
# 16K context instead of the default 262K, which would fill a 48 GB card:
printf 'FROM qwen3.8:27b-q8_0\nPARAMETER num_ctx 16384\n' > /tmp/Modelfile && ollama create qwen3.8:27b-q8-16k -f /tmp/Modelfile
./scripts/trader llm test local/qwen3.8:27b-q8-16k NVDA   # one real deliberation: every agent's answer, timings, unusable answers
```

Hosted: put `AIT_OPENROUTER_API_KEY=...` in `backend/.env` and use model names like `anthropic/claude-sonnet-5`.

A good `llm test` has zero unusable answers and specialists that disagree for concrete reasons. If every specialist
paraphrases the regime and repeats one confidence, the model is too weak for this desk.

## 4. Connect Robinhood (read-only so far)

```bash
./scripts/trader robinhood login      # prints a URL; approve in the browser
```

If the browser is on another machine, the redirect to `http://127.0.0.1:8765/callback?...` fails to load. That is
expected: copy the whole URL from the address bar, paste it into the terminal, press Enter.

```bash
./scripts/trader robinhood discover   # lists the server's tools; checks the ones the adapter needs
./scripts/trader robinhood verify     # reads account, balance, positions, a quote, bars, an option chain,
                              # and sends one order to Robinhood's *simulator* (nothing is placed)
```

After verify passes, market data comes from Robinhood automatically. Details: [ROBINHOOD.md](ROBINHOOD.md).

## 5. Paper trade on real data

```bash
./scripts/trader start --data robinhood --llm local/qwen3.8:27b-q8-16k
```

Agents act only during the regular session (9:35 to 15:40 New York time by default) and everything is flat 10 minutes
before the close. Outside those hours the engine idles and makes no model calls. Watch it in the terminal:

- **TERMINAL**: equity, day P&L, limits, open positions, the market read, the live event stream and every decision.
- **HISTORY**: every closed trade with exact fills and P&L, and P&L per session.
- **SETUP**: mode, Robinhood connection, live readiness, which model each agent uses.

Click any decision or position for its full trace, down to the exact prompt each agent saw. See
[DASHBOARD.md](DASHBOARD.md).

## 6. Going live (real money)

Do this only after paper results on real data convince you, and only with money you can lose.

1. Fund the Robinhood **Agentic** account with a small amount (in the Robinhood app).
2. Open **SETUP**. Every blocking check in LIVE READINESS must be green.
3. Press **SWITCH TO LIVE** (in the header, or on SETUP). On the server run `./scripts/trader live-code` for a one-time code, type
   it and the confirmation phrase. The engine restarts in live mode.

Switching back to paper is a button too, refused while live positions are open. Safety design:
[RISK_AND_SAFETY.md](RISK_AND_SAFETY.md).

## Everyday commands

```bash
./scripts/trader status            # processes, equity, kill state
./scripts/trader logs -f           # follow both logs
./scripts/trader kill              # KILL SWITCH: halt new entries (works even if the API is wedged)
./scripts/trader kill --flatten    # halt and sell everything
./scripts/trader rearm             # re-enable trading (asks for REARM)
./scripts/trader restart [flags]   # same flags as start
./scripts/trader doctor            # configuration check
./scripts/trader test              # backend tests + frontend lint/typecheck
```

`start` flags: `--demo`, `--fresh` (demo only: wipe demo data), `--data robinhood|yfinance|auto`, `--llm <model>`,
`--local` (bind to 127.0.0.1 only), `--dev` (UI hot reload), `--rebuild` (fresh UI build).

The terminal binds to all interfaces so you can open it from another machine on your network. It is not meant for the
public internet: use `--local` plus an SSH tunnel if the network is not trusted.
