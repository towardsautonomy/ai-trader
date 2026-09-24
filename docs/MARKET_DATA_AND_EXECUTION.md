# Market data and execution

## Sources

`AIT_DATA_SOURCE` or `./scripts/trader start --data ...`:

| Source | Used for | Notes |
|---|---|---|
| `robinhood` | real quotes, 5-minute bars, option chains with greeks, news, earnings | needs login + verify; the default (`auto`) once verified |
| `yfinance` | fallback when Robinhood is not set up | unofficial, sometimes delayed; empty option bids outside market hours |
| `synthetic` | the demo | a made-up market; the clock may be ignored |

Robinhood specifics (`app/broker/robinhood_mcp.py`): quotes in batches of 50; bars in batches of 10 symbols,
regular-hours, synthesized ("interpolated") bars dropped; options from the standard chain, the four nearest expiries in
the allowed days-to-expiry window, 15 strikes each side of spot within ±10%; news from the last 36 hours; one
market-wide earnings calendar call per day.

## From candidate to order

**Option shortlist** (`build_shortlist`): tradability, not selection. A contract qualifies if it has a two-sided market,
spread ≤ 8% of mid, open interest ≥ 200, volume ≥ 50 or open interest ≥ 1,000 (volume restarts at zero each morning),
5 to 35 days to expiry, delta within 0.3 to 0.7, and one contract costs no more than the premium cap at the entry limit.
Up to three calls and three puts nearest delta 0.5 are offered; the portfolio manager picks.

**Planner** (`build_proposal`): checks the plan can express the thesis (no short stock, no short options; long puts for
bearish views), that stop and target bracket the price, then sizes. Shares: size so the loss at the stop is the chosen
fraction of the 0.5% risk budget, capped by position and cash limits. Options: whole contracts within the chosen
fraction of the premium budget. Every clamp is logged; plans that cannot be built are discarded with the reason.

**Executor**: entries are marketable limits a little through the ask and are cancelled, not chased, if unfilled after
20 s. Exits reprice more aggressively on each attempt. Robinhood orders carry an idempotency key derived from the
order id, so a retry after a network error never duplicates an order; option prices are moved onto the contract's tick.

## Paper broker

Real quotes in, deliberately pessimistic fills out (`app/broker/paper.py`): buys fill at the ask plus slippage, sells at
the bid minus slippage (0.02% for shares, 1% for options), rounded onto a real exchange tick against us and never past
the limit, plus $0.03 per option contract. A passive limit rests until the market reaches it. Sells can never open a
short. The paper book persists across restarts.

Robinhood has no paper environment, so paper trading cannot run on Robinhood's side. Its order simulator
(`review_equity_order`) is used by `verify` to check the order format without placing anything.
