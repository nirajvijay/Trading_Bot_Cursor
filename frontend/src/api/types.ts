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

export interface PreMarketChecklistResponse {
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

export interface AdminConfigValues {
  per_trade_risk_cap_inr: number
  limited_per_trade_risk_cap_inr: number
  daily_loss_cap_inr: number
  vwap_accept_gap_exclusive_max: number
  vwap_limited_gap_inclusive_max: number
}

export interface AdminConfigResponse {
  version_id: string
  entries_paused: boolean
  values: AdminConfigValues
  vwap_accept_gap_percent: number
  vwap_limited_gap_percent: number
  warnings: string[]
  accepting_triggers: boolean
  engine_running: boolean
}

export interface AdminConfigPatchRequest {
  values: AdminConfigValues
  expected_version_id?: string | null
  comment?: string | null
}

export interface AdminAuditEntry {
  id: number
  at: string
  actor_username: string
  action: string
  version_id?: string | null
  from_version_id?: string | null
  diff_json: string
  result: string
  detail?: string | null
  step_up_verified: boolean
}

export interface AdminAuditResponse {
  entries: AdminAuditEntry[]
  limit: number
  offset: number
}

export interface AdminActionResponse {
  success: boolean
  message: string
  entries_paused?: boolean | null
  detail?: string | null
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
  last_error: string | null
  escalations: Record<string, string>
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
  risk_taken_rupees: number | null
  realised_pnl: number | null
  live_pnl: number | null
  entry_order_id: string | null
  stop_order_id: string | null
  exit_order_id: string | null
  is_live: boolean
  run_id: string | null
  close_reason: string | null
  skip_reason: string | null
  stop_adopted_from_broker: boolean
  manual_review: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ExecutionPositions {
  session_date: string
  open: ExecutionPosition[]
  closed: ExecutionPosition[]
  rejected: ExecutionPosition[]
  total_live_pnl: number | null
  live_pnl_as_of: string | null
  live_pnl_complete: boolean
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

export type ExecutionCommandKind = 'stop' | 'start' | 'close_position' | 'kill_all'

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
