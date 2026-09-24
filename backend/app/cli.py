"""Command line: run the engine, operate the kill switch, set up Robinhood."""

from __future__ import annotations

import asyncio
import json
import logging

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings, load_config

app = typer.Typer(no_args_is_help=True, add_completion=False, help="AI Trader")
rh = typer.Typer(no_args_is_help=True, help="Robinhood MCP setup")
app.add_typer(rh, name="robinhood")
console = Console()


@app.command()
def run(host: str = "", port: int = 0):
    """Start the engine and the API. Agents act only inside the trading window."""
    import uvicorn

    from app.api.server import create_app
    from app.runtime import StartupRefused

    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    console.print(f"[bold]AI Trader[/bold] mode=[{'red' if s.mode == 'live' else 'green'}]{s.mode}[/] data={s.data_source}")
    try:
        uvicorn.run(create_app(), host=host or s.host, port=port or s.port, log_level="warning")
    except StartupRefused as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(2)


@app.command("live-code")
def live_code():
    """Print a one-time code (valid 10 minutes) that the dashboard asks for when switching to live."""
    from app.modeswitch import issue_code
    code = issue_code(get_settings().data_dir)
    console.print(f"one-time code for switching to LIVE in the dashboard: [bold]{code}[/bold] (valid 10 minutes, single use)")


@app.command()
def kill(flatten: bool = typer.Option(False, "--flatten", help="also liquidate every position"),
         reason: str = "manual (cli)"):
    """Engage the kill switch. Works even if the server is wedged or down: it drops a
    marker file the watchdog picks up within seconds, and that a restart honours."""
    s = get_settings()
    marker = s.data_dir / ("KILL_FLATTEN" if flatten else "KILL")
    marker.write_text(reason)
    console.print(f"[bold red]KILL SWITCH -> {'FLATTEN' if flatten else 'HALT'}[/bold red] ({marker})")
    try:  # also tell a running server right away
        import httpx
        r = httpx.post(f"http://{s.host}:{s.port}/api/kill", json={"level": "flatten" if flatten else "halt", "reason": reason},
                       headers={"X-API-Token": s.api_token}, timeout=3)
        console.print(f"server acknowledged: {r.json()['kill']['state']}")
    except Exception:
        console.print("server not reachable; the marker file will be honoured by the watchdog or at next start")


@app.command()
def rearm(confirm: str = typer.Option(..., prompt="Type REARM to re-enable trading")):
    """Re-arm after a kill. Always a deliberate, manual act."""
    import httpx

    s = get_settings()
    try:
        r = httpx.post(f"http://{s.host}:{s.port}/api/kill/rearm", json={"confirm": confirm},
                       headers={"X-API-Token": s.api_token}, timeout=5)
        console.print(r.json())
    except httpx.HTTPError:
        if confirm != "REARM":
            raise typer.Exit(1)
        for name in ("KILL", "KILL_FLATTEN"):
            (s.data_dir / name).unlink(missing_ok=True)

        async def clear():
            from app.core.events import EventLog
            from app.core.killswitch import KillSwitch
            from app.db.session import Database
            db = Database(s.db_url)
            await db.init()
            ks = KillSwitch(db, EventLog(db), s.data_dir)
            await ks.load()
            await ks.rearm("REARM", "cli (server down)")
            await db.close()

        asyncio.run(clear())
        console.print("server not running; kill state cleared on disk")


@app.command()
def status():
    """Show what the running engine is doing."""
    import httpx

    s = get_settings()
    st = httpx.get(f"http://{s.host}:{s.port}/api/status", timeout=5).json()
    t = Table(show_header=False)
    a = st["account"]
    for k, v in [("mode", st["mode"]), ("kill switch", st["kill"]["state"].upper()), ("session", st["session"]["phase"]),
                 ("data / broker / llm", f"{st['data_source']} / {st['broker']} / {st['llm']}"),
                 ("equity", a.get("equity")), ("day P&L", f"{a.get('day_pnl')} ({a.get('day_pnl_pct')}%)"),
                 ("open positions", a.get("open_positions")), ("LLM spend today", a.get("llm_spend_today")),
                 ("regime", st["regime"].get("label")), ("last cycle", json.dumps(st["last_cycle"]))]:
        t.add_row(k, str(v))
    console.print(t)


# ---------------------------------------------------------------------- llm
llm_app = typer.Typer(no_args_is_help=True, help="Model providers")
app.add_typer(llm_app, name="llm")


@llm_app.command("test")
def llm_test(symbol: str = "NVDA", model: str = typer.Option("", help="use this model for every agent, e.g. local/llama3"),
             synthetic: bool = typer.Option(False, help="synthetic data instead of live yfinance bars")):
    """Run one full deliberation (regime, scout, specialists, skeptic, portfolio manager) on real
    market data through the configured models, and print what each agent said, with timings.
    Places no orders, touches no trading state."""
    import os
    if model:
        os.environ["AIT_MODEL"] = model
        get_settings.cache_clear()
    s = get_settings()
    cfg = load_config(settings=s)
    from app.runtime import build_llm, unresolved_models
    if unresolved_models(s, cfg):
        console.print(f"[red]no provider for {unresolved_models(s, cfg)}; set AIT_LOCAL_LLM_URL or AIT_OPENROUTER_API_KEY[/red]")
        raise typer.Exit(1)

    async def go():
        import tempfile, time
        from app.agents.swarm import Swarm
        from app.db.session import Database
        from app.engine.planner import build_shortlist
        from app.marketdata.synthetic import SyntheticData
        from app.marketdata.yahoo import YahooData
        from app.scanner.scanner import build_table
        db = Database(f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/llm_test.db")
        await db.init()
        llm, label = build_llm(s, cfg, db)
        swarm = Swarm(llm, cfg.agents)
        console.print(f"provider [bold]{label}[/bold]; models: " + ", ".join(sorted({cfg.agents.model_for(a) for a in ("scout", "momentum", "portfolio_manager")})))
        data = SyntheticData([symbol, "SPY", "QQQ"]) if synthetic else YahooData()
        syms = [symbol, "SPY", "QQQ"]
        cfg.universe.max_spread_pct = 5
        bars, quotes = await data.get_bars(syms, "5m", 78), await data.get_quotes(syms)
        table = build_table(bars, quotes, cfg.universe)
        if not any(c.symbol == symbol for c in table):
            console.print(f"[red]no data for {symbol}[/red]"); return
        t0 = time.monotonic()
        regime, r = await swarm.regime([c for c in table if c.symbol in ("SPY", "QQQ")], {"symbols": len(table), "pct_above_vwap": 50})
        console.print(f"\n[cyan]regime[/cyan] ({r.latency_ms/1000:.1f}s) {regime['label']} / {regime['bias']} {regime['confidence']}: {regime['summary']}" + (f" [red]ERROR {r.error}[/red]" if r.error else ""))
        picks, note, r = await swarm.scout(table, open_symbols=[], recently_closed=[], regime=regime, max_picks=2, minutes_to_close=180)
        console.print(f"[cyan]scout[/cyan] ({r.latency_ms/1000:.1f}s) picked {[c.symbol for c in picks]}: " + "; ".join(c.scout_reason for c in picks) + (f" [red]ERROR {r.error}[/red]" if r.error else ""))
        cand = next((c for c in picks if c.symbol == symbol), next(c for c in table if c.symbol == symbol))
        shortlist = []
        try:
            shortlist = build_shortlist(await data.get_option_chain(symbol, cfg.options.min_dte, cfg.options.max_dte), cfg, max_premium_usd=1000)
        except Exception as e:
            console.print(f"[yellow]no option chain: {e}[/yellow]")
        ctx = {"portfolio": {"equity": 100000, "day_pnl_pct": 0.0, "exposure_pct": 0.0, "open_positions": [], "trades_today": 0,
                             "consecutive_losses": 0, "recent_closed": []}, "track_record": {}, "lessons": [],
               "envelope": {"shares": {"max_stop_pct_from_entry": cfg.equity_exits.max_stop_pct, "full_size_risk_usd": 500, "max_position_usd": 25000},
                            "options": {"enabled": cfg.options.enabled, "full_size_premium_usd": 1000}, "position_slots_left": 8,
                            "note": "Short stock and short options do not exist here."}}
        opinions, pm, err = await swarm.deliberate(cand, regime=regime, shortlist=shortlist, pm_context=ctx, minutes_to_close=180, decision_id="llm-test")
        for o in opinions:
            tag = f"[red]ERROR {o.error}[/red]" if o.error else ""
            console.print(f"[cyan]{o.agent:16}[/cyan] ({o.latency_ms/1000:.1f}s) {o.stance.value:8} {o.confidence:.2f}  {o.thesis} {tag}")
            for e in o.evidence[:3]:
                console.print(f"                   - {e}")
        console.print(f"[cyan]portfolio_manager[/cyan] {pm.action.upper()} " + (f"{pm.direction} via {pm.vehicle.value if pm.vehicle else '?'} stop {pm.stop_price} target {pm.target_price} size {pm.size_fraction} conf {pm.confidence}" if pm.action == "enter" else f"conf {pm.confidence}")
                      + f"\n                   {pm.summary}" + (f"\n                   [red]PM ERROR: {err}[/red]" if err else ""))
        total = time.monotonic() - t0
        from sqlalchemy import select, func
        from app.db.models import LLMCall
        async with db.session() as ses:
            n, cost = (await ses.execute(select(func.count(), func.sum(LLMCall.cost_usd)))).one()
        # a "failure" is any answer the desk could not use: transport errors, bad JSON, and copied examples alike
        errs = sum(1 for o in opinions if o.error) + int(bool(err)) + int(bool(regime.get("label") == "unknown")) + int("discarded" in (r.error or ""))
        console.print(f"\n{n} model calls, {errs} unusable answers, ${cost or 0:.4f}, {total:.1f}s wall for one candidate "
                      f"(a live cycle deliberates up to {cfg.engine.max_candidates_per_cycle} candidates, {cfg.engine.parallel_candidates} at a time)")
        if hasattr(llm, "aclose"):
            await llm.aclose()
        await db.close()
        return int(errs or 0)

    failed = asyncio.run(go())
    if failed:
        raise typer.Exit(1)


@llm_app.command("models")
def llm_models():
    """List the models the local server offers."""
    import httpx
    s = get_settings()
    if not s.local_llm_url:
        console.print("AIT_LOCAL_LLM_URL is not set"); raise typer.Exit(1)
    for m in httpx.get(s.local_llm_url.rstrip("/") + "/models", timeout=5).json()["data"]:
        console.print(f"  local/{m['id']}")


# ---------------------------------------------------------------------- robinhood
def _mcp(interactive: bool = False):
    from app.broker.robinhood_mcp import RobinhoodMCP
    s = get_settings()
    return RobinhoodMCP(s.robinhood_mcp_url, s.data_dir, interactive=interactive)


@rh.command()
def login():
    """One-time browser login to Robinhood's agentic-trading MCP server."""
    async def go():
        m = _mcp(interactive=True)
        tools = await m.list_tools()
        await m.close()
        return tools

    tools = asyncio.run(go())
    console.print(f"[green]logged in[/green]; the server exposes {len(tools)} tools. Next: `ait robinhood discover`")


@rh.command()
def discover():
    """Dump the server's real tool schemas to data/robinhood_tools.json and check the ones this adapter uses."""
    from app.broker.robinhood_mcp import USED_TOOLS

    async def go():
        m = _mcp()
        tools = await m.list_tools()
        await m.close()
        return tools

    s = get_settings()
    tools = asyncio.run(go())
    (s.data_dir / "robinhood_tools.json").write_text(json.dumps(tools, indent=2))
    names = {t["name"] for t in tools}
    for t in tools:
        mark = "[green]used[/green] " if t["name"] in USED_TOOLS else "     "
        console.print(f"  {mark}[cyan]{t['name']}[/cyan]  {(t['description'] or '')[:90]}")
    missing = [n for n in USED_TOOLS if n not in names]
    if missing:
        console.print(f"[red]the adapter needs tools the server does not expose: {missing}[/red]")
        raise typer.Exit(1)
    console.print(f"\nall {len(USED_TOOLS)} tools the adapter uses are present. Next: `ait robinhood verify`")


@rh.command()
def verify(symbol: str = "SPY"):
    """Exercise every read path plus a SIMULATED order (review_equity_order). Places no orders.
    Pins the schemas of the tools used; live mode requires this to pass against the current server."""
    from app.broker.robinhood_mcp import RobinhoodBroker, schema_digest
    from app.core.types import Instrument, OrderReason, OrderRequest, Side, utcnow
    from app.db.session import Database
    from app.runtime import VERIFY_KEY

    s, cfg = get_settings(), load_config()

    def line(ok: bool, label: str, text: str) -> bool:
        console.print(f"{'[green]OK[/green]  ' if ok else '[red]FAIL[/red]'} {label:10} {text}")
        return ok

    async def go() -> bool:
        m = _mcp()
        b = RobinhoodBroker(m)
        ok = True
        try:
            digest = schema_digest(await m.list_tools())
            ok &= line(True, "tools", "all tools the adapter uses are present")
            a = await b.agentic_account()
            ok &= line(True, "account", f"agentic account ...{a['account_number'][-4:]} ({a.get('type')}, {a.get('option_level') or 'no options'})")
            line(await b.options_allowed(), "options", "long calls/puts allowed" if await b.options_allowed() else "options level < 2: live will trade shares only")
            acct = await b.get_account()
            ok &= line(acct.equity >= 0 and acct.cash >= 0, "balance", f"equity {acct.equity:,.2f} cash {acct.cash:,.2f} buying power {acct.buying_power:,.2f}")
            pos = await b.get_positions()
            ok &= line(True, "positions", str([(p.instrument.key, p.qty) for p in pos]) + ("  (live expects an empty account at first start)" if pos else ""))
            q = (await b.get_quotes([symbol]))[symbol]
            ok &= line(0 < q.bid <= q.ask, "quote", f"{symbol} bid {q.bid} ask {q.ask} last {q.last} age {q.age_seconds:.0f}s")
            bars = (await b.get_bars([symbol], cfg.engine.bar_interval, cfg.engine.bar_lookback)).get(symbol, [])
            ok &= line(len(bars) > 0, "bars", f"{len(bars)} x {cfg.engine.bar_interval}, last {bars[-1].ts:%Y-%m-%d %H:%M}Z close {bars[-1].close}" if bars else "none")
            chain = await b.get_option_chain(symbol, cfg.options.min_dte, cfg.options.max_dte)
            c = min(chain, key=lambda c: abs((c.delta or 0) - 0.5), default=None)
            ok &= line(bool(chain), "options", f"{len(chain)} contracts near the money; e.g. {c.instrument.key} bid {c.bid} ask {c.ask} delta {c.delta} OI {c.open_interest}" if c else "no contracts")
            # the order format, checked by Robinhood's own simulator: 1 share far below the market
            req = OrderRequest(client_order_id="verify", instrument=Instrument(symbol=symbol), side=Side.BUY, qty=1,
                               limit_price=round(q.mid * 0.5, 2), reason=OrderReason.ENTRY, opens_position=True)
            review = await b._data("review_equity_order", await b.equity_order_args(req))
            checks = review.get("order_checks") if isinstance(review, dict) else None
            checks = checks if isinstance(checks, list) else [checks] if checks else []
            alerts = [c.get("alertType", c) if isinstance(c, dict) else c for c in checks]
            ok &= line(isinstance(review, dict) and review.get("symbol") == symbol, "order fmt",
                       f"Robinhood's simulator accepted a 1-share limit (not placed); its alerts: {alerts or 'none'}")
        except Exception as e:
            ok = line(False, "error", f"{type(e).__name__}: {e}")
        finally:
            await m.close()
        if ok:
            db = Database(s.db_url)
            await db.init()
            await db.kv_set(VERIFY_KEY, {"digest": digest, "at": utcnow().isoformat()})
            await db.close()
        return ok

    if asyncio.run(go()):
        console.print("[green]verified.[/green] Real order placement is only proven by a real order: start live with a "
                      "small Agentic balance and watch the first entry and exit.")
    else:
        raise typer.Exit(1)


def _verified_at(s) -> str:
    from app.db.session import Database
    from app.runtime import VERIFY_KEY

    async def go():
        db = Database(s.db_url)
        await db.init()
        v = await db.kv_get(VERIFY_KEY)
        await db.close()
        return v
    v = asyncio.run(go())
    return f"yes ({v.get('at', '')[:16]})" if v else ""


@app.command()
def doctor():
    """Check configuration without starting anything."""
    s, cfg = get_settings(), load_config()
    from app.agents.swarm import AGENTS, system_prompt
    from app.runtime import unresolved_models
    local = "not configured"
    if s.local_llm_url:
        try:
            import httpx
            names = [m["id"] for m in httpx.get(s.local_llm_url.rstrip("/") + "/models", timeout=3).json()["data"]]
            local = f"{s.local_llm_url} ({len(names)} models: {', '.join(names[:6])}{'...' if len(names) > 6 else ''})"
        except Exception as e:
            local = f"{s.local_llm_url} UNREACHABLE ({type(e).__name__})"
    bad = unresolved_models(s, cfg)
    rows = [
        ("mode", s.mode), ("data_source", s.data_source),
        ("OpenRouter key", "set" if s.openrouter_api_key else "not set"),
        ("local LLM", local),
        ("agent models", "all resolve" if not bad else ("ALL OFFLINE (no provider configured)" if len(bad) == len(AGENTS) and not s.openrouter_api_key and not s.local_llm_url
                         else "NO PROVIDER for " + ", ".join(f"{a}={m}" for a, m in bad.items()))),
        ("Robinhood login", "yes" if _mcp().logged_in else "no"),
        ("Robinhood verified", _verified_at(s) or "no (run `ait robinhood verify`)"),
        ("universe", f"{len(cfg.universe.symbols)} symbols"), ("prompts", f"{sum(bool(system_prompt(a)) for a in AGENTS)}/{len(AGENTS)} load"),
        ("kill markers", ", ".join(n for n in ("KILL", "KILL_FLATTEN") if (s.data_dir / n).exists()) or "none"),
    ]
    t = Table(show_header=False)
    for r in rows:
        t.add_row(*r)
    console.print(t)


if __name__ == "__main__":
    app()
