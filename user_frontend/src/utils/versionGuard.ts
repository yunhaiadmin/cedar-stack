/**
 * 版本守卫：长开标签页跑旧构建的根治（2026-11-18 事故）。
 *
 * 事故形态：用户手机上一个没关的标签页一直跑着旧构建——「团队管理」
 * 入口 2026-11-16 已删除，旧页点开是 API 404 的 Not Found；求片反馈页
 * 的旧 CSS chunk 已被新部署替换，整页无样式渲染。全新会话生产正常，
 * 生产包里也没有残留：SPA 没有任何版本检测，旧标签页可以无限期跑
 * 下去。2026-11-13 同类事故只修了 index.html 的 no-cache（管新打开
 * 的页），没管已经打开的页，本模块补的就是这一半。
 *
 * 机制：
 * - 构建期 vite 把构建标识（CI 提交 SHA 优先、构建时间戳兜底）写进
 *   index.html 的 <meta name="build-id">；本模块从 document 的 meta
 *   读自己这份值，不另存常量——常量可能与壳不一致，meta 才是真源。
 * - 触发点三处：启动后每 5 分钟、标签页从隐藏切回可见、路由切换；
 *   检查本身 60 秒节流（路由切换频繁，不必每次都拉壳）。
 * - 检查用 fetch('/?_v=<时间戳>', { cache: 'no-store' }) 拉壳，解析
 *   返回 HTML 的 build-id 与自身比对；不同 → 有新版本。
 * - 有新版本只在界面底部弹一条不挡操作的小胶囊「有新版本，点此
 *   刷新」，点击才 location.reload()。绝不自动强刷：用户可能正在
 *   看片或填表。同一版本只提示一次（关掉后不再重复弹）。
 *
 * 与 chunk 恢复的分工：chunk 加载失败的判定与自动刷新仍是
 * utils/chunkRecovery 一套（横切唯一实现，不在这里另写）。本模块
 * 只在 main.ts 的 vite:preloadError 接线前加一道时间窗闸门
 * （handlePreloadError）：10 分钟内已放行过一次自动重载，再失败就
 * 不再自动重载（防新构建本身有问题时无限刷新），改走上面的提示
 * 胶囊；另外 preloadError 是「刚部署换包」的强信号，守卫启动时
 * 另挂一个监听，命中即绕过节流立即查一次版本。
 */
import type { Router } from 'vue-router'
import { isChunkLoadError } from './chunkRecovery'

/** 定时检查间隔：启动后每 5 分钟拉一次壳比对 */
const CHECK_INTERVAL_MS = 5 * 60 * 1000
/** 检查节流：距上次检查不足 60 秒跳过（路由切换触发点专用） */
const CHECK_THROTTLE_MS = 60 * 1000
/** 自动重载防循环时间窗：10 分钟内已放行过自动重载就不再放行 */
const AUTO_RELOAD_WINDOW_MS = 10 * 60 * 1000
/** sessionStorage 键：最近一次放行自动重载的时间戳（跨整页刷新存活） */
const LAST_AUTO_RELOAD_KEY = 'version-guard:last-auto-reload'
const PROMPT_ID = 'version-guard-prompt'

let ownBuildId = ''
let lastCheckAt = 0
/** 已提示过的版本键：同一版本只提示一次 */
let promptedFor: string | null = null
let started = false

/** 自身 build-id：从 document 的 meta 读（见文件头，不另存常量） */
export function readOwnBuildId(): string {
  if (typeof document === 'undefined') return ''
  const content =
    document.querySelector('meta[name="build-id"]')?.getAttribute('content')?.trim() ?? ''
  // 占位符没被构建替换（异常路径）视同没有：守卫整体停用，免得误报
  return content === '%BUILD_ID%' ? '' : content
}

/** 从壳 HTML 里解析 build-id（属性顺序两种都认） */
function parseBuildId(html: string): string {
  const m =
    html.match(/<meta[^>]+name=["']build-id["'][^>]+content=["']([^"']*)["']/i) ??
    html.match(/<meta[^>]+content=["']([^"']*)["'][^>]+name=["']build-id["']/i)
  return (m?.[1] ?? '').trim()
}

/**
 * 新版本提示胶囊：底部居中、浮在底部导航坞上方（坞的底距是安全区
 * +10px、高约 64px，这里留 84px），z-index 900 低于弹层（≥1200）。
 * 点击主体整页刷新；右侧 × 关掉后同一版本不再弹。versionKey 是远端
 * build-id（闸门路径传固定键 'chunk-reload-blocked'），只做去重键。
 */
export function showUpdatePrompt(versionKey: string) {
  if (typeof document === 'undefined') return
  if (promptedFor === versionKey) return
  promptedFor = versionKey
  if (document.getElementById(PROMPT_ID)) return
  const bar = document.createElement('div')
  bar.id = PROMPT_ID
  bar.style.cssText = [
    'position:fixed', 'left:50%', 'transform:translateX(-50%)',
    'bottom:calc(env(safe-area-inset-bottom, 0px) + 84px)',
    'z-index:900', 'display:flex', 'align-items:center', 'gap:2px',
    'padding:5px 6px 5px 14px', 'border-radius:999px',
    'background:var(--au-bg-soft,#11100e)',
    'border:1px solid var(--au-border,rgba(243,237,228,.16))',
    'color:var(--au-text,#f3ede4)', 'font-size:13px',
    'box-shadow:0 8px 32px rgba(0,0,0,.45)',
  ].join(';')
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.textContent = '有新版本，点此刷新'
  btn.style.cssText = [
    'padding:4px 2px', 'border:0', 'background:none', 'cursor:pointer',
    'color:inherit', 'font-size:13px', 'font-weight:600', 'white-space:nowrap',
  ].join(';')
  btn.addEventListener('click', () => window.location.reload())
  const close = document.createElement('button')
  close.type = 'button'
  close.textContent = '×'
  close.setAttribute('aria-label', '关闭')
  close.style.cssText = [
    'padding:2px 7px', 'border:0', 'background:none', 'cursor:pointer',
    'color:inherit', 'font-size:15px', 'line-height:1', 'opacity:.6',
  ].join(';')
  close.addEventListener('click', () => bar.remove())
  bar.append(btn, close)
  document.body.appendChild(bar)
}

/** 拉壳比对一次。force=true 绕过节流（preloadError 强信号专用）。 */
export async function checkForUpdate(opts?: { force?: boolean }): Promise<void> {
  if (!ownBuildId) return
  const now = Date.now()
  if (!opts?.force && now - lastCheckAt < CHECK_THROTTLE_MS) return
  lastCheckAt = now
  try {
    const res = await fetch(`/?_v=${Date.now()}`, { cache: 'no-store' })
    if (!res.ok) return
    const remoteId = parseBuildId(await res.text())
    if (remoteId && remoteId !== ownBuildId) showUpdatePrompt(remoteId)
  } catch { /* 离线/网络异常：不提示，等下一次触发 */ }
}

/**
 * vite:preloadError 闸门（main.ts 在调统一 chunk 恢复前先过这里）：
 * 非 chunk 加载失败返回 false（与我无关，调用方照常处理）；是 chunk
 * 失败时查时间窗——10 分钟内已放行过一次自动重载，返回 true 表示
 * 「已处理、不要再自动重载」，同时改走提示胶囊；不在窗内则记下
 * 本次放行时间并返回 false，由 chunkRecovery 按它自己的预算刷新。
 */
export function handlePreloadError(payload: unknown): boolean {
  if (!isChunkLoadError(payload)) return false
  let last = 0
  try {
    last = Number(sessionStorage.getItem(LAST_AUTO_RELOAD_KEY) ?? 0) || 0
  } catch { /* 隐私模式忽略 */ }
  if (last && Date.now() - last < AUTO_RELOAD_WINDOW_MS) {
    showUpdatePrompt('chunk-reload-blocked')
    return true
  }
  try {
    sessionStorage.setItem(LAST_AUTO_RELOAD_KEY, String(Date.now()))
  } catch { /* 隐私模式忽略 */ }
  return false
}

/** 启动守卫（main.ts 挂载前调用一次）。自身没有 build-id 时整体停用。 */
export function startVersionGuard(router?: Router) {
  if (started) return
  started = true
  ownBuildId = readOwnBuildId()
  if (!ownBuildId) return
  // 启动时刻记为刚检查过：紧随其后的首批路由切换不重复拉壳
  lastCheckAt = Date.now()
  window.setInterval(() => { void checkForUpdate() }, CHECK_INTERVAL_MS)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void checkForUpdate()
  })
  // chunk 预加载失败是「刚部署换包」的强信号：不等节流，立即查一次
  window.addEventListener('vite:preloadError', () => { void checkForUpdate({ force: true }) })
  router?.afterEach(() => { void checkForUpdate() })
}
