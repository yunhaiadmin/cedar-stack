/**
 * SPA chunk 加载失败恢复（横切唯一实现：路由错误 / vite 预加载事件 /
 * 视图内异步组件三处入口共用这一套，不许再各写一份判定与计数）。
 *
 * 事故史：
 * - 2026-10-13（#788/#796）：部署把全部 chunk 换了新哈希，用户旧构建懒加载
 *   旧 chunk → 404 → 页面打不开。初版兜底只挂在 router.onError 上，判定正则
 *   只覆盖 Chrome/Firefox 的动态 import 失败文案，连续失败最多自动刷新
 *   2 次（sessionStorage 计数，导航成功清零），超预算弹重试横条。
 * - 2026-11-13（用户 iPhone 实锤）：部署后旧标签页只剩壳、正文空白，兜底
 *   没触发。根因是 iOS Safari 的失败形态不同：chunk 已被新部署删掉、
 *   服务器 try_files 回了 index.html 时，Safari 报的是模块 MIME 错误
 *   （"Failed to load module script: Expected a JavaScript module script
 *   but the server responded with a MIME type of "text/html"…"），不在原
 *   正则内；且路由子资源的 vite 预加载失败不走 router.onError；视图内
 *   defineAsyncComponent 失败更是静默渲染空。三处一并补齐。
 *
 * 防重复计数：同一次失败可能先后触发 vite:preloadError 与 router.onError
 * （预加载先失败、路由随后报错）。本模块用处理闩锁保证一轮失败只计一次
 * 预算：首次命中即落闩，后续重复事件直接视为已处理；刷新本身会卸载页面，
 * 闩锁不会跨页面存活。导航成功时由 router 调 resetChunkRecoveryState()
 * 连闩锁一起清掉，下一轮部署失败重新计数。
 *
 * 预算清零的两个坑（2026-11-13 真渲染实测抓到）：自动刷新后新页面一启动，
 * 若无条件清零，计数永远涨不到上限、页面无限自动刷新——
 * ① vue-router 对失败的导航同样触发 afterEach，失败路径不许清零
 *   （由 router 侧按 failure 判空后才调 reset）；
 * ② 启动导航成功只证明路由组件加载了，视图内异步 chunk 在那之后才加载，
 *   本页第一个成功导航不清计数，后续成功导航才清（手动点重试横条属于
 *   用户明确要求重来，走 forceClearBudget 强制清零）。
 */
import { useToast } from '@/composables/useToast'

const CHUNK_RELOAD_KEY = 'spa-chunk-reload-count'
const CHUNK_RETRY_BAR_ID = 'spa-chunk-retry'
/** 连续失败最多自动刷新的次数（与初版口径一致，杜绝 index.html 被缓存时死循环） */
const MAX_AUTO_RELOAD = 2

/**
 * chunk / 模块加载失败的错误信息样本（判定宁可偏宽，但只限模块加载语义）：
 * - Chrome/Edge：Failed to fetch dynamically imported module
 * - Firefox：同上 / Loading chunk xxx failed（旧打包器）
 * - Safari 旧版：Importing a module script failed
 * - Safari 新版（chunk 被删、服务器回 index.html）：Failed to load module
 *   script: Expected a JavaScript module script but the server responded
 *   with a MIME type of "text/html"
 * - Firefox 模块 MIME 被拦：disallowed MIME type
 * - webpack 系：ChunkLoadError（按 name 或 message 命中）
 */
const CHUNK_ERROR_RE =
  /Failed to fetch dynamically imported module|Importing a module script failed|Loading chunk [\w-]+ failed|ChunkLoadError|Failed to load module script|Expected a JavaScript|not a valid JavaScript|MIME type of ["']?text\/html|disallowed MIME type/i

/** 是否为 chunk / 模块脚本加载失败（业务错误、接口错误一律不算） */
export function isChunkLoadError(err: unknown): boolean {
  if (!err) return false
  const e = err as Error
  if (e.name === 'ChunkLoadError') return true
  const msg = String(e.message ?? err ?? '')
  return CHUNK_ERROR_RE.test(msg)
}

let recoveryLatched = false

/** 预算耗尽时的手动出口：顶部重试横条（点「重试」清计数后整页刷新） */
function showChunkRetryBar() {
  if (typeof document === 'undefined' || document.getElementById(CHUNK_RETRY_BAR_ID)) return
  const bar = document.createElement('div')
  bar.id = CHUNK_RETRY_BAR_ID
  bar.setAttribute('role', 'alert')
  bar.style.cssText = [
    'position:fixed', 'top:12px', 'left:50%', 'transform:translateX(-50%)',
    'z-index:99999', 'display:flex', 'align-items:center', 'gap:12px',
    'padding:10px 14px', 'border-radius:12px',
    'background:var(--au-bg-soft,#11100e)',
    'border:1px solid var(--au-danger-border,rgba(217,119,107,.28))',
    'color:var(--au-text,#f3ede4)', 'font-size:13px',
    'box-shadow:0 8px 32px rgba(0,0,0,.6)',
  ].join(';')
  const tip = document.createElement('span')
  tip.textContent = '页面没加载出来'
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.textContent = '重试'
  btn.style.cssText = [
    'padding:6px 16px', 'border-radius:8px', 'cursor:pointer',
    'font-size:13px', 'font-weight:700',
    'border:1px solid var(--au-danger,#d9776b)',
    'background:var(--au-danger,#d9776b)', 'color:#fff',
  ].join(';')
  btn.addEventListener('click', () => {
    resetChunkRecoveryState({ forceClearBudget: true })
    window.location.reload()
  })
  bar.append(tip, btn)
  document.body.appendChild(bar)
}

/**
 * 统一恢复入口：命中 chunk 加载失败时，预算内自动整页刷新拿新版
 * index.html；预算耗尽则停刷、弹提示＋重试横条，不再静默停在空白页。
 * 返回 true 表示已按 chunk 失败处理（含重复事件被闩锁吞掉的情况）；
 * 非 chunk 错误返回 false，调用方自行处理。
 */
export function handleChunkLoadError(err: unknown): boolean {
  if (!isChunkLoadError(err)) return false
  // 同一轮失败的重复触发（preloadError 与 onError 先后到达）只处理一次，
  // 避免预算被瞬间耗光或重复弹条。
  if (recoveryLatched) return true
  recoveryLatched = true
  let n = 0
  try {
    n = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) ?? 0) || 0
  } catch { /* 隐私模式忽略 */ }
  if (n < MAX_AUTO_RELOAD) {
    try {
      sessionStorage.setItem(CHUNK_RELOAD_KEY, String(n + 1))
    } catch { /* 隐私模式忽略 */ }
    window.location.reload()
  } else {
    useToast().error('页面没加载出来，点重试重新加载', 10000)
    showChunkRetryBar()
  }
  return true
}

let firstSuccessResetSeen = false

/**
 * 导航成功后调用：放开闩锁、收重试横条，并清刷新计数——但本页第一个
 * 成功导航（启动导航）不清计数：它成功只说明路由组件加载了，视图内
 * 异步 chunk 的成败还没验过，清了会让刚累积的失败预算被误清、上限
 * 永远达不到（见文件头「预算清零的两个坑」）。手动重试走 forceClearBudget。
 */
export function resetChunkRecoveryState(opts?: { forceClearBudget?: boolean }) {
  recoveryLatched = false
  if (typeof document !== 'undefined') {
    document.getElementById(CHUNK_RETRY_BAR_ID)?.remove()
  }
  if (!opts?.forceClearBudget) {
    if (!firstSuccessResetSeen) {
      firstSuccessResetSeen = true
      return
    }
  }
  try {
    sessionStorage.removeItem(CHUNK_RELOAD_KEY)
  } catch { /* 隐私模式忽略 */ }
}
