export type UiPhase =
  | 'IDLE'
  | 'SPIKE_DETECTED'
  | 'PULLBACK_ACTIVE'
  | 'PULLBACK_READY'
  | 'CONTINUATION_ARMED'
  | 'TRIGGERED'
  | 'REJECTED'
  | 'DISARMED'

export interface RadarRow {
  symbol: string
  instrument_token?: number | null
  last_1m_close?: number | null
  pct_change?: number | null
  phase: UiPhase
  direction?: string | null
  spike: string
  pullback: string
  continuation: string
  volume?: number | null
  trigger_price?: number | null
  distance_pct?: number | null
  last_event: string
  updated_at?: string | null
  setup_count?: number
  vwap_classification?: 'ACCEPT' | 'LIMITED' | 'REJECT' | 'UNAVAILABLE' | null
}

export interface TimelineEvent {
  sequence_number: number
  event_type: string
  resulting_state: string
  label: string
  evaluation_candle_time?: string | null
  created_at: string
}

export interface TimelineContinuation {
  trigger_price: number
  armed_at: string
  decision?: string | null
  reason?: string | null
}

export interface TimelineSetup {
  setup_id: string
  direction: string
  spike_candle_time: string
  created_at: string
  final_state: string
  status: string
  events: TimelineEvent[]
  continuation?: TimelineContinuation | null
}

export interface TimelineSpike {
  candle_time: string
  direction: string
  detected_at: string
  close: number
}

export interface SymbolTimelineResponse {
  session_date: string
  symbol: string
  spikes: TimelineSpike[]
  setups: TimelineSetup[]
}

export interface RadarResponse {
  session_date: string
  rows: RadarRow[]
}

export interface SessionCoverage {
  session_date: string
  subscribed: number
  tokens_with_1m: number
  tokens_with_5m: number
  baseline_as_of?: string | null
  spikes: number
  setups: number
  continuation_arms: number
  continuation_decisions: number
  continuation_successful: number
  continuation_failed: number
  vwap_successful: number
}

export interface VwapQualifierStatus {
  state: 'bootstrapping' | 'ready' | 'repairing' | 'unavailable'
  bootstrap_ready?: boolean
  bootstrap_failed?: boolean
  feed_stale?: boolean
  repair_queued?: number
  uncertain_bucket_count?: number
  failed_token_count?: number
  token_count?: number
  classified?: number
  accept?: number
  limited?: number
  reject?: number
  unavailable?: number
  persist_failures?: number
  callback_failures?: number
  reason?: string | null
}

export interface VwapHealthStatus {
  status: 'ok' | 'alarm' | 'idle' | 'unknown'
  session_live?: boolean
  session_date?: string | null
  triggered_count: number
  qualified_count: number
  stuck_count: number
  callback_failures: number
  persist_failures: number
  reason?: string | null
  checked_at?: string | null
}

export interface RunnerStatus {
  session_date?: string | null
  subscribed_tokens?: number | null
  feed_status?: string | null
  last_tick_time?: string | null
  updated_at?: string | null
  /** Server-derived from status file freshness; absent on older backends. */
  runner_state?: 'running' | 'stopped' | null
  vwap_qualifier?: VwapQualifierStatus | null
}

export interface AuthStatusResponse {
  api_key_configured: boolean
  api_secret_configured: boolean
  access_token_present: boolean
  refresh_token_present: boolean
  masked_api_key?: string | null
  masked_access_token?: string | null
  masked_refresh_token?: string | null
}

export interface LoginUrlResponse {
  login_url: string
}

export interface KiteStartResponse {
  mode?: 'auto' | 'oauth'
  authorize_url?: string | null
  success?: boolean
  message?: string | null
  user_id?: string | null
  masked_access_token?: string | null
  auto_failure_reason?: string | null
}

export interface MeResponse {
  username: string
  mfa_enabled: boolean
  mfa_required: boolean
  step_up_active: boolean
  auth_enabled: boolean
  passkey_count: number
}

export interface PasskeyOptionsResponse {
  challenge_id: string
  options: Record<string, unknown>
}

export interface PasskeyInfo {
  credential_id: string
  name: string
  created_at: string
  last_used_at: string | null
}

export interface PasskeyListResponse {
  passkeys: PasskeyInfo[]
}

export interface MfaSetupResponse {
  otpauth_uri: string
  secret: string
  message: string
}

export interface SessionResponse {
  success: boolean
  user_id?: string | null
  masked_access_token: string
  masked_refresh_token?: string | null
  message: string
}

export interface CheckTokenResponse {
  valid: boolean
  message: string
  user_id?: string | null
}

export type ChecklistStatus = 'not_checked' | 'ok' | 'warning' | 'failed' | 'needs_update'

export interface GenerateAction {
  available: boolean
  label: string
  task?: string | null
  reason?: string | null
}

export interface GenerateResponse {
  success: boolean
  message: string
  task: string
}

export interface DatabaseStatus {
  name: string
  path: string
  exists: boolean
  readable: boolean
  scope: string
}

export interface SuggestedCommands {
  runner: string
  instrument_collector: string
  historical_collector: string
  baseline_generator: string
  five_minute_generator: string
  offline_validation: string
  startup: string[]
}

export interface KiteAuthCheck {
  status: ChecklistStatus
  message: string
  api_key_configured: boolean
  api_secret_configured: boolean
  access_token_present: boolean
  masked_access_token?: string | null
  token_validated_today?: boolean
  token_checked_at?: string | null
  token_generated_at?: string | null
  copy_command: string
}

export interface InstrumentsCheck {
  status: ChecklistStatus
  message: string
  instruments_count: number
  expected_count: number
  tick_size_count: number
  last_updated?: string | null
  missing_symbols: string[]
  copy_command: string
  generate_action?: GenerateAction | null
}

export interface HistoricalCandlesCheck {
  status: ChecklistStatus
  message: string
  latest_date?: string | null
  expected_prior_session?: string | null
  symbols_covered: number
  expected_count: number
  missing_count: number
  missing_symbols_sample: string[]
  copy_command: string
  db_path?: string | null
  generate_action?: GenerateAction | null
}

export interface BaselinesCheck {
  status: ChecklistStatus
  message: string
  baseline_as_of?: string | null
  expected_as_of?: string | null
  symbols_covered: number
  expected_count: number
  reliable_count: number
  last_generated_at?: string | null
  copy_command: string
  db_path?: string | null
  generate_action?: GenerateAction | null
}

export interface FiveMinuteCandlesCheck {
  status: ChecklistStatus
  message: string
  latest_date?: string | null
  expected_prior_session?: string | null
  symbols_covered: number
  expected_count: number
  missing_count: number
  ema_seed_ready: number
  ema_seed_missing: number
  copy_command: string
  generate_action?: GenerateAction | null
}

export interface OfflineChecksCheck {
  status: ChecklistStatus
  message: string
  api_health: string
  database_readable: boolean
  databases?: DatabaseStatus[]
  missing_databases?: string[]
  radar_row_count: number
  copy_command: string
  generate_action?: GenerateAction | null
}

export interface DashboardReadinessCheck {
  status: ChecklistStatus
  message: string
  api_reachable: boolean
  latest_session?: string | null
  market_hour_trial_ready: boolean
  trial_ready_reason: string
  copy_command: string
}

export interface ChecklistAreas {
  kite_auth: KiteAuthCheck
  instruments: InstrumentsCheck
  historical_candles: HistoricalCandlesCheck
  baselines: BaselinesCheck
  five_minute_candles: FiveMinuteCandlesCheck
  offline_checks: OfflineChecksCheck
  dashboard_readiness: DashboardReadinessCheck
}

export interface ChecklistActivity {
  session_date: string
  revision: string
  source: 'automatic' | 'manual'
  status: 'running' | 'completed' | 'blocked' | 'skipped'
  stage: 'kite' | 'instruments' | 'historical' | 'baselines' | 'five-minute' | 'validation'
  message: string
  dirty?: string[]
}

export interface PreMarketChecklistResponse {
  activity?: ChecklistActivity | null
  session_date: string
  checked_at: string
  overall_status: ChecklistStatus
  blockers: string[]
  next_step: string
  local_data_dir?: string
  suggested_commands: SuggestedCommands
  areas: ChecklistAreas
}

export interface ObservationReadiness {
  checklist_ok: boolean
  checklist_status: ChecklistStatus
  market_open: boolean
  runner_running: boolean
  can_start: boolean
  reason: string
  session_date: string
  expected_stop_at?: string | null
}

export interface ObservationStartResponse {
  success: boolean
  message: string
  pid?: number | null
}

export interface ObservationStopResponse {
  success: boolean
  message: string
  pid?: number | null
}

// ---------------------------------------------------------------------------
// Execution engine (/execution).
// ---------------------------------------------------------------------------

export interface ExecutionSessionCaps {
  per_trade_cap_rupees: number
  per_trade_cap_vwap_limited_rupees: number
  daily_loss_cap_rupees: number
  total_capital_rupees: number
  leverage_factor: number
}

export interface ExecutionPrecondition {
  key: string
  ok: boolean
  detail: string
}

export interface ExecutionPreflight {
  can_start: boolean
  checks: ExecutionPrecondition[]
  engine_state: string
  engine_reason: string | null
  refusals: string[]
}

export interface ExecutionCapital {
  total_capital_rupees: number
  leverage_factor: number
  buying_power_rupees: number
  margin_used_rupees: number
  remaining_capital_rupees: number
  remaining_buying_power_rupees: number
}

/** "running" | "stopped" | "crashed" | "absent" */
export type ExecutionEngineState = 'running' | 'stopped' | 'crashed' | 'absent'

/** Live Open P&L feed: WebSocket, Kite REST fallback, or none (PAPER). */
export type LivePnlFeedState = 'live' | 'fallback' | 'off'

export interface ExecutionStatus {
  engine_state: ExecutionEngineState
  engine_reason: string | null
  heartbeat_age_seconds: number | null
  stopped_on_purpose: boolean
  stop_reason: string | null
  run_id: string | null
  session_date: string | null
  is_live: boolean
  tick_count: number
  entries_allowed: boolean
  entries_stopped: boolean
  entries_paused: boolean
  pause_reason: string | null
  open_positions: number
  unprotected: number
  realised_loss_today: number
  daily_loss_cap: number
  remaining_daily: number
  caps: ExecutionSessionCaps
  capital: ExecutionCapital | null
  total_live_pnl: number | null
  live_pnl_as_of: string | null
  live_pnl_complete: boolean
  /** Total Day = Total Realised + Total Ongoing. Absent from older APIs. */
  total_day_pnl?: number | null
  total_realised_pnl?: number | null
  total_ongoing_pnl?: number | null
  live_pnl_feed_state?: LivePnlFeedState | null
  live_pnl_feed_reason?: string | null
  live_pnl_last_tick_at?: string | null
  /** Kite order-update pushes received this engine run (each wakes the loop). */
  live_pnl_order_updates?: number | null
  last_error: string | null
  escalations: Record<string, string>
  /** What the running (or last) session was started as. */
  strategy_mode?: StrategyMode
  /** ARB's day state, written by the engine every tick. Null in a legacy session. */
  arb?: ArbDayStatus | null
}

export type StrategyMode = 'legacy_vwap' | 'arb'

/** engine_core.arb_snapshot: the Adaptive Risk Budget right now. */
export interface ArbDayStatus {
  error?: string
  risk_scale: number
  /** Day P&L after estimated charges, open positions marked at Kite LTP. */
  day_mtm: number | null
  closed_net: number | null
  open_net: number | null
  prices_complete: boolean | null
  stale_symbols: string[]
  day_check_ok: boolean
  peak_mtm: number | null
  lock_armed: boolean
  lock_line: number | null
  lock_arm_at: number
  lock_keep: number
  hard_day_stop: number
  closed_loss_backstop: number
  open_risk_cap: number
  house_money: number
  live_risk: number
  room: number
  min_risk: number
  losers_today: number
  half_size: boolean
  score0_used: number
  score0_max: number
  orb_risk: number
  funnel_risks: number[]
  orb_cutoff: string
  funnel_cutoff: string
  halted_reason: string | null
  halted_at: string | null
  halted_mtm: number | null
}

export interface ArbStage {
  key: string
  risk_scale: number
  unlocked: boolean
  orb_risk: number
  funnel_risks: number[]
  open_risk_cap: number
  min_risk: number
  hard_day_stop: number
  day_lock_arm: number
  closed_loss_backstop: number
}

/** What the 09:15 autostart launches, and the stages ARB can run at. */
export interface ExecutionSettings {
  autostart_strategy: StrategyMode
  arb_risk_scale: number
  arb_total_capital_rupees: number
  updated_at: string | null
  updated_by: string | null
  unlocked_scale: number
  day_lock_keep: number
  stages: ArbStage[]
}

/** What ARB decided when it took (or skipped) a signal. */
export interface ArbTradeInfo {
  strategy?: 'ORB' | 'FUNNEL'
  risk_scale?: number
  score?: number | null
  score_parts?: Record<string, boolean>
  base_risk?: number
  risk_used?: number
  risk_allowed?: number
  half_size?: boolean
  one_share_floor?: boolean
  room?: number
  losers_today?: number
  live_risk?: number
  day_mtm?: number
}

export interface ExecutionPosition {
  trade_id: string
  setup_id: string
  session_date: string
  tradingsymbol: string
  direction: string
  vwap_classification: string | null
  state: string
  qty: number
  entry_price: number | null
  stop_price: number | null
  /** Risk at the fill; never changes. */
  risk_taken_rupees: number | null
  /** Loss if the current stop filled now; negative once profit is locked. Open only. */
  current_risk_rupees?: number | null
  realised_pnl: number | null
  live_pnl: number | null
  /** "ws" = live WebSocket tick, "kite_rest" = fallback to Kite REST pnl. */
  live_pnl_source?: 'ws' | 'kite_rest' | null
  live_pnl_reason?: string | null
  /** This trade's P&L plus earlier closed trades in the same stock today. */
  stock_day_total?: number | null
  /** Kite's day figure for the stock vs our per-trade sum, when over ₹1 apart. */
  pnl_mismatch?: { kite_pnl: number; ours: number; diff: number; over_exit_qty?: number } | null
  /** Closed, but no closing order was found at Kite. */
  realised_unattributed?: boolean
  entry_order_id: string | null
  stop_order_id: string | null
  exit_order_id: string | null
  is_live: boolean
  run_id: string | null
  close_reason: string | null
  skip_reason: string | null
  stop_adopted_from_broker: boolean
  /** The structural stop the trade was first protected with: the trailing floor.
   *  stop_price is the current stop while open, the final one once closed. */
  initial_stop_price?: number | null
  /** Auto-trail on/off for this position. */
  trail_enabled?: boolean
  /** Kite modifications counted on the live stop order (Kite allows 25). */
  stop_mod_count?: number
  /** An exit is under way: waiting on the stop cancel, or a safety exit. */
  exiting?: boolean
  manual_review: string | null
  created_at: string | null
  updated_at: string | null
  /** ARB: "ORB", or "F0".."F3" by funnel score. Absent on a legacy trade. */
  source?: string | null
  arb?: ArbTradeInfo | null
  /** The price the backtest assumed, and how much worse the fill was (bps). */
  signal_price?: number | null
  entry_slippage_bps?: number | null
  /** "initial" | "locked" (+0.25R) | "giveback" (25% of the peak gain). */
  lock_stage?: string | null
  /** The stop is at or beyond entry: no longer uses the open-risk budget. */
  risk_freed?: boolean
  /** Closed trades: the result in R before charges. */
  r_gross?: number | null
}

export interface ExecutionPositions {
  session_date: string
  open: ExecutionPosition[]
  closed: ExecutionPosition[]
  rejected: ExecutionPosition[]
  total_live_pnl: number | null
  live_pnl_as_of: string | null
  live_pnl_complete: boolean
  /** Total Day = Total Realised + Total Ongoing. Absent from older APIs. */
  total_day_pnl?: number | null
  total_realised_pnl?: number | null
  total_ongoing_pnl?: number | null
  live_pnl_feed_state?: LivePnlFeedState | null
  live_pnl_feed_reason?: string | null
  live_pnl_last_tick_at?: string | null
  /** Kite order-update pushes received this engine run (each wakes the loop). */
  live_pnl_order_updates?: number | null
}

export interface ExecutionEvent {
  event_id: number
  trade_id: string
  at: string
  event_type: string
  payload: Record<string, unknown>
}

export interface ExecutionEvents {
  trade_id: string
  events: ExecutionEvent[]
}

export type ExecutionCommandKind =
  | 'stop'
  | 'start'
  | 'close_position'
  | 'kill_all'
  | 'move_stop'
  | 'set_trail'

/** move_stop: ticks = +1 / -1 in price terms. set_trail: enabled. */
export interface ExecutionCommandPayload {
  ticks?: 1 | -1
  enabled?: boolean
}

export interface ExecutionCommand {
  command_id: number
  kind: string
  status: 'pending' | 'applied' | 'rejected'
  trade_id: string | null
  result: Record<string, unknown> | null
  at: string | null
  applied_at: string | null
}

export interface ExecutionStartResponse {
  success: boolean
  message: string
  pid: number | null
}

// ---- Charges tab (display only; never feeds the engine) ----

export interface ChargeBreakdown {
  brokerage: number
  stt: number
  exchange: number
  sebi: number
  stamp_duty: number
  gst: number
  total: number
}

export type TradeChargesStatus = 'ok' | 'paper' | 'unavailable'

export interface TradeChargesRow {
  trade_id: string
  tradingsymbol: string
  direction: string
  qty: number
  closed_at: string | null
  close_reason: string | null
  /** ARB only: "ORB", or "F0".."F3" by funnel score. */
  source?: string | null
  entry_avg: number | null
  exit_avg: number | null
  gross_pnl: number | null
  charges: ChargeBreakdown | null
  net_pnl: number | null
  status: TradeChargesStatus
  reason: string | null
}

export interface TradeChargesDay {
  session_date: string
  trades: TradeChargesRow[]
  total_gross_pnl: number
  total_charges: number
  total_net_pnl: number
  priced_count: number
  live_closed_count: number
  kite_error: string | null
}

// ---------------------------------------------------------------------------
// Strategy tab (display only): the ORB board and the funnel feed
// ---------------------------------------------------------------------------

export type OrbDetectorState = 'collecting' | 'ranked' | 'done' | 'disabled'
export type OrbArmState = 'ARMED' | 'TRIGGERED' | 'MISSED' | 'EXPIRED'

export interface OrbRules {
  rule_version: string
  range: string
  min_rvol: number
  top_n: number
  stop_atr_mult: number
  trigger_end: string
  max_overshoot_atr: number
}

/** What the execution engine did with a signal. */
export interface EngineDecision {
  decision: 'entered' | 'skipped'
  state: string
  reason: string | null
  source: string | null
  base_risk: number | null
  risk_used: number | null
  half_size: boolean | null
  qty: number | null
  entry_price: number | null
  close_reason: string | null
}

export interface OrbArm {
  /** Null when the engine has no row for this signal. */
  engine?: EngineDecision | null
  setup_id: string
  symbol: string
  direction: string
  rank: number
  rvol: number
  level: number
  atr14: number
  stop_distance: number
  stop_at_level: number | null
  or_open: number
  or_high: number
  or_low: number
  or_close: number
  or_volume: number
  avg_or_volume: number
  state: OrbArmState
  reason: string | null
  decided_at: string | null
  trigger_price: number | null
  planned_stop: number | null
  overshoot: number | null
  overshoot_bps: number | null
}

export interface OrbRankedStock {
  symbol: string
  direction: string | null
  rank: number | null
  rvol: number | null
  level: number | null
  reason: string | null
}

export interface OrbDay {
  session_date: string
  rules: OrbRules
  status: {
    state: OrbDetectorState
    reason: string | null
    updated_at: string
    restored: boolean
  } | null
  baselines: {
    ready: number
    total: number
    as_of: string | null
    not_ready: { symbol: string; reason: string | null; detail: string | null }[]
  }
  ranked: boolean
  ranked_at: string | null
  arms: OrbArm[]
  in_play_not_armed: OrbRankedStock[]
  near_misses: OrbRankedStock[]
  valid_count: number
  unusable: Record<string, number>
}

export interface FunnelTrigger {
  engine?: EngineDecision | null
  setup_id: string
  symbol: string
  direction: string
  trigger_time: string | null
  trigger_price: number
  stop: number | null
  vwap_classification: string | null
  volume_ratio: number | null
  stop_pct: number | null
  is_first_trigger_today: boolean | null
  align_market_pct: number | null
  align_sector_pct: number | null
  passes: boolean
  reason: string | null
  score: number | null
  score_parts: Record<string, boolean>
  base_risk: number | null
}

export interface FunnelFeed {
  session_date: string
  triggers: FunnelTrigger[]
  pending: number
}


/** GET /strategy/breadth: the market's move from the open (the funnel score's input). */
export interface MarketBreadth {
  session_date: string
  as_of: string | null
  stocks: number
  market_pct: number | null
  threshold_pct: number
  /** Which side scores a point for "market against" right now. */
  scores_for: 'UP' | 'DOWN' | null
  sectors: { sector: string; pct: number; stocks: number }[]
}

// --- Performance tab (/performance) ------------------------------------------

export interface PerformanceGate {
  key: string
  label: string
  /** null = cannot be judged yet. */
  ok: boolean | null
  detail: string
}

export interface PerformanceDay {
  session_date: string
  trades: number
  gross: number
  charges: number
  net: number
  r_gross: number
  cumulative_net: number
  halted_reason: string | null
}

export interface PerformanceParityDay {
  session_date: string
  generated_at: string
  candidates: number | null
  matched: number | null
  match_rate: number | null
  live_entered: number | null
  replay_entered: number | null
  live_r: number | null
  replay_r: number | null
  halt_match: boolean | null
  live_halt: { reason: string; minute: string | null } | null
  replay_halt: { reason: string; minute: string | null } | null
}

export interface PerformanceMismatch {
  setup_id: string
  symbol: string
  strategy: string
  direction: string
  time: string
  why: string | null
  live: { decision: string; reason?: string | null; risk_used?: number | null }
  replay: { decision: string; reason?: string | null; risk_used?: number | null }
}

export interface SlippageStats {
  trades: number
  median: number | null
  mean: number | null
}

export interface PerformanceSummary {
  risk_scale: number
  available_scales: number[]
  stage: {
    risk_scale: number
    key: string
    unlocked_scale: number
    next_scale: number | null
    next_key: string | null
    days: number
    trades: number
    first_day: string | null
    last_day: string | null
    gates: PerformanceGate[]
    all_gates_met: boolean
    incidents: { session_date: string; symbol: string; close_reason: string }[]
  }
  totals: {
    trades: number
    wins: number
    win_rate: number | null
    gross: number
    charges: number
    net: number
    r_gross: number
    days: number
    green_days: number
    green_day_rate: number | null
    worst_day: number | null
    best_day: number | null
    max_drawdown: number
  }
  days: PerformanceDay[]
  months: { month: string; net: number; r_gross: number; trades: number; days: number; green_days: number }[]
  sources: { source: string; trades: number; wins: number; net: number; r_gross: number; avg_r: number | null }[]
  slippage: {
    entry: SlippageStats
    stop_exit: SlippageStats
    rolling_trades: number
    rolling_entry_mean: number | null
    rolling_alert: boolean
    rolling_demote: boolean
    alert_bps: number
    demote_bps: number
  }
  day_controls: {
    session_date: string
    reason: string
    at: string | null
    mtm: number | null
    peak: number | null
    day_net: number | null
  }[]
  parity: {
    days: PerformanceParityDay[]
    candidates: number
    matched: number
    match_rate: number | null
    live_r: number
    replay_r: number
    unchecked_days: string[]
    detail_date: string | null
    detail_rows: PerformanceMismatch[]
  }
  reference: {
    period: string
    scaled_to: number
    at_3bps: { net_per_month: number; worst_day: number; green_days: number; max_drawdown: number; trades_per_month: number }
    at_5bps: { net_per_month: number; worst_day: number; green_days: number; max_drawdown: number; trades_per_month: number }
  }
}
