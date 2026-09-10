// Server-render smoke checks only: not browser interaction/visual acceptance.
import assert from 'node:assert/strict'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {createServer} from 'vite'

const server = await createServer({server:{middlewareMode:true},appType:'custom'})
try {
  globalThis.window = {location:{pathname:'/'}}
  let requests = 0
  globalThis.fetch = async () => {requests++; throw new Error('Render must not fetch')}
  const {default:App} = await server.ssrLoadModule('/src/App.tsx')
  const home = renderToStaticMarkup(React.createElement(App))
  assert.match(home,/Owner access/i)
  assert.doesNotMatch(home,/entry permission|broker sync|effective capital|unresolved protection/i)
  assert.equal(requests,0)
  for(const [file,name,props,text] of [
    ['/src/components/TradingDesk.tsx','TradingDesk',{sessionDate:'2026-09-10'},'Trading Desk'],
    ['/src/components/admin/AdminWorkspace.tsx','AdminWorkspace',{},'Admin'],
    ['/src/components/TradeAuditPanel.tsx','TradeAuditPanel',{rows:[]},'Trade audit'],
    ['/src/components/PrivateStatusStrip.tsx','PrivateStatusStrip',{},'unavailable or stale'],
  ]) {
    const module = await server.ssrLoadModule(file)
    const html = renderToStaticMarkup(React.createElement(module[name],props))
    assert.ok(html.includes(text),`${name}: missing initial-state content`)
  }
  console.log('PASS: public + four owner initial-state server renders; no render-time requests. Browser interactions untested.')
} finally {await server.close()}
