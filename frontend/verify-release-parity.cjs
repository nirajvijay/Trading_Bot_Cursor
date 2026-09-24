// Current source-build smoke check: every API request is fulfilled with fixture data.
const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const os = require('node:os')
const { createRequire } = require('node:module')
const runtime = createRequire(process.env.PARITY_RUNTIME + '/package.json')
const { chromium } = runtime('playwright')
const assert = require('node:assert/strict')
const date = '2026-09-21', at = date + 'T06:03:44Z'
const area = {status:'needs_update',message:'Historical candles need coverage through prior session 2026-09-18',expected_count:100,symbols_covered:0,missing_count:100,missing_symbols:[],missing_symbols_sample:[],latest_date:'2026-09-16',expected_prior_session:'2026-09-18',copy_command:'python3 fixture.py',generate_action:{label:'Generate',enabled:true}}
const checklist = {session_date:date,checked_at:at,overall_status:'failed',blockers:[area.message],next_step:area.message,suggested_commands:{runner:'python3 live_observation_runner.py --status-file /tmp/runner_status.json'},areas:{kite_auth:{status:'ok',message:'Token validated today',api_key_configured:true,api_secret_configured:true,access_token_present:true,token_validated_today:true,token_checked_at:at,token_generated_at:at,masked_access_token:'ab...xy'},instruments:{...area,status:'warning',instruments_count:100,tick_size_count:100,last_updated:'2026-09-10T12:37:00Z'},historical_candles:{...area},baselines:{...area,baseline_as_of:'2026-09-15',expected_as_of:'2026-09-18',reliable_count:0},five_minute_candles:{...area,ema_seed_ready:100,ema_seed_missing:0},offline_checks:{...area,status:'failed',api_health:'ok',database_readable:true,radar_row_count:0},dashboard_readiness:{...area,api_reachable:true,latest_session:date,market_hour_trial_ready:false,trial_ready_reason:area.message}}}
function fixture(p) {
  if(p.endsWith('/vwap/health')) return {status:'unknown',session_date:date,checked_at:at}
  if(p.endsWith('/account/me')) return {username:'NJ',mfa_enabled:true,mfa_required:false,auth_enabled:true}
  if(p.endsWith('/sessions')) return [date]
  if(p.endsWith('/timeline')) return {session_date:date,symbol:'ABB',spikes:[],setups:[]}
  if(p.includes('/premarket-checklist')) return checklist
  if(p.endsWith('/radar')) return {session_date:date,rows:[{symbol:'ABB',phase:'IDLE',spike:'—',pullback:'—',continuation:'—',last_event:'Waiting',last_1m_close:1234,pct_change:1.2,volume:1000,updated_at:at}]}
  if(p.endsWith('/coverage')) return {session_date:date,subscribed:100,tokens_with_1m:100,tokens_with_5m:100,spikes:0,setups:0,continuation_arms:0,continuation_decisions:0,continuation_successful:0,continuation_failed:0}
  if(p.includes('/observation/readiness')) return {session_date:date,checklist_ok:false,checklist_status:'failed',market_open:false,runner_running:false,can_start:false,reason:area.message}
  if(p.includes('/observation/sectors')) return {valid:true,version:'fixture',universe_version:'fixture',symbol_count:1,sectors:[{name:'Industrials',symbols:['ABB']}]}
  if(p.endsWith('/execution/status')) return {engine_state:'absent',engine_reason:'no_heartbeat',heartbeat_age_seconds:null,stopped_on_purpose:false,stop_reason:null,run_id:null,session_date:date,is_live:false,tick_count:0,entries_allowed:false,entries_stopped:false,entries_paused:false,pause_reason:null,open_positions:0,unprotected:0,realised_loss_today:0,daily_loss_cap:3000,remaining_daily:3000,caps:{per_trade_cap_rupees:900,per_trade_cap_vwap_limited_rupees:450,daily_loss_cap_rupees:3000,total_capital_rupees:300000,leverage_factor:5},capital:null,total_live_pnl:null,live_pnl_as_of:null,live_pnl_complete:false,last_error:null,escalations:{}}
  if(p.endsWith('/execution/preflight')) return {can_start:false,checks:[{key:'market_hours',ok:false,detail:'outside NSE cash session'},{key:'before_entry_cutoff',ok:true,detail:'before 14:00 IST'},{key:'observation_runner',ok:false,detail:'observation_runner_not_running'},{key:'no_engine_running',ok:true,detail:'no live engine'}],engine_state:'absent',engine_reason:'no_heartbeat',refusals:['market_hours','observation_runner']}
  if(p.includes('/execution/positions')) return {session_date:date,open:[],closed:[],rejected:[],total_live_pnl:null,live_pnl_as_of:null,live_pnl_complete:false}
  if(p.endsWith('/status') && !p.includes('/auth/')) return {runner_state:'stopped',feed_status:'OFFLINE',session_date:date,subscribed_tokens:100,updated_at:at}
  if(p.endsWith('/auth/status')) return {api_key_configured:true,api_secret_configured:true,access_token_present:true,masked_access_token:'ab...xy'}
  if(p.endsWith('/auth/check-token')) return {valid:true,message:'Token valid',user_id:'NJ'}
  if(p.endsWith('/auth/kite/start')) return {mode:'auto',success:true,user_id:'NJ',message:'Token generated'}
  if(p.endsWith('/health')) return {status:'ok'}
  if(p.endsWith('/account/logout')) return {success:true}
  throw Error('Unhandled fixture: '+p)
}
async function serve(root) {
  const s=http.createServer((req,res)=>{let f=path.join(root,new URL(req.url,'http://local').pathname);if(!fs.existsSync(f)||fs.statSync(f).isDirectory())f=path.join(root,'index.html');res.setHeader('Content-Type',f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':f.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(f))})
  await new Promise(r=>s.listen(0,'127.0.0.1',r));return s
}
async function main(){
 const server=await serve(path.resolve(__dirname,'dist'))
 const browser=await chromium.launch({headless:true,executablePath:process.env.PARITY_BROWSER})
 try {
   const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Asia/Kolkata'})
   const errors=[],calls=[]
   page.on('pageerror',e=>errors.push(e.message))
   await page.clock.install({time:new Date(at)});await page.clock.pauseAt(new Date(at))
   await page.route('**/*',async route=>{
     const u=new URL(route.request().url())
     if(u.pathname.startsWith('/api/')) {
       calls.push(route.request().method()+' '+u.pathname)
       try { await route.fulfill({json:fixture(u.pathname)}) }
       catch(e) { errors.push(e.message);await route.fulfill({status:500,json:{detail:e.message}}) }
     } else if(u.hostname!=='127.0.0.1') await route.abort()
     else await route.continue()
   })
   await page.goto('http://127.0.0.1:'+server.address().port+'/owner')
   await page.getByRole('button',{name:'Checklist',exact:true}).waitFor()
   for(const tab of ['Observation','Checklist','Execution Desk','VWAP Health']) {
     await page.getByRole('button',{name:tab,exact:true}).click()
     await page.waitForLoadState('networkidle')
     const body=await page.locator('body').innerText()
     assert.doesNotMatch(body,/Diagnostics & Logs|Admin Console|ADMIN CONSOLE|Save thresholds|Audit & rollback/i)
     assert.equal(await page.locator('main').innerText().then(t=>t.trim().length>0),true)
     await page.screenshot({path:path.join(os.tmpdir(),'admin-removal-'+tab.replace(/[^a-z]/gi,'')+'.png'),fullPage:true,animations:'disabled'})
     console.log(tab+': rendered without admin console')
   }
   assert.equal(calls.some(p=>p.includes('/admin/')),false)
   assert.deepEqual(errors,[])
   console.log('No admin requests or browser runtime errors. All API traffic was mocked.')
 } finally { await browser.close();server.close() }
}
main().catch(e=>{console.error(e);process.exitCode=1})
