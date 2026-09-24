# ai_trader terminal

Monitoring and control console for the AI trading engine in `../backend`. One
operator, localhost, real money at stake: it shows what the engine is doing
live, lets you trace the full reasoning behind every action, and carries the
kill switch on every page.

Next.js 15 (App Router) + TypeScript (strict) + Tailwind CSS. All data is
fetched client-side with TanStack Query (polling) plus one WebSocket for the
live event feed. Recharts draws the equity curve. There is no auth and no
server-side data fetching.

## Run

```bash
# 1. a backend to talk to. Synthetic market + offline agents, trades within seconds:
cd ../backend
AIT_DATA_SOURCE=synthetic AIT_IGNORE_CLOCK=true AIT_PORT=8400 ./ait run

# 2. the terminal
cd ../frontend
npm install
cp .env.example .env.local   # optional, defaults work for a local backend
npm run dev                  # http://localhost:3400
```

| script              | what it does                          |
| ------------------- | ------------------------------------- |
| `npm run dev`       | dev server on port 3400               |
| `npm run build`     | production build (type-checks, lints) |
| `npm run start`     | serve the production build on 3400    |
| `npm run lint`      | ESLint (next/core-web-vitals + TS)    |
| `npm run typecheck` | `tsc --noEmit`                        |

Open it on `localhost` or `127.0.0.1`: the backend's CORS policy only allows
those origins.

## Environment

| variable                | default                 | purpose                                                                                                   |
| ----------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------- |
| `NEXT_PUBLIC_API_URL`   | `http://127.0.0.1:8400` | Backend base URL. The WebSocket URL is derived from it (`ws://.../ws`).                                   |
| `NEXT_PUBLIC_API_TOKEN` | empty                   | Sent as `X-API-Token` on mutating calls (kill, re-arm, manual close) when the backend has a token set.    |

`NEXT_PUBLIC_*` values are compiled into the browser bundle, so the token is
visible to anything that can load the page. That is acceptable for a
localhost console and nothing else.

## Screens

- **`/` terminal**: status bar (mode, equity, day P&L, drawdown, exposure,
  session phase with countdown, data/broker/LLM source, usage-vs-limit meters,
  a loud amber warning when the LLM is `offline-heuristic`), open positions
  with manual close, live event stream, decision feed with per-agent stance
  chips, market/regime panel, equity curve and performance stats.
- **`/decisions/[id]` decision trace**: the reasoning chain as a timeline:
  scout -> candidate signals + recent closes -> regime -> each agent's opinion
  -> the context the PM saw (collapsed) -> the option shortlist the PM was
  offered, with the chosen row highlighted -> PM plan, with `contract_index`
  resolved to the actual contract -> proposal with code clamps in amber ->
  risk verdict as a pass/fail checklist -> orders -> result -> events -> every
  LLM call with its exact prompt and raw response. Decisions recorded before
  the shortlist was captured still render and say so.
- **`/positions/[id]` position trace**: P&L header, levels (initial stop vs
  current stop, so stop movement is visible), exit reason, position-manager
  reviews (requested vs applied, with code refusals in amber), orders, the
  lesson written afterwards, and the embedded decision trace. Every review and
  the lesson have the same exact prompt/response viewer as the agent opinions
  (the i-th `position_manager` call pairs with the i-th review; if the counts
  ever differ the calls are listed separately rather than mis-attributed).
- **`/agents`**: per-agent model, all-time track record, usage over a rolling
  18 h window (not a calendar day), plus the lessons fed back to the agents.
- **`/log`**: full event history, paged backwards with `before_id`. Message
  search, kind prefix and level are applied by the server, so they cover all
  history and not just the loaded rows. New events are tailed with `after_id`.
  The orders table is on the second tab.

Performance stats on `/` are all-time for the current mode (the API has no
date filter).

## Kill switch

Always in the top-right corner, on every page. `Shift+K` opens it, `Esc` closes it.

- **HALT** (stop new entries): click `HALT`, then `CONFIRM HALT` in the panel.
- **FLATTEN** (halt and sell everything): type `FLATTEN`, then `EXECUTE FLATTEN`.
- While engaged, a red banner on every page shows state, reason, source and
  time. **RE-ARM** requires typing `REARM`.
- Every mutation reports its outcome in a strip under the header. Failures
  stay until dismissed.

If the backend is unreachable the UI says `BACKEND UNREACHABLE` and cannot
stop anything. Use the fallback, which works even when the server is down:

```bash
cd ../backend && ./ait kill --flatten
```

## Engine liveness

An API that answers is not proof the engine is trading safely. The status bar
shows seconds since each engine loop last ticked against its interval
(`ENGINE entry 41s/120s exit 1s/5s review 41s/120s watchdog 1s/10s`). A red
`ENGINE MAY BE WEDGED` banner appears on every page, with the same CLI
fallback, when any loop is overdue or when the `exit` or `watchdog` loop
reports an error: those are the loops that enforce stops and the kill switch.
An error on the `entry` or `review` loop alone shows in amber in the status
bar without the banner. When the API runs without an engine (`engine: {}`)
both stay hidden.

## Manual close

`CLOSE` on a position row (or on the position page), then confirm. The backend
answers 200 `closing`, 409 if the position is no longer open, or 404 for an
unknown id; 409/404 are reported as `REFUSED, no order was sent`.

## Behaviour worth knowing

- **Polling**: status and open positions every 2 s, decisions and open traces
  every 5 s, equity/stats/agents/orders every 15 s, lessons every 30 s. Failed
  polls are not retried silently: a panel that already has data keeps showing
  it under a red `STALE` marker instead of going blank.
- **Event stream**: one WebSocket for the whole app, reconnecting with backoff
  (1 s up to 10 s). On every (re)connect, and whenever an id gap shows the
  server dropped frames, the missing events are backfilled from `/api/events`.
  Hovering the stream pauses auto-scroll.
- **Session countdown** ticks locally between polls from the server's own
  `server_time`, so a skewed browser clock does not distort it.
- **Scanlines** are a ~3% opacity overlay; toggle them in the header
  (remembered in `localStorage`).
- Timestamps are shown in the browser's local time zone.

## Layout

```
src/
  app/                  routes: /, /decisions/[id], /positions/[id], /agents, /log
  components/
    shell/              header, kill switch, engaged banner, engine-wedged banner, notices, backend-down banner
    dashboard/          the panels on /
    trace/              decision + position trace views, LLM call viewer, sparkline, levels bar
    ui/                 Panel, query states, meter, key/value, collapsible, JSON/text blocks
  hooks/                polling queries, WebSocket event stream, notices, ticking clock, debounce
  lib/
    types.ts            API response types, derived from docs/api_samples
    api.ts              the only module that talks to the backend
    engine.ts           the rule for when the engine counts as wedged
    format.ts           number/time formatting and colour rules
screenshots/            captured against the synthetic backend (1440 px and 1024 px)
```
