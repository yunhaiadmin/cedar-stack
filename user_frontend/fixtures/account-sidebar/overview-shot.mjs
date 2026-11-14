/**
 * 概览夹具。挂真实 PersonalCenterView，不读生产登录，接口失败就停在空态。
 * 只量双栏/单栏和横向溢出。
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'

const OUT = '/opt/cursor/artifacts'
const STORE = '/cursor/stores/self/pr192-batch2'
mkdirSync(OUT, { recursive: true })
mkdirSync(STORE, { recursive: true })

const chrome = spawn('google-chrome', [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-sandbox',
  '--user-data-dir=/tmp/chrome-overview-shot',
  '--remote-debugging-port=9356', 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
for (let i = 0; i < 50; i++) {
  try { if ((await fetch('http://127.0.0.1:9356/json/version')).ok) break } catch { /* */ }
  await sleep(150)
}
const tab = await (await fetch('http://127.0.0.1:9356/json/new?about:blank', { method: 'PUT' })).json()
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
  if (res.result?.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails).slice(0, 600))
  return JSON.parse(res.result.result.value)
}
async function size(w, h) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 800 })
  await sleep(300)
}
async function snap(name) {
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  const buf = Buffer.from(shot.result.data, 'base64')
  writeFileSync(`${OUT}/${name}.png`, buf)
  writeFileSync(`${STORE}/${name}.png`, buf)
}

await send('Page.navigate', { url: 'http://127.0.0.1:5199/overview-fixture' })
await sleep(1600)

const measure = `(() => {
  const hero = document.querySelector('.ov-hero')
  const cols = hero ? getComputedStyle(hero).gridTemplateColumns.split(' ').filter(Boolean).length : 0
  const title = document.querySelector('.ov-title')?.textContent?.trim() || ''
  const meter = document.querySelector('.ov-hero__meter')?.innerText?.replace(/\\s+/g, ' ').slice(0, 80) || ''
  return JSON.stringify({
    title,
    cols,
    meter,
    hasCard: !!document.querySelector('.ov-card'),
    hasRenew: document.body.innerText.includes('续费订阅'),
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
    fakeGb: /\\d+\\s*GB/.test(document.body.innerText),
  })
})()`

await size(1440, 900)
let s = await evalJson(measure)
check('1440 title', s.title === '概览', s.title)
check('1440 two columns', s.cols === 2, String(s.cols))
check('1440 card and renew', s.hasCard && s.hasRenew, s.meter)
check('1440 no horizontal overflow', s.scroll <= s.client + 1, `${s.scroll}/${s.client}`)
check('1440 empty fixture does not invent GB', s.fakeGb === false, s.meter)
await snap('overview-fixture-1440')

await size(390, 844)
s = await evalJson(measure)
check('390 one column', s.cols === 1, String(s.cols))
check('390 no horizontal overflow', s.scroll <= s.client + 1, `${s.scroll}/${s.client}`)
check('390 title and card', s.title === '概览' && s.hasCard)
await snap('overview-fixture-390')

const failed = checks.filter((c) => !c.pass)
const report = {
  pass: failed.length === 0,
  failed: failed.length,
  checks,
  note: '夹具挂真实概览组件。没有生产登录，订阅/流量/余额接口失败后停在空态。空态不出现 GB 数字。',
}
writeFileSync(`${OUT}/overview-fixture.json`, JSON.stringify(report, null, 2))
writeFileSync(`${STORE}/overview-fixture.json`, JSON.stringify(report, null, 2))
console.log(`\n${checks.length - failed.length}/${checks.length} passed`)
ws.close()
chrome.kill('SIGKILL')
process.exit(failed.length ? 1 : 0)
