/**
 * 套餐 / 电影列表 / 详情 / 收藏。真实组件，夹具数据，不是生产登录。
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'

const OUT = '/opt/cursor/artifacts'
const STORE = '/cursor/stores/self/pr192-batch2'
mkdirSync(OUT, { recursive: true })
mkdirSync(STORE, { recursive: true })

const chrome = spawn('google-chrome', [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-sandbox',
  '--user-data-dir=/tmp/chrome-batch2',
  '--remote-debugging-port=9357', 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
for (let i = 0; i < 40; i++) {
  try { if ((await fetch('http://127.0.0.1:9357/json/version')).ok) break } catch { /* */ }
  await sleep(150)
}
const tab = await (await fetch('http://127.0.0.1:9357/json/new?about:blank', { method: 'PUT' })).json()
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
  if (res.result?.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails).slice(0, 700))
  return JSON.parse(res.result.result.value)
}
async function act(expression) {
  const res = await send('Runtime.evaluate', { expression, returnByValue: true })
  if (res.result?.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails).slice(0, 500))
}
async function size(w, h) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 800 })
  await sleep(280)
}
async function go(url) {
  await send('Page.navigate', { url })
  await sleep(900)
}
async function snap(name) {
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  const buf = Buffer.from(shot.result.data, 'base64')
  writeFileSync(`${OUT}/${name}.png`, buf)
  writeFileSync(`${STORE}/${name}.png`, buf)
}
const overflow = `document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1`

await size(1440, 900)
await go('http://127.0.0.1:5199/game/account/subscribe')
await act(`document.documentElement.setAttribute('data-theme','light')`)
await sleep(200)
let s = await evalJson(`(() => {
  const heads = [...document.querySelectorAll('.cmp-plan__name')].map((el) => el.textContent.trim())
  const prices = [...document.querySelectorAll('.cmp-price')].map((el) => el.textContent.trim())
  const rail = document.querySelector('.sub-rail')?.innerText?.replace(/\\s+/g, ' ') || ''
  return JSON.stringify({
    heads, prices, rail,
    cols: document.querySelectorAll('.cmp-colhead').length,
    layout: getComputedStyle(document.querySelector('.sub-layout')).gridTemplateColumns,
    overflow: ${overflow},
  })
})()`)
check('subscribe shows two plans from fixture', s.heads.includes('夹具甲') && s.heads.includes('夹具乙'), s.heads.join(','))
check('subscribe prices come from fixture monthly', s.prices.some((p) => p.includes('10')) && s.prices.some((p) => p.includes('30')), s.prices.join('|'))
check('subscribe rail follows first plan', s.rail.includes('夹具甲'), s.rail.slice(0, 80))
check('subscribe desktop has side column', s.layout.split(' ').length >= 2, s.layout)
check('subscribe 1440 no overflow', s.overflow)
await snap('batch2-subscribe-1440')

await act(`[...document.querySelectorAll('.cmp-colhead')].find((el) => el.innerText.includes('夹具乙')).click()`)
await sleep(200)
s = await evalJson(`JSON.stringify({ rail: document.querySelector('.sub-rail')?.innerText?.replace(/\\s+/g,' ') || '' })`)
check('subscribe select updates summary', s.rail.includes('夹具乙') && s.rail.includes('30'), s.rail.slice(0, 100))
await act(`[...document.querySelectorAll('.sub-cap')].find((el) => el.innerText.includes('年付')).click()`)
await sleep(200)
s = await evalJson(`JSON.stringify({ rail: document.querySelector('.sub-rail')?.innerText?.replace(/\\s+/g,' ') || '', dur: document.querySelector('.sub-dur__value')?.innerText || '' })`)
check('subscribe year cap updates duration', s.dur.includes('12') && s.rail.includes('330'), `${s.dur} ${s.rail.slice(0, 80)}`)

await size(390, 844)
s = await evalJson(`JSON.stringify({ overflow: ${overflow}, oneCol: getComputedStyle(document.querySelector('.sub-layout')).gridTemplateColumns.split(' ').length })`)
check('subscribe 390 stacks', s.oneCol === 1, String(s.oneCol))
check('subscribe 390 no overflow', s.overflow)
await snap('batch2-subscribe-390')

await size(1440, 900)
await go('http://127.0.0.1:5199/library/movies')
s = await evalJson(`(() => {
  const grid = document.querySelector('.lb-grid')
  const cards = [...grid.querySelectorAll(':scope > *')]
  const first = cards[0]?.getBoundingClientRect()
  const cols = new Set(cards.map((el) => Math.round(el.getBoundingClientRect().top))).size
    ? cards.filter((el) => Math.abs(el.getBoundingClientRect().top - first.top) < 4).length
    : 0
  return JSON.stringify({
    cols,
    x: Math.round(first?.x || 0),
    w: Math.round(first?.width || 0),
    h: Math.round(first?.height || 0),
    y: Math.round(first?.y || 0),
    overflow: ${overflow},
    title: document.querySelector('.lb-h1')?.textContent,
  })
})()`)
check('movies 1440 is six columns', s.cols === 6, String(s.cols))
check('movies 1440 starts near sampled inset', s.x >= 20 && s.x <= 48, String(s.x))
check('movies 1440 poster is portrait', s.h > s.w && s.w > 140, `${s.w}x${s.h}`)
check('movies 1440 no overflow', s.overflow)
await snap('batch2-movies-1440')
await act(`[...document.querySelectorAll('.lb-pill')].find((el) => el.innerText.includes('另一类')).click()`)
await sleep(200)
s = await evalJson(`JSON.stringify({ empty: document.querySelector('.lb-empty')?.innerText || '', n: document.querySelectorAll('.lb-grid > *').length })`)
check('movies filter switches category', s.n === 0 && s.empty.includes('电影'), s.empty)

await size(390, 844)
await go('http://127.0.0.1:5199/library/movies')
s = await evalJson(`(() => {
  const cards = [...document.querySelectorAll('.lb-grid > *')]
  const first = cards[0]?.getBoundingClientRect()
  const cols = cards.filter((el) => first && Math.abs(el.getBoundingClientRect().top - first.top) < 4).length
  return JSON.stringify({ cols, overflow: ${overflow} })
})()`)
check('movies 390 is three columns', s.cols === 3, String(s.cols))
check('movies 390 no overflow', s.overflow)
await snap('batch2-movies-390')

await size(1440, 900)
await go('http://127.0.0.1:5199/media/fixture-1')
s = await evalJson(`(() => {
  const title = document.querySelector('.md-title')?.getBoundingClientRect()
  const poster = document.querySelector('.md-poster')?.getBoundingClientRect()
  return JSON.stringify({
    titleTop: Math.round(title?.top || 0),
    posterTop: Math.round(poster?.top || 0),
    wrapX: Math.round(title?.x || 0),
    name: document.querySelector('.md-title')?.textContent,
    fav: !!document.querySelector('.md-btn--fav'),
  })
})()`)
check('detail title is above poster', s.posterTop > s.titleTop + 20, `${s.titleTop} -> ${s.posterTop}`)
check('detail body starts near 96', s.wrapX >= 80 && s.wrapX <= 120, String(s.wrapX))
check('detail has favorite control', s.fav && s.name === '夹具影片', s.name)
await snap('batch2-detail-1440')

await go('http://127.0.0.1:5199/favorites')
const box = await evalJson(`(() => {
  const btn = [...document.querySelectorAll('.fv-tab')].find((el) => (el.textContent || '').includes('电影'))
  const r = btn.getBoundingClientRect()
  return JSON.stringify({ x: r.x + r.width / 2, y: r.y + r.height / 2, t: (btn.textContent || '').trim() })
})()`)
await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 })
await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 })
await sleep(150)
const fav = await evalJson(`JSON.stringify({
  on: document.querySelector('.fv-tab--on')?.textContent?.trim() || '',
  names: [...document.querySelectorAll('.fv-name')].map((el) => el.textContent.trim()),
})`)
check('favorites movie tab filters', fav.on.includes('电影') && fav.names.includes('夹具电影') && !fav.names.includes('夹具剧集'), JSON.stringify({ ...fav, target: box.t }))

const failed = checks.filter((c) => !c.pass)
const report = {
  pass: failed.length === 0,
  failed: failed.length,
  checks,
  note: '夹具数据。套餐价是夹具月价，不是生产价格。媒体海报没有真实图片。不是生产登录。',
  notCovered: ['订阅记录页无完整参考图', '邀请页无完整参考图', '剧集列表复用电影网格，无单独参考图', '收藏页无参考图，只验证页签过滤', '真实生产登录'],
}
writeFileSync(`${OUT}/batch2-fixture.json`, JSON.stringify(report, null, 2))
writeFileSync(`${STORE}/batch2-fixture.json`, JSON.stringify(report, null, 2))
console.log(`\n${checks.length - failed.length}/${checks.length} passed`)
ws.close()
chrome.kill('SIGKILL')
process.exit(failed.length ? 1 : 0)
