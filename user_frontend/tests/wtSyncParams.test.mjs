// G-192~G-199 回归测试：一起看同步参数模块（src/utils/wtSyncParams.ts）。
// 运行：cd user_frontend && node tests/wtSyncParams.test.mjs
// 惯例同 playerPrefs.test.mjs：node 直接运行，不引入 vitest/jest；
// 本模块无 @/ 别名依赖，不需要 hooks 重定向。
import assert from 'node:assert'

const ls = new Map()
globalThis.localStorage = {
  getItem: (k) => (ls.has(k) ? ls.get(k) : null),
  setItem: (k, v) => ls.set(k, String(v)),
  removeItem: (k) => ls.delete(k),
}
globalThis.window = globalThis

const mod = await import('../src/utils/wtSyncParams.ts')
const { WT_SYNC_DEFAULTS, WT_SYNC_LOCAL_KEY } = mod

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

// 1) 默认值照友站定案（G-194~G-198）：1000/200/3000/5/3000。
check('defaults', WT_SYNC_DEFAULTS, {
  startMs: 1000,
  endMs: 200,
  directMs: 3000,
  ratePct: 5,
  intervalMs: 3000,
})

// 2) 本地持久化：空 → 默认；存后读回一致；坏 JSON → 默认。
ls.clear()
check('load empty -> defaults', mod.loadWtSyncParams(), WT_SYNC_DEFAULTS)
const custom = { startMs: 800, endMs: 150, directMs: 4000, ratePct: 8, intervalMs: 2000 }
check('save returns sanitized', mod.saveWtSyncParams(custom), custom)
check('load saved', mod.loadWtSyncParams(), custom)
check('stored under key', JSON.parse(ls.get(WT_SYNC_LOCAL_KEY)), custom)
ls.set(WT_SYNC_LOCAL_KEY, '{broken')
check('broken json -> defaults', mod.loadWtSyncParams(), WT_SYNC_DEFAULTS)

// 3) 消毒：非数回落默认、越界钳制、结束纠偏不大于开始纠偏。
check(
  'sanitize junk fields',
  mod.sanitizeWtSyncParams({ startMs: 'x', endMs: -5, directMs: 1e9, ratePct: 0, intervalMs: 2500.6 }),
  { startMs: 1000, endMs: 0, directMs: 60000, ratePct: 1, intervalMs: 2501 },
)
check(
  'sanitize end>start clamps end to start',
  mod.sanitizeWtSyncParams({ startMs: 500, endMs: 900 }),
  { startMs: 500, endMs: 500, directMs: 3000, ratePct: 5, intervalMs: 3000 },
)
check('sanitize null', mod.sanitizeWtSyncParams(null), WT_SYNC_DEFAULTS)

// 4) 分段纠偏决策（G-194~G-197）：P = 默认参数，base = 房主倍速 1。
const P = WT_SYNC_DEFAULTS
const D = (diffMs, correcting, baseRate = 1) =>
  mod.decideWtDrift({ diffMs, correcting, params: P, baseRate })

check('within start band: none', D(500, false), { action: 'none', rate: 1, correcting: false })
check('at start boundary: none', D(1000, false), { action: 'none', rate: 1, correcting: false })
check('beyond start: rate up when behind', D(1500, false), { action: 'rate', rate: 1.05, correcting: true })
check('beyond start: rate down when ahead', D(-1500, false), { action: 'rate', rate: 0.95, correcting: true })
check('correcting mid-band keeps rate', D(800, true), { action: 'rate', rate: 1.05, correcting: true })
check('correcting flips sign keeps correcting', D(-800, true), { action: 'rate', rate: 0.95, correcting: true })
check('correcting within end band: restore', D(200, true), { action: 'restore', rate: 1, correcting: false })
check('correcting small diff: restore', D(-120, true), { action: 'restore', rate: 1, correcting: false })
check('beyond direct: seek', D(3001, false), { action: 'seek', rate: 1, correcting: false })
check('beyond direct while correcting: seek + stop', D(-4500, true), { action: 'seek', rate: 1, correcting: false })
check('at direct boundary: rate not seek', D(3000, false), { action: 'rate', rate: 1.05, correcting: true })

// 5) 基准倍速消费锚点 rate（G-197）：房主 1.25 倍时纠偏在 1.25 上下浮动。
check('base rate from anchor', D(1500, false, 1.25), { action: 'rate', rate: 1.3125, correcting: true })
check('restore returns to anchor rate', D(100, true, 1.25), { action: 'restore', rate: 1.25, correcting: false })
check('junk base rate falls back to 1', D(1500, false, 0), { action: 'rate', rate: 1.05, correcting: true })

// 6) 目标进度外推（G-198 自查用）：播放中按锚点 rate 推进，暂停不推进，时钟回拨不倒退。
const anchor = { position: 100, playing: true, rate: 1, ts: 1000 }
check('extrapolate playing', mod.wtTargetPosition(anchor, 1005000), 105)
check('extrapolate paused', mod.wtTargetPosition({ ...anchor, playing: false }, 1005000), 100)
check('extrapolate rate 2', mod.wtTargetPosition({ ...anchor, rate: 2 }, 1005000), 110)
check('extrapolate clock skew clamps', mod.wtTargetPosition(anchor, 999000), 100)

console.log(`\n${passed} passed`)
