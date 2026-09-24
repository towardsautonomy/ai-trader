"""Configuration: secrets and mode from .env, tunables from config.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
LIVE_CONFIRM_PHRASE = "I-ACCEPT-REAL-MONEY-RISK"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_prefix="AIT_", extra="ignore"
    )

    mode: Literal["paper", "live"] = "paper"
    mode_source: str = "env"  # "dashboard" when data/mode.json chose it
    live_confirm: str = ""
    # auto = robinhood once logged in and `robinhood verify` passed, else yfinance. synthetic is for demos/tests.
    data_source: Literal["auto", "robinhood", "yfinance", "synthetic"] = "auto"
    ignore_clock: bool = False  # honoured only for paper + synthetic
    openrouter_api_key: str = ""
    # OpenAI-compatible local server (Ollama: http://127.0.0.1:11434/v1). Agents whose model
    # is written `local/<name>` in config.yaml run here; nothing leaves the machine for them.
    local_llm_url: str = ""
    local_llm_api_key: str = ""
    local_llm_timeout: int = 120
    # Thinking for local reasoning models (Qwen3.x etc.), sent as `reasoning_effort`:
    # none | low | medium | high, or empty to leave it to the server. Thinking is slow on
    # local hardware (a 27B model writes ~20 tokens/s) and needs a bigger reply budget.
    local_llm_thinking: str = "none"
    # If set, every agent uses this one model, overriding config.yaml (e.g. AIT_MODEL=local/qwen2.5:32b).
    model: str = ""
    api_token: str = ""  # if set, mutating endpoints require it
    host: str = "127.0.0.1"
    port: int = 8400
    # Extra browser origins allowed to call the API (comma-separated), e.g. the LAN
    # address the terminal is opened from. localhost is always allowed.
    cors_origins: str = ""
    data_dir: Path = BACKEND_DIR / "data"
    config_file: Path = BACKEND_DIR / "config.yaml"
    robinhood_mcp_url: str = "https://agent.robinhood.com/mcp/trading"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'ai_trader.db'}"

    @property
    def live_armed(self) -> bool:
        return self.mode == "live" and self.live_confirm == LIVE_CONFIRM_PHRASE


class UniverseCfg(BaseModel):
    symbols: list[str]
    regime_symbols: list[str] = ["SPY", "QQQ"]
    min_price: float = 5.0
    max_spread_pct: float = 0.15


class EngineCfg(BaseModel):
    cycle_seconds: int = 120
    exit_poll_seconds: int = 5
    watchdog_seconds: int = 10
    max_candidates_per_cycle: int = 5
    parallel_candidates: int = 3   # deliberated at once; 1 suits a local server that runs one request at a time
    scout_table_rows: int = 40
    position_review_seconds: int = 120
    bar_interval: str = "5m"
    bar_lookback: int = 78


class SessionCfg(BaseModel):
    no_entry_first_minutes: int = 5
    no_entry_last_minutes: int = 20
    flatten_before_close_minutes: int = 10
    hold_overnight: bool = False


class RiskCfg(BaseModel):
    max_risk_per_trade_pct: float = 0.5
    max_position_notional_pct: float = 25
    max_option_premium_pct: float = 1.0
    max_total_exposure_pct: float = 100
    max_open_positions: int = 8
    max_positions_per_symbol: int = 1
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 6.0
    max_orders_per_minute: int = 12
    max_trades_per_day: int = 40
    max_day_trades_5d: int = 0
    max_quote_age_seconds: int = 20
    max_llm_spend_per_day_usd: float = 15.0


class EquityExitCfg(BaseModel):
    """Hard backstops; the agents choose the actual levels inside these."""

    max_stop_pct: float = 3.0
    take_profit_ceiling_pct: float = 8.0
    max_hold_minutes: int = 240


class OptionsCfg(BaseModel):
    enabled: bool = True
    allowed_strategies: list[str] = ["long_call", "long_put"]
    min_dte: int = 5
    max_dte: int = 35
    target_delta: float = 0.50
    delta_tolerance: float = 0.20
    max_spread_pct: float = 8.0
    min_open_interest: int = 200
    min_volume: int = 50
    shortlist_size: int = 6
    stop_loss_pct: float = 35
    take_profit_ceiling_pct: float = 100
    exit_at_dte: int = 2
    max_hold_minutes: int = 300


class ExecutionCfg(BaseModel):
    entry_limit_offset_bps: float = 5
    option_entry_offset_pct: float = 1.5
    entry_timeout_seconds: int = 20
    exit_limit_offset_bps: float = 15
    option_exit_offset_pct: float = 1.5
    exit_timeout_seconds: int = 8
    exit_max_reprices: int = 4


class PaperCfg(BaseModel):
    starting_cash: float = 100_000
    slippage_bps: float = 2
    option_slippage_pct: float = 1.0
    commission_per_contract: float = 0.03


class AgentsCfg(BaseModel):
    default_model: str = "anthropic/claude-haiku-4.5"
    models: dict[str, str] = {}
    timeout_seconds: int = 40
    # Per-agent thinking for local reasoning models (none|low|medium|high); agents not listed
    # use AIT_LOCAL_LLM_THINKING. Thinking agents get a bigger reply budget and a longer timeout.
    thinking: dict[str, str] = {}
    specialists: list[str] = ["momentum", "mean_reversion", "volatility"]
    lessons_in_prompt: int = 6
    show_agent_track_record: bool = True

    def model_for(self, agent: str) -> str:
        return self.models.get(agent, self.default_model)


class AppConfig(BaseModel):
    universe: UniverseCfg
    engine: EngineCfg = Field(default_factory=EngineCfg)
    session: SessionCfg = Field(default_factory=SessionCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    equity_exits: EquityExitCfg = Field(default_factory=EquityExitCfg)
    options: OptionsCfg = Field(default_factory=OptionsCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    paper: PaperCfg = Field(default_factory=PaperCfg)
    agents: AgentsCfg = Field(default_factory=AgentsCfg)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return apply_mode_choice(s)


def apply_mode_choice(s: Settings) -> Settings:
    """A paper/live choice made in the dashboard overrides AIT_MODE (see app/modeswitch.py)."""
    from app.modeswitch import read_choice
    choice = read_choice(s.data_dir)
    if choice and not s.ignore_clock:  # the demo never follows it
        s.mode = choice["mode"]
        s.live_confirm = choice.get("live_confirm", "") if s.mode == "live" else ""
        s.mode_source = "dashboard"
    return s


def load_config(path: Path | None = None, settings: Settings | None = None) -> AppConfig:
    settings = settings or get_settings()
    path = path or settings.config_file
    cfg = AppConfig.model_validate(yaml.safe_load(path.read_text()))
    if settings.model:
        cfg.agents.default_model, cfg.agents.models = settings.model, {}
    return cfg
