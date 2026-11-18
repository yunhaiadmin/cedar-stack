// 版本守卫回归测试（2026-11-18 事故：长开标签页跑旧构建）：
// 运行：cd user_frontend && node tests/versionGuard.test.mjs
// 场景：用户手机上一个没关的旧标签页跑着已删除的「团队管理」入口（点开
// API 404）、求片反馈页因旧 CSS chunk 被新部署替换而无样式渲染。全新会话
// 生产正常——问题在客户端：SPA 没有任何版本检测，旧标签页可无限期跑旧
// 构建。2026-11-13 同类事故只修了 index.html no-cache，没管已打开的页。
// 惯例同 tests/detailLoadRace.test.mjs：node 直接运行的源码契约检查，
// 断言构建注入点、检查器接线与防循环时间窗都在位。
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(ROOT, rel), 'utf8')

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}

// 1) index.html 模板含 build-id meta 注入点（占位符由构建期替换）
const html = read('index.html')
check(
  'index.html 含 build-id meta 注入点',
  html.includes('<meta name="build-id" content="%BUILD_ID%">'),
)

// 2) vite 构建把构建标识（CI 提交 SHA 优先、时间戳兜底）注入壳
const viteConfig = read('vite.config.ts')
check('vite.config 含 transformIndexHtml 注入', viteConfig.includes('transformIndexHtml'))
check('vite.config 替换 %BUILD_ID% 占位符', viteConfig.includes('%BUILD_ID%'))
check('vite.config 优先 CI 提交 SHA', viteConfig.includes('GITHUB_SHA'))

// 3) versionGuard 模块存在且齐件
const guardPath = join(ROOT, 'src/utils/versionGuard.ts')
check('versionGuard 模块存在', existsSync(guardPath))
const guard = readFileSync(guardPath, 'utf8')

// 3a) 自身 build-id 从 document 的 meta 读（不另存常量，免与壳不一致）
check('自身 build-id 读 document meta', guard.includes('meta[name="build-id"]'))
// 3b) no-store 拉壳并带时间戳参数防缓存
check('拉壳走 fetch + ?_v= 时间戳', guard.includes('?_v='))
check('拉壳 cache: no-store', guard.includes("cache: 'no-store'"))
// 3c) 拉回的壳解析 build-id 后与自身比较，不同才提示
check('比较远端与自身 build-id', guard.includes('!== ownBuildId'))
check('不一致走提示路径', guard.includes('showUpdatePrompt'))
// 3d) 三类触发：5 分钟定时、切回可见、路由切换（60 秒节流）
check('5 分钟定时检查', guard.includes('setInterval') && guard.includes('5 * 60 * 1000'))
check('切回可见触发检查', guard.includes('visibilitychange'))
check('路由切换触发检查', guard.includes('afterEach'))
check('检查节流 60 秒', guard.includes('60 * 1000'))
// 3e) 提示胶囊：文案在位、点击整页刷新、不自动强刷
check('提示文案在位', guard.includes('有新版本，点此刷新'))
check('点击提示整页刷新', guard.includes('location.reload()'))
check('同一版本只提示一次', guard.includes('promptedFor'))
// 3f) vite:preloadError 监听（部署换包的强信号，立即触发一次检查）
check('监听 vite:preloadError', guard.includes("'vite:preloadError'"))
// 3g) preloadError 防循环时间窗：sessionStorage 记最近自动重载时间，
//     10 分钟内已重载过就不再重载、改走提示（闸门函数供 main.ts 调用）
check('自动重载时间窗 10 分钟', guard.includes('AUTO_RELOAD_WINDOW_MS') && guard.includes('10 * 60 * 1000'))
check('自动重载时间存 sessionStorage', guard.includes('sessionStorage') && guard.includes('version-guard:last-auto-reload'))
check('preloadError 闸门函数在位', guard.includes('export function handlePreloadError'))
check('闸门命中改走提示路径', guard.includes("showUpdatePrompt('chunk-reload-blocked')"))

// 4) main.ts 接线：启动守卫＋preloadError 先过闸门再走统一 chunk 恢复
const main = read('src/main.ts')
check('main.ts 启动版本守卫', main.includes('startVersionGuard(router)'))
check('main.ts preloadError 先过闸门', main.includes('handlePreloadError'))

console.log(`\n${passed} 项全部通过`)
