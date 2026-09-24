# Connecting Robinhood

Robinhood's agentic-trading MCP server (`https://agent.robinhood.com/mcp/trading`) is a standard MCP server over
streamable HTTP with OAuth 2.1 (dynamic client registration, PKCE, refresh tokens; verified by probing its
`.well-known` metadata). The engine connects to it directly as an MCP client; Claude Code is not in the loop.

There is **no sandbox**. Orders are real.

## 1. Login
`./trader robinhood login` prints an authorisation URL (and tries to open a browser). Approve in Robinhood; it then
redirects the browser to `http://127.0.0.1:8765/callback?code=...`.

- Browser on the same machine as the engine: the redirect completes the login by itself.
- Browser on another machine (the usual case for a headless box): that redirect fails to load, which is expected.
  Copy the full URL from the address bar and paste it into the terminal running `login`. Alternatively forward the
  port first (`ssh -L 8765:127.0.0.1:8765 user@box`) and the redirect completes on its own.

Tokens land in `backend/data/robinhood_tokens.json` (mode 600, git-ignored). Refresh is automatic. If the session is
ever revoked the engine reports it and you log in again.

## 2. Discover
`./trader robinhood discover` writes every tool the server exposes, with its input schema, to
`backend/data/robinhood_tools.json`, and checks that the tools the adapter uses are all present.

The adapter (`backend/app/broker/robinhood_mcp.py`) is written against the real tools, learned from the live server on
2026-09-23:

| Engine needs | Robinhood tools |
|---|---|
| account, balance | `get_accounts` (the one `agentic_allowed` account; anything else is refused), `get_portfolio` |
| positions | `get_equity_positions`, `get_option_positions` (merged; a short option never matches the ledger, so it halts) |
| quotes, 5m bars | `get_equity_quotes` (50 per call), `get_equity_historicals` (10 per call, `5minute`, regular hours) |
| options | `get_option_chains` (standard chain) -> `get_option_instruments` (4 nearest expiries in the DTE window, 15 strikes each side of spot) -> `get_option_quotes` (greeks, IV, OI) |
| orders | `place_equity_order` / `place_option_order` (limit, GFD, regular hours, UUID `ref_id` derived from our order id so retries never duplicate); `get_*_orders`, `cancel_*_order` |

Every reply is wrapped as `{"data": ..., "guide": ...}`; the adapter reads `data`. Option limit prices are moved onto the
contract's tick (e.g. $0.05 above $3): buys round up, sells round down.

## 3. Verify
`./trader robinhood verify` checks, without placing anything: the tools are present, exactly one agentic account exists and
its options level, balance, positions, a quote, 5m bars, a near-the-money option chain, and the order format through
Robinhood's own simulator (`review_equity_order`, 1 share far below the market). Passing pins a digest of the used tools'
input schemas. Live mode re-reads the schemas at every start and refuses if they changed since the last verify.

Once verified, paper mode (`data_source: auto`) also uses Robinhood's quotes, bars and option chains instead of yfinance.

Not provable without a real order: the shape of a placed order's reply, fills on option orders, and non-empty position rows
(the agentic account was empty on 2026-09-23). First live session: fund the Agentic account with a small amount, watch the
terminal, and confirm the first entry and exit round-trip cleanly (the ledger/broker reconciliation HALTs the engine on
any disagreement).

## Also available on the server, not used yet
News (`get_equity_news`), earnings (`get_earnings_calendar`, `get_earnings_results`), fundamentals, analyst ratings,
technical indicators, scans, and stop orders on stocks and options (sell-to-close `stop_market`), which would allow
broker-side protective stops.

## Guardrails on Robinhood's side
A dedicated Agentic account with its own budget, a notification per trade, and disconnect-anytime from the app. Those
are in addition to, not instead of, the envelope in `config.yaml`.
