// Offline browser comparison: every API request is fulfilled with fixture data.
const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const os = require('node:os')
const { createRequire } = require('node:module')
const runtime = createRequire(process.env.PARITY_RUNTIME + '/package.json')
const { chromium } = runtime('playwright')
const { PNG } = runtime('pngjs')
const assert = require('node:assert/strict')
const date = '2026-09-21', at = date + 'T06:03:44Z'
const area = {status:'needs_update',message:'Historical candles need coverage through prior session 2026-09-18',expected_count:100,symbols_covered:0,missing_count:100,missing_symbols:[],missing_symbols_sample:[],latest_date:'2026-09-16',expected_prior_session:'2026-09-18',copy_command:'python3 fixture.py',generate_action:{label:'Generate',enabled:true}}
const checklist = {session_date:date,checked_at:at,overall_status:'failed',blockers:[area.message],next_step:area.message,suggested_commands:{runner:'python3 live_observation_runner.py --status-file /tmp/runner_status.json'},areas:{kite_auth:{status:'ok',message:'Token validated today',api_key_configured:true,api_secret_configured:true,access_token_present:true,token_validated_today:true,token_checked_at:at,token_generated_at:at,masked_access_token:'ab...xy'},instruments:{...area,status:'warning',instruments_count:100,tick_size_count:100,last_updated:'2026-09-10T12:37:00Z'},historical_candles:{...area},baselines:{...area,baseline_as_of:'2026-09-15',expected_as_of:'2026-09-18',reliable_count:0},five_minute_candles:{...area,ema_seed_ready:100,ema_seed_missing:0},offline_checks:{...area,status:'failed',api_health:'ok',database_readable:true,radar_row_count:0},dashboard_readiness:{...area,api_reachable:true,latest_session:date,market_hour_trial_ready:false,trial_ready_reason:area.message}}}
const values={per_trade_risk_cap_inr:1000,limited_per_trade_risk_cap_inr:500,daily_loss_cap_inr:3000,vwap_accept_gap_exclusive_max:0.001,vwap_limited_gap_inclusive_max:0.002}
function fixture(p) {
  if(p.endsWith('/account/me')) return {username:'NJ',mfa_enabled:true,mfa_required:false,auth_enabled:true}
  if(p.endsWith('/sessions')) return [date]
  if(p.endsWith('/timeline')) return {session_date:date,symbol:'ABB',spikes:[],setups:[]}
  if(p.includes('/premarket-checklist')) return checklist
  if(p.endsWith('/radar')) return {session_date:date,rows:[{symbol:'ABB',phase:'IDLE',spike:'—',pullback:'—',continuation:'—',last_event:'Waiting',last_1m_close:1234,pct_change:1.2,volume:1000,updated_at:at}]}
  if(p.endsWith('/coverage')) return {session_date:date,subscribed:100,tokens_with_1m:100,tokens_with_5m:100,spikes:0,setups:0,continuation_arms:0,continuation_decisions:0,continuation_successful:0,continuation_failed:0}
  if(p.includes('/observation/readiness')) return {session_date:date,checklist_ok:false,checklist_status:'failed',market_open:false,runner_running:false,can_start:false,reason:area.message}
  if(p.includes('/observation/sectors')) return {valid:true,version:'fixture',universe_version:'fixture',symbol_count:1,sectors:[{name:'Industrials',symbols:['ABB']}]}
  if(p.includes('/trading-engine/')) return {state:'stopped',session_date:date,live_orders_enabled:false,live_orders_env_enabled:false,engine_running:false,can_confirm_live:false,unprotected_count:0,limits_protected:true,closed_loss_today:0,committed_risk:0,remaining_daily:3000,live_pnl:0,total_capital:100000,leverage_factor:1,margin_used:0,remaining_capital:100000,buying_power:100000,active:[],closed:[],skipped:[]}
  if(p.endsWith('/status') && !p.includes('/auth/')) return {runner_state:'stopped',feed_status:'OFFLINE',session_date:date,subscribed_tokens:100,updated_at:at}
  if(p.endsWith('/auth/status')) return {api_key_configured:true,api_secret_configured:true,access_token_present:true,masked_access_token:'ab...xy'}
  if(p.endsWith('/auth/check-token')) return {valid:true,message:'Token valid',user_id:'NJ'}
  if(p.endsWith('/auth/kite/start')) return {mode:'auto',success:true,user_id:'NJ',message:'Token generated'}
  if(p.endsWith('/admin/config')) return {version_id:'fixture',entries_paused:true,values,vwap_accept_gap_percent:0.1,vwap_limited_gap_percent:0.2,warnings:[],accepting_triggers:false,engine_running:false}
  if(p.includes('/admin/audit')) return {entries:[],limit:30,offset:0}
  if(p.endsWith('/health')) return {status:'ok'}
  if(p.endsWith('/account/logout')) return {success:true}
  if(p.endsWith('/trading/control')) return {strip:{execution_mode:'PAPER',entry_mode:'PAUSED',entry_permission:'BLOCKED',engine_state:'stopped',unresolved_incident:false,as_of:at},effective:values,saved:values}
  throw Error('Unhandled fixture: '+p)
}
async function serve(root) {
  const s=http.createServer((req,res)=>{let f=path.join(root,new URL(req.url,'http://local').pathname);if(!fs.existsSync(f)||fs.statSync(f).isDirectory())f=path.join(root,'index.html');res.setHeader('Content-Type',f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':f.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(f))})
  await new Promise(r=>s.listen(0,'127.0.0.1',r));return s
}
async function main(){
 const roots=[path.resolve(__dirname,'../Reference/live-release-auto-login-button-20260916121611/frontend/dist'),path.resolve(__dirname,'dist')]
 const browser=await chromium.launch({headless:true,executablePath:process.env.PARITY_BROWSER}),servers=await Promise.all(roots.map(serve)),shots=[],texts=[],requests=[]
 try {
  for(let i=0;i<2;i++){
   const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Asia/Kolkata'}),errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message))
   await page.clock.install({time:new Date(at)});await page.clock.pauseAt(new Date(at))
   await page.route('**/*',async route=>{const u=new URL(route.request().url());if(u.pathname.startsWith('/api/')){calls.push(route.request().method()+' '+u.pathname);try{await route.fulfill({json:fixture(u.pathname)})}catch(e){errors.push(e.message);await route.fulfill({status:500,json:{detail:e.message}})}}else if(u.hostname!=='127.0.0.1')await route.abort();else await route.continue()})
   await page.goto('http://127.0.0.1:'+servers[i].address().port+'/owner');await page.getByRole('button',{name:'Checklist',exact:true}).waitFor();shots[i]={};texts[i]={}
   for(const tab of ['Radar Stream','Checklist','Execution Desk','Diagnostics & Logs','Settings']){
    await page.getByRole('button',{name:tab,exact:true}).click();await page.waitForLoadState('networkidle')
    texts[i][tab]=await page.locator('body').innerText();shots[i][tab]=await page.screenshot({fullPage:true,animations:'disabled'});fs.writeFileSync(path.join(os.tmpdir(),'parity-'+i+'-'+tab.replace(/[^a-z]/gi,'')+'.png'),shots[i][tab])
   }
   await page.getByRole('button',{name:'Checklist',exact:true}).click();await page.getByRole('button',{name:'Generate Kite Token',exact:true}).click();await page.getByText('[11:33:44] [KITE] Auto token generated for NJ',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Generate Kite Token',exact:true}).isEnabled(),true)
   await page.getByRole('button',{name:'Logout',exact:true}).click();await page.getByText('Sign in to the observation dashboard',{exact:true}).waitFor();shots[i].Login=await page.screenshot({animations:'disabled'});texts[i].Login=await page.locator('body').innerText()
   assert.deepEqual(errors,[]);requests[i]=calls;await page.close()
  }
  if(process.env.PARITY_DIAGNOSTIC) {console.log('Diagnostic screenshots saved to '+os.tmpdir()+'; comparison assertions not run.');return}
  for(const tab of Object.keys(shots[0])){assert.equal(texts[0][tab],texts[1][tab],tab+' text differs');const a=PNG.sync.read(shots[0][tab]),b=PNG.sync.read(shots[1][tab]);assert.equal(a.width,b.width);assert.equal(a.height,b.height);assert.equal(Buffer.compare(a.data,b.data),0,tab+' pixels differ');console.log(tab+': identical text and pixels')}
  assert.deepEqual(requests[0],requests[1]);console.log('Auto-login, normal login UI, and API request sequences match; no runtime errors. All API traffic was mocked.')
 }finally{await browser.close();servers.forEach(s=>s.close())}
}
main().catch(e=>{console.error(e);process.exitCode=1})
