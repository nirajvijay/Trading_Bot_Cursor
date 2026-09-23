// Offline regression: existing stage controls reflect server-owned work.
const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const runtime = createRequire(path.join(process.env.PARITY_RUNTIME, 'package.json'))
const { chromium } = runtime('playwright')
const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())
const at = new Date().toISOString()
let activity = null, fullReads = 0, lightReads = 0, writes = 0
const stages = { kite: 'kite_auth', instruments: 'instruments', historical: 'historical_candles', baselines: 'baselines', 'five-minute': 'five_minute_candles' }
const labels = ['Generate Kite Token', 'Generate Instruments', 'Generate 1-Minute Candles', 'Generate Baselines', 'Generate 5-Minute Candles']
function checklist() {
  const area = { status: 'ok', message: 'Valid', expected_count: 100, symbols_covered: 100, missing_count: 0, missing_symbols: [], missing_symbols_sample: [], latest_date: day, expected_prior_session: day, generate_action: { available: true }, copy_command: '' }
  const areas = {
    kite_auth: { ...area, api_key_configured: true, api_secret_configured: true, access_token_present: true, token_validated_today: true, token_checked_at: at },
    instruments: { ...area, instruments_count: 100, tick_size_count: 100, last_updated: at },
    historical_candles: { ...area },
    baselines: { ...area, reliable_count: 100, baseline_as_of: day, expected_as_of: day },
    five_minute_candles: { ...area, ema_seed_ready: 100, ema_seed_missing: 0 },
    offline_checks: { ...area, api_health: 'ok', database_readable: true, radar_row_count: 0 },
    dashboard_readiness: { ...area, api_reachable: true, market_hour_trial_ready: true },
  }
  if (activity?.status === 'running' || activity?.status === 'blocked') {
    areas[stages[activity.stage]] = { ...areas[stages[activity.stage]], status: activity.status === 'blocked' ? 'failed' : 'warning', message: activity.message }
  }
  return { session_date: day, checked_at: at, overall_status: activity?.status === 'blocked' ? 'failed' : activity?.status === 'running' ? 'warning' : 'ok', blockers: activity?.status === 'blocked' ? [activity.message] : [], next_step: activity?.message || 'Ready', suggested_commands: { runner: '' }, areas, activity }
}
function fixture(url) {
  const p = url.pathname
  if (p.endsWith('/account/me')) return { username: 'owner', mfa_enabled: true, passkey_count: 1, auth_enabled: true }
  if (p.endsWith('/premarket-checklist')) {
    if (url.searchParams.has('activity_only')) { lightReads++; return { session_date: day, activity } }
    fullReads++; return checklist()
  }
  if (p.endsWith('/observation/readiness')) return { session_date: day, checklist_ok: activity?.status === 'completed', can_start: false, runner_running: false, reason: '', checklist_status: 'not_checked' }
  if (p.endsWith('/observation/sectors')) return { valid: true, sectors: [] }
  if (p.endsWith('/session-clock')) return { state: 'CLOSED', as_of: at }
  if (p.endsWith('/radar')) return { session_date: day, rows: [] }
  if (p.endsWith('/sessions')) return [day]
  return {}
}
async function main() {
  const root = path.join(__dirname, 'dist')
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname
    let file = path.join(root, pathname === '/owner' || pathname === '/' ? 'index.html' : pathname)
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(root, 'index.html')
    const mime = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html'
    res.setHeader('Content-Type', mime); res.end(fs.readFileSync(file))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  let browser
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome' })
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', err => errors.push(err.message))
    await page.route('**/api/v1/**', async route => {
      if (route.request().method() !== 'GET') writes++
      await route.fulfill({ json: fixture(new URL(route.request().url())) })
    })
    await page.goto(`http://127.0.0.1:${server.address().port}/owner`)
    await page.getByRole('button', { name: 'Checklist', exact: true }).click()
    await page.getByRole('button', { name: labels[0], exact: true }).waitFor()
    assert.equal(await page.locator('section[data-expanded]').count(), 5)
    await page.clock.install()
    let revision = 0
    for (const [index, stage] of Object.keys(stages).entries()) {
      activity = { session_date: day, source: 'automatic', status: 'running', stage, message: `Preparing ${stage}`, revision: String(++revision) }
      await page.clock.fastForward(5100)
      await page.waitForFunction(label => document.querySelector(`button[aria-label="${label}"]`)?.getAttribute('aria-busy') === 'true', labels[index])
      assert.equal(await page.getByRole('button', { name: labels[index], exact: true }).textContent(), 'Working…')
      for (const label of labels) assert.equal(await page.getByRole('button', { name: label, exact: true }).isDisabled(), true)
      assert.equal(await page.getByRole('button', { name: 'Run All Pending Checks', exact: true }).isDisabled(), true)
    }
    const stableReads = fullReads
    await page.clock.fastForward(5100)
    await page.waitForTimeout(50)
    assert.equal(fullReads, stableReads, 'unchanged activity must not rescan databases')
    await page.reload()
    await page.getByRole('button', { name: 'Checklist', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('button[aria-label="Generate 5-Minute Candles"]')?.getAttribute('aria-busy') === 'true')
    activity = { ...activity, status: 'completed', message: '', revision: String(++revision) }
    await page.clock.fastForward(5100)
    await page.waitForFunction(() => document.querySelector('button[aria-label="Generate 5-Minute Candles"]')?.getAttribute('aria-busy') === 'false')
    activity = { ...activity, status: 'blocked', stage: 'historical', message: 'Historical coverage incomplete', revision: String(++revision) }
    await page.clock.fastForward(5100)
    await page.waitForFunction(() => !document.querySelector('button[aria-label="Generate 1-Minute Candles"]')?.disabled)
    assert.equal(writes, 0, 'frontend must never click actions to drive automation')
    assert.equal(errors.length, 0, errors.join('\n'))
    assert.ok(lightReads >= 5)
    console.log('PASS: five existing stages, busy/disabled controls, mid-run reload, completion, recovery, and lightweight polling')
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
