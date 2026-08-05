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

export interface RunnerStatus {
  session_date?: string | null
  subscribed_tokens?: number | null
  feed_status?: string | null
  last_tick_time?: string | null
  updated_at?: string | null
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
  symbols_covered: number
  expected_count: number
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
