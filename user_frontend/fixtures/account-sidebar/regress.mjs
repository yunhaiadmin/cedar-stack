/**
 * 浏览器驱动真实组件。不改 display，不代表登录会话。
 * 失败即非零退出。
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'

const OUT = '/opt/cursor/artifacts'
const STORE = '/cursor/stores/self/pr192-review-fixes'
mkdirSync(OUT, { recursive: true })
mkdirSync(STORE, { recursive: true })

const chrome = spawn('google-chrome', [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-sandbox',
  '--user-data-dir=/tmp/chrome-nav-regress',
  '--remote-debugging-port=9355', 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
for (let i = 0; i < 50; i++) {
  try { if ((await fetch('http://127.0.0.1:9355/json/version')).ok) break } catch { /* */ }
  await sleep(150)
}
const tab = await (await fetch('http://127.0.0.1:9355/json/new?about:blank', { method: 'PUT' })).json()
const ws = new WebSocket(tab.webSocketDebuggerUrl)
let seq = 0
const pending = new Map()
ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id) }
})
await new Promise((r) => ws.addEventListener('open', r, { once: true }))
const send = (method, params = {}) => new Promise((res) => {
  const id = ++seq
  pending.set(id, res)
  ws.send(JSON.stringify({ id, method, params }))
})
await send('Page.enable')
await send('Runtime.enable')

const checks = []
function check(name, cond, detail) {
  checks.push({ name, pass: !!cond, detail })
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name} ${detail ?? ''}`)
}

async function evalJson(expression) {
  const res = await send('Runtime.evaluate', { expression, returnByValue: true })
  if (res.result?.exceptionDetails) {
    throw new Error(JSON.stringify(res.result.exceptionDetails).slice(0, 800))
  }
  const value = res.result?.result?.value
  if (typeof value !== 'string') throw new Error('no value ' + JSON.stringify(res.result).slice(0, 400))
  return JSON.parse(value)
}
async function act(expression) {
  const res = await send('Runtime.evaluate', { expression, returnByValue: true })
  if (res.result?.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails).slice(0, 500))
}
async function size(w, h) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 800 })
  await sleep(250)
}
async function snap(name) {
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  const buf = Buffer.from(shot.result.data, 'base64')
  writeFileSync(`${OUT}/${name}.png`, buf)
  writeFileSync(`${STORE}/${name}.png`, buf)
}
async function key(name, shift = false) {
  const modifiers = shift ? 8 : 0
  const code = name === 'Tab' ? 9 : 27
  await send('Input.dispatchKeyEvent', {
    type: 'keyDown', key: name, code: name, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, modifiers,
  })
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp', key: name, code: name, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, modifiers,
  })
  await sleep(80)
}

const stateExpr = `(() => {
  const btn = document.querySelector('.uhd-account__navbtn')
  const side = document.querySelector('.uhd-side')
  const drawer = document.querySelector('.uhd-account__drawer')
  const modal = document.querySelector('.uhd-account__modal')
  const main = document.querySelector('.uhd-account__main')
  const box = (el) => {
    if (!el) return null
    const r = el.getBoundingClientRect()
    const s = getComputedStyle(el)
    return { w: Math.round(r.width), x: Math.round(r.x), y: Math.round(r.y), display: s.display, position: s.position }
  }
  const active = document.activeElement
  const labelCount = document.querySelectorAll('.uhd-side__item-label').length
  return JSON.stringify({
    path: location.pathname,
    btn: btn ? { display: getComputedStyle(btn).display, expanded: btn.getAttribute('aria-expanded') } : null,
    side: box(side),
    drawer: box(drawer),
    main: box(main),
    mainInert: !!main?.inert,
    modalInert: !!modal?.inert,
    drawerInert: !!drawer?.inert,
    topInert: !!document.querySelector('.fake-top')?.inert,
    dockInert: !!document.querySelector('.fake-dock')?.inert,
    already: !!document.getElementById('already-inert')?.inert,
    dialog: !!document.querySelector('[role="dialog"]'),
    sheet: !!document.querySelector('.m-sheet'),
    sheetInert: !!document.querySelector('.m-sheet')?.inert,
    menuModalInert: !!document.querySelector('.m-modal')?.inert,
    active: active?.getAttribute?.('aria-label') || (active?.id || active?.innerText || '').trim().slice(0, 24),
    activeIsMain: active === document.querySelector('.uhd-account__main'),
    activeInDrawer: !!(drawer && active && drawer.contains(active)),
    activeInSheet: !!(document.querySelector('.m-modal') && active && document.querySelector('.m-modal').contains(active)),
    overflow: document.documentElement.style.overflow,
    labels: labelCount,
    hasOverview: [...document.querySelectorAll('.uhd-side__item-label')].some((el) => el.textContent.includes('我的概览')),
  })
})()`

await send('Page.navigate', { url: 'http://127.0.0.1:5199/game/account' })
await sleep(1200)
await size(1440, 900)
let s = await evalJson(stateExpr)
check('1440 nav button hidden', s.btn?.display === 'none', s.btn?.display)
check('1440 sidebar about 250', s.side && s.side.w >= 240 && s.side.w <= 260, JSON.stringify(s.side))
check('1440 main starts after sidebar', s.main && s.main.x >= 240, JSON.stringify(s.main))
check('1440 labels visible', s.hasOverview === true, String(s.labels))
await act(`document.querySelector('.uhd-side__collapse').click()`)
await sleep(350)
s = await evalJson(stateExpr)
check('1440 collapsed about 68', s.side && s.side.w >= 60 && s.side.w <= 80, JSON.stringify(s.side))
check('1440 collapsed hides labels', s.hasOverview === false, String(s.labels))
await snap('regress-1440-collapsed')

await size(390, 844)
await sleep(200)
s = await evalJson(stateExpr)
check('390 button shown after desktop collapse', s.btn?.display === 'inline-flex')
check('390 closed drawer not shown', !s.dialog && (s.drawer?.display === 'none' || s.drawer?.w === 0))
check('390 main full width closed', s.main?.w === 390, String(s.main?.w))
await act(`document.querySelector('.uhd-account__navbtn').click()`)
await sleep(250)
s = await evalJson(stateExpr)
check('390 open is dialog', s.dialog && s.btn?.expanded === 'true')
check('390 drawer fixed overlay wider than 68', s.drawer?.position === 'fixed' || s.side?.w > 100, JSON.stringify({ drawer: s.drawer, side: s.side }))
check('390 open main still 390', s.main?.w === 390 && s.main?.x === 0, JSON.stringify(s.main))
check('390 labels ignore desktop collapse', s.hasOverview === true, String(s.labels))
check('390 background inert', s.mainInert && s.topInert && s.dockInert)
check('390 drawer itself not inert', s.drawerInert === false && s.modalInert === false)
check('390 pre-existing inert kept', s.already === true)
check('390 focus entered drawer', s.active === '关闭导航' && s.activeInDrawer)
check('390 overflow locked', s.overflow === 'hidden')
await snap('regress-390-open')
await key('Tab', true)
s = await evalJson(stateExpr)
check('390 shift-tab stays in drawer', s.activeInDrawer, s.active)
const afterShift = s.active
await key('Tab', false)
s = await evalJson(stateExpr)
check('390 tab from end returns inside', s.activeInDrawer && s.active !== afterShift, `${afterShift} -> ${s.active}`)
await key('Escape')
await sleep(150)
s = await evalJson(stateExpr)
check('390 esc closes', s.btn?.expanded === 'false' && !s.dialog)
check('390 esc restores focus', s.active === '导航', s.active)
check('390 esc clears inert', !s.mainInert && !s.topInert && !s.dockInert)
check('390 esc restores prior inert', s.already === true)
check('390 esc restores overflow', s.overflow === '', JSON.stringify(s.overflow))
await snap('regress-390-after-esc')

await act(`document.querySelector('.uhd-account__navbtn').click()`)
await sleep(200)
await act(`document.querySelector('.uhd-account__scrim').click()`)
await sleep(150)
s = await evalJson(stateExpr)
check('390 scrim closes and restores', s.btn?.expanded === 'false' && !s.mainInert && s.overflow === '' && s.active === '导航')

await act(`document.querySelector('.uhd-account__navbtn').click()`)
await sleep(200)
await act(`[...document.querySelectorAll('.uhd-side__item')].find((el) => el.innerText.includes('我的概览')).click()`)
await sleep(250)
s = await evalJson(stateExpr)
check('same route closes to opener', s.path === '/game/account' && !s.dialog && s.active === '导航', s.path + ' ' + s.active)

await act(`document.querySelector('.uhd-account__navbtn').click()`)
await sleep(200)
await act(`[...document.querySelectorAll('.uhd-side__item')].find((el) => el.innerText.includes('接线员')).click()`)
await sleep(300)
s = await evalJson(stateExpr)
check('cancelled navigation keeps drawer', s.path === '/game/account' && s.dialog && s.btn?.expanded === 'true', s.path + ' ' + s.active)
check('cancelled focus stays in drawer', s.activeInDrawer, s.active)

await act(`[...document.querySelectorAll('.uhd-side__item')].find((el) => el.innerText.includes('我的钱包')).click()`)
await sleep(300)
s = await evalJson(stateExpr)
check('successful navigation focuses main', s.path === '/wallet' && !s.dialog && s.activeIsMain, s.path + ' ' + s.active)
check('successful navigation clears inert and overflow', !s.mainInert && !s.topInert && s.overflow === '')

await size(768, 800)
s = await evalJson(stateExpr)
check('768 still mobile control', s.btn?.display === 'inline-flex')
await size(769, 800)
await sleep(200)
s = await evalJson(stateExpr)
check('769 desktop control hidden', s.btn?.display === 'none')
check('769 collapse preference restored to 68', s.side && s.side.w >= 60 && s.side.w <= 80, JSON.stringify(s.side))
check('769 labels hidden again', s.hasOverview === false)

await act(`document.querySelector('.uhd-side__collapse').click()`)
await sleep(350)
s = await evalJson(stateExpr)
check('769 expand restores wide sidebar', s.side && s.side.w >= 240, JSON.stringify(s.side))

await send('Page.navigate', { url: 'http://127.0.0.1:5199/landing' })
await sleep(1800)
await size(390, 844)
await sleep(300)
s = await evalJson(stateExpr)
const overflowBefore = s.overflow
check('landing 390 no menu yet', !s.sheet)
await act(`document.querySelector('.m-nav__burger').click()`)
await sleep(400)
s = await evalJson(stateExpr)
check('landing menu open', s.sheet && s.dialog)
check('landing overflow locked', s.overflow === 'hidden')
check('landing background inert', s.topInert && s.dockInert)
check('landing sheet not inert', s.sheetInert === false && s.menuModalInert === false)
check('landing focus inside menu', s.activeInSheet, s.active)
await snap('regress-landing-390-open')
await key('Tab', true)
s = await evalJson(stateExpr)
check('landing shift-tab stays in menu', s.activeInSheet, s.active)
const landEdge = s.active
await key('Tab', false)
s = await evalJson(stateExpr)
check('landing tab wraps inside menu', s.activeInSheet && s.active !== landEdge, `${landEdge} -> ${s.active}`)

await size(901, 800)
await sleep(300)
s = await evalJson(stateExpr)
check('901 closes landing menu', !s.sheet)
check('901 restores overflow', s.overflow === overflowBefore, JSON.stringify(s.overflow))
check('901 clears inert', !s.topInert && !s.dockInert && s.already === true)
await snap('regress-landing-901-closed')

await size(900, 800)
await sleep(200)
await act(`document.querySelector('.m-nav__burger').click()`)
await sleep(350)
s = await evalJson(stateExpr)
check('900 can open menu', !!s.sheet && s.overflow === 'hidden')
await act(`window.__fixtureGo('/game/account')`)
await sleep(500)
await size(390, 844)
s = await evalJson(stateExpr)
const life = await evalJson(`JSON.stringify({ ld: window.__ld || 0, lu: window.__lu || 0 })`)
check('keepalive deactivates landing', life.ld >= 1 && life.lu === 0, JSON.stringify(life))
check('leave landing restores overflow', s.overflow === '', JSON.stringify(s.overflow))
check('leave landing clears chrome inert', !s.topInert && !s.dockInert)

const failed = checks.filter((c) => !c.pass)
const report = { pass: failed.length === 0, failed: failed.length, checks }
writeFileSync(`${OUT}/pr192-nav-regress.json`, JSON.stringify(report, null, 2))
writeFileSync(`${STORE}/pr192-nav-regress.json`, JSON.stringify(report, null, 2))
console.log(`\n${checks.length - failed.length}/${checks.length} passed`)
ws.close()
chrome.kill('SIGKILL')
process.exit(failed.length ? 1 : 0)
