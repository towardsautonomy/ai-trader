// Shapes of the backend API responses, derived from docs/api_samples/*.json
// and backend/app/api/server.py. Fields are not invented: if it is not here,
// the backend does not send it.

export type Mode = "paper" | "live";
export type KillState = "armed" | "halted" | "flatten";
export type SessionPhase = "closed" | "opening_buffer" | "open" | "closing_buffer" | "flatten";
export type EventLevel = "info" | "warn" | "error" | "critical";
export type Stance = "bullish" | "bearish" | "neutral";
export type DecisionStatus =
  | "deliberating"
  | "executed"
  | "skipped_by_pm"
  | "rejected_by_risk"
  | "unbuildable"
  | "unfilled"
  | "failed";
export type PositionStatus = "open" | "closing" | "closed";
export type KillLevel = "halt" | "flatten";

export interface KillSnapshot {
  state: KillState;
  reason: string;
  source: string;
  since: string | null;
}

export interface Account {
  equity: number;
  cash: number;
  day_start_equity: number;
  day_pnl: number;
  day_pnl_pct: number;
  drawdown_pct: number;
  exposure: number;
  exposure_pct: number;
  open_positions: number;
  trades_today: number;
  llm_spend_today: number;
}

export interface Session {
  is_open: boolean;
  phase: SessionPhase;
  entries_allowed: boolean;
  seconds_to_close: number | null;
  next_open: string | null;
}

export interface Limits {
  max_daily_loss_pct: number;
  max_drawdown_pct: number;
  max_open_positions: number;
  max_total_exposure_pct: number;
  max_trades_per_day: number;
  max_llm_spend_per_day_usd: number;
}

export interface Regime {
  label: string;
  bias: string;
  confidence: number;
  summary: string;
  what_works: string;
}

export interface LastCycle {
  ts: string;
  cycle_id: string;
  scanned: number;
  picked: string[];
  entered: number;
  seconds: number;
}

export interface LoopHeartbeat {
  seconds_since_tick: number;
  interval: number;
  overdue: boolean;
  last_error: string;
}

export interface AgentModel {
  agent: string;
  model: string;
  provider: "local" | "openrouter";
  /** Empty when the agent answers without thinking. */
  thinking: string;
  resolved: boolean;
}

export interface Status {
  mode: Mode;
  /** "dashboard" when the mode was chosen on the setup page, "env" when it came from .env. */
  mode_source?: string;
  /** Why the last dashboard switch to live was refused at startup (the engine fell back to paper). */
  live_refused?: string;
  /** Synthetic market and/or ignored clock: nothing here is real. */
  demo?: boolean;
  data_source: string;
  broker: string;
  llm: string;
  models?: AgentModel[];
  kill: KillSnapshot;
  /** Empty object when the broker is unreachable; see account_error. */
  account: Partial<Account>;
  account_error: string;
  session: Session;
  limits: Limits;
  /** Keyed by loop name (entry, exit, review, watchdog). Empty when the API runs without the engine, absent on older backends. */
  engine?: Record<string, LoopHeartbeat>;
  regime: Partial<Regime> | null;
  market_note: string | null;
  last_cycle: Partial<LastCycle> | null;
  server_time: string;
}

export interface Instrument {
  symbol: string;
  right: string | null;
  strike: number | null;
  expiry: string | null;
}

export interface Position {
  id: string;
  decision_id: string;
  mode: Mode;
  symbol: string;
  instrument: Instrument;
  instrument_key: string;
  direction: number;
  qty: number;
  entry_price: number;
  entry_ts: string;
  initial_stop: number;
  stop_price: number;
  target_price: number;
  und_stop: number;
  und_target: number;
  max_hold_minutes: number;
  thesis: string;
  invalidation: string;
  atr: number;
  high_water: number;
  low_water: number;
  last_price: number;
  risk_usd: number;
  status: PositionStatus;
  exit_price: number | null;
  exit_ts: string | null;
  exit_reason: string | null;
  exit_detail: string | null;
  realized_pnl: number;
  fees: number;
  unrealized_pnl: number;
  pnl_r: number;
  is_option: boolean;
}

export interface TraderEvent {
  id: number;
  ts: string;
  level: EventLevel;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  decision_id: string | null;
  position_id: string | null;
}

export interface StanceChip {
  agent: string;
  stance: Stance;
  confidence: number;
}

export interface DecisionSummary {
  id: string;
  ts: string;
  symbol: string;
  status: DecisionStatus;
  outcome: string | null;
  scout_reason: string | null;
  pm_action: string | null;
  pm_vehicle: string | null;
  pm_direction: string | null;
  pm_confidence: number | null;
  pm_summary: string | null;
  stances: StanceChip[];
}

export interface Quote {
  instrument: Instrument;
  bid: number;
  ask: number;
  last: number;
  ts: string;
}

export interface Candidate {
  symbol: string;
  quote: Quote;
  signals: Record<string, number>;
  recent: {
    closes: number[];
    rel_volume: number[];
    day_open: number;
    day_high: number;
    day_low: number;
  };
  atr: number;
  hint_score: number;
  hint_setups: string[];
  scout_reason: string;
}

export interface ShortlistContract {
  index: number;
  type: string;
  strike: number;
  expiry: string;
  dte: number;
  bid: number;
  ask: number;
  mid: number;
  spread_pct: number;
  delta: number;
  iv: number;
  open_interest: number;
  volume: number;
  /** Worst-case dollars for one contract at the entry limit. Absent on decisions recorded before it existed. */
  cost_per_contract_usd?: number;
}

export interface PMContext {
  portfolio: Record<string, unknown>;
  track_record: Record<string, unknown>;
  lessons: unknown[];
  envelope: Record<string, unknown>;
  /** The contracts the PM could choose from; pm.contract_index points into it. Absent on older decisions. */
  option_shortlist?: ShortlistContract[];
}

export interface PMPlan {
  action: string;
  direction: string;
  vehicle: string | null;
  contract_index: number | null;
  stop_price: number | null;
  target_price: number | null;
  size_fraction: number | null;
  max_hold_minutes: number | null;
  confidence: number;
  summary: string;
  key_risks: string[];
  invalidation: string;
}

export interface Proposal {
  symbol: string;
  instrument: Instrument;
  vehicle: string;
  side: string;
  qty: number;
  limit_price: number;
  stop_price: number;
  target_price: number;
  und_stop: number;
  und_target: number;
  risk_usd: number;
  notional_usd: number;
  quote_age_seconds: number;
  spread_pct: number;
  max_hold_minutes: number;
  clamps: string[];
}

export interface RiskCheck {
  rule: string;
  passed: boolean;
  detail: string;
}

export interface RiskVerdict {
  approved: boolean;
  checks: RiskCheck[];
}

export interface Decision {
  id: string;
  ts: string;
  cycle_id: string;
  mode: Mode;
  symbol: string;
  status: DecisionStatus;
  candidate: Candidate | null;
  regime: Partial<Regime> | null;
  scout_reason: string | null;
  context: Partial<PMContext> | null;
  pm: PMPlan | null;
  proposal: Proposal | null;
  risk: RiskVerdict | null;
  outcome: string | null;
}

export interface Opinion {
  id: number;
  decision_id: string;
  ts: string;
  agent: string;
  model: string;
  stance: Stance;
  confidence: number;
  thesis: string;
  evidence: string[];
  invalidation: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error: string;
}

export interface Order {
  id: string;
  ts: string;
  updated_ts: string;
  decision_id: string | null;
  position_id: string | null;
  mode: Mode;
  broker_order_id: string | null;
  instrument_key: string;
  instrument: Instrument;
  side: string;
  qty: number;
  limit_price: number | null;
  reason: string;
  status: string;
  filled_qty: number;
  avg_fill_price: number | null;
  message: string;
  /** Present on trace endpoints, stripped from GET /api/orders. */
  raw?: Record<string, unknown>;
}

export interface LLMCallMeta {
  id: number;
  ts: string;
  agent: string;
  model: string;
  decision_id: string | null;
  position_id: string | null;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error: string;
}

export interface LLMCall extends LLMCallMeta {
  prompt: string;
  response: string;
}

export interface DecisionTrace {
  decision: Decision;
  opinions: Opinion[];
  orders: Order[];
  events: TraderEvent[];
  position_id: string | null;
  llm_calls: LLMCallMeta[];
}

export interface PositionReview {
  id: number;
  ts: string;
  position_id: string;
  model: string;
  action: string;
  reasoning: string;
  confidence: number;
  requested: { new_stop?: number | null; new_target?: number | null };
  applied: { changes?: Record<string, number>; notes?: string[] };
  context: Record<string, unknown>;
  cost_usd: number;
  error: string;
}

export interface Lesson {
  id: number;
  ts: string;
  position_id: string;
  symbol: string;
  vehicle: string;
  outcome_r: number;
  tags: string[];
  situation: string;
  what_happened: string;
  lesson: string;
  model: string;
  cost_usd: number;
}

export interface PositionTrace {
  position: Position;
  reviews: PositionReview[];
  orders: Order[];
  events: TraderEvent[];
  lesson: Lesson | null;
  /** position_manager review calls (the i-th pairs with reviews[i]) and the reviewer's lesson call. Absent on older backends. */
  llm_calls?: LLMCallMeta[];
  decision: DecisionTrace;
}

export interface AgentRow {
  agent: string;
  model: string;
  calls: number;
  right: number;
  hit_rate: number | null;
  calls_today: number;
  cost_today: number;
  avg_latency_ms: number;
  errors_today: number;
}

export interface Stats {
  trades: number;
  wins: number;
  win_rate: number | null;
  total_pnl: number;
  avg_r: number | null;
  profit_factor: number | null;
  by_exit_reason: Record<string, { count: number; pnl: number }>;
}

export interface EquityPoint {
  ts: string;
  equity: number;
  day_pnl: number;
}

export interface CloseResponse {
  ok: boolean;
  status: string;
}

export interface KillResponse {
  changed: boolean;
  kill: KillSnapshot;
}

export interface RearmResponse {
  kill: KillSnapshot;
}

export interface RobinhoodSnapshot {
  logged_in: boolean;
  verified_at: string | null;
  /** The engine takes market data from Robinhood. */
  in_use: boolean;
  /** Orders go to Robinhood (live mode). */
  broker_in_use: boolean;
  account: { last4: string; type: string; option_level: string; options_ok: boolean } | null;
  balance: { equity: number; cash: number; buying_power: number } | null;
  positions: { key: string; qty: number }[] | null;
  schemas_current: boolean | null;
  error: string;
  checked_at: number;
}

export interface ReadinessCheck {
  id: string;
  label: string;
  /** null: could not be checked. */
  ok: boolean | null;
  detail: string;
  fix: string;
  blocking: boolean;
}

export interface Readiness {
  mode: Mode;
  mode_source: string;
  ready: boolean;
  checks: ReadinessCheck[];
  options: string;
}

export interface ModeResponse {
  switching_to: Mode;
  restarting: boolean;
}

export interface HistoryTrade extends Position {
  /** The trading session the trade closed in. */
  day: string | null;
  hold_minutes: number | null;
}

export interface HistoryDay {
  day: string;
  trades: number;
  wins: number;
  pnl: number;
  best: number;
  worst: number;
  cumulative: number;
}

export interface History {
  mode: Mode;
  trades: HistoryTrade[];
  days: HistoryDay[];
  totals: { trades: number; wins: number; pnl: number };
}
