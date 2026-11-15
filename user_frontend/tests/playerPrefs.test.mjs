// ⚠️11 回归测试：网页播放器设置同步模块（src/api/playerPrefs.ts）。
// 运行：cd user_frontend && node tests/playerPrefs.test.mjs
// 惯例同 userLogout.test.mjs：node 直接运行，不引入 vitest/jest；
// @/api 由 playerPrefs.hooks.mjs 重定向到 tests/stubs/playerPrefsApi.mjs。
import { register } from 'node:module'
import assert from 'node:assert'

register('./playerPrefs.hooks.mjs', import.meta.url)

const ls = new Map()
globalThis.localStorage = {
  getItem: (k) => (ls.has(k) ? ls.get(k) : null),
  setItem: (k, v) => ls.set(k, String(v)),
  removeItem: (k) => ls.delete(k),
}
globalThis.window = globalThis

const prefs = await import('../src/api/playerPrefs.ts')
const apiStub = (await import('./stubs/playerPrefsApi.mjs')).default

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}
function reset() {
  ls.clear()
  apiStub.__reset()
}

const DM = {
  enabled: true, area: 'half', opacity: 80, fontSize: 'small',
  density: 'low', speed: 'fast', offsetSeconds: 2,
}
const NEW_KEY = prefs.PLAYER_PREFS_LOCAL_KEY
const OLD_KEY = prefs.LEGACY_DANMAKU_LOCAL_KEY

// 1) 本地读：空 → {}；新键直读；旧键 danmaku 补入；新键优先于旧键。
reset()
check('load empty', prefs.loadLocalPlayerPrefs(), {})
ls.set(OLD_KEY, JSON.stringify(DM))
check('load folds legacy danmaku', prefs.loadLocalPlayerPrefs(), { danmaku: DM })
ls.set(NEW_KEY, JSON.stringify({ rate: 1.5 }))
check('load new key + legacy danmaku', prefs.loadLocalPlayerPrefs(), { rate: 1.5, danmaku: DM })
ls.set(NEW_KEY, JSON.stringify({ danmaku: { ...DM, opacity: 50 } }))
check('new key danmaku wins over legacy', prefs.loadLocalPlayerPrefs().danmaku.opacity, 50)
ls.set(NEW_KEY, '{broken')
check('broken local falls back to legacy', prefs.loadLocalPlayerPrefs(), { danmaku: DM })

// 2) 本地写：写新键 + danmaku 镜像旧键。
reset()
prefs.saveLocalPlayerPrefs({ danmaku: DM, rate: 2 })
check('save writes new key', JSON.parse(ls.get(NEW_KEY)), { danmaku: DM, rate: 2 })
check('save mirrors legacy danmaku', JSON.parse(ls.get(OLD_KEY)), DM)

// 3) 合并：顶层键粒度本地优先，本地缺的用服务端补。
check(
  'merge local-first per top-level key',
  prefs.mergePlayerPrefs(
    { rate: 1.5 },
    { danmaku: DM, rate: 2, subtitle: { fontSize: 'large', delayMs: 100 } },
  ),
  { danmaku: DM, rate: 1.5, subtitle: { fontSize: 'large', delayMs: 100 } },
)

// 4) 同步：服务端补齐本地缺的键并写回本地；本地已有的不被覆盖。
reset()
ls.set(NEW_KEY, JSON.stringify({ rate: 1.5 }))
apiStub.__setGetResponse({ preferences: { danmaku: DM, rate: 2 } })
{
  const merged = await prefs.syncPlayerPrefsFromServer()
  check('sync merges remote gaps', merged, { danmaku: DM, rate: 1.5 })
  check('sync writes merged back locally', JSON.parse(ls.get(NEW_KEY)), { danmaku: DM, rate: 1.5 })
  check('sync used prefs url', apiStub.calls[0].url, '/api/user/player-preferences')
}

// 5) 同步：服务端从未存过（null）→ 原样返回本地，不写本地。
reset()
ls.set(NEW_KEY, JSON.stringify({ rate: 1.25 }))
apiStub.__setGetResponse({ preferences: null })
{
  const merged = await prefs.syncPlayerPrefsFromServer()
  check('sync null remote returns local', merged, { rate: 1.25 })
}

// 6) 同步：拉取失败静默 → 返回本地、不抛。
reset()
ls.set(NEW_KEY, JSON.stringify({ rate: 0.75 }))
apiStub.__setGetFails(true)
{
  const merged = await prefs.syncPlayerPrefsFromServer()
  check('sync failure falls back to local', merged, { rate: 0.75 })
}

// 7) 防抖回推：连调多次只 PUT 一次且是最后一份；失败静默不抛。
reset()
prefs.schedulePushPlayerPrefs({ rate: 1 })
prefs.schedulePushPlayerPrefs({ rate: 2 })
await new Promise((r) => setTimeout(r, 1000))
{
  const puts = apiStub.calls.filter((c) => c.method === 'put')
  check('debounced push fires once', puts.length, 1)
  check('debounced push sends latest', puts[0].body, { rate: 2 })
}
apiStub.__setPutFails(true)
prefs.schedulePushPlayerPrefs({ rate: 3 })
await new Promise((r) => setTimeout(r, 1000))
check('failed push swallowed', apiStub.calls.filter((c) => c.method === 'put').length, 2)

console.log(`\n${passed} passed`)
