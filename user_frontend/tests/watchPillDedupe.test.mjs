// 「网页观影」胶囊去重契约（2026-11-18）：账户区胶囊由 App.vue 的
// UhdTopBar（内挂 UhdWatchPill）全区统一承接，各页不得再自带手写胶囊。
// 运行：cd user_frontend && node tests/watchPillDedupe.test.mjs
// 背景：2026-11-14 顶栏统一单已定版此口径（InviteFriendsView、
// SubscriptionRecordsView 当时已删自带钮并留注释）。2026-11-15 按作品
// 组织重做时 RequestFeedbackView / RequestMediaDetailView「照友站」新写
// 顶栏绕开口径，两页各多出一颗手写胶囊（.rf-watch / .rmd-watch），与
// 统一顶栏的常驻胶囊上下叠出两颗——用户 2026-11-18 真机截图抓现行。
// 本契约锁死：两页不再有手写胶囊标记/样式/直达链接；全站视图层
// （剥注释后）不再出现「网页观影」字样（胶囊只许 UhdWatchPill 一份）。
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(ROOT, p), 'utf8')

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(vue|css)$/.test(name)) out.push(p)
  }
  return out
}
const stripComments = (t) =>
  t.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\*[\s\S]*?\*\//g, '')

const rf = read('src/views/game/RequestFeedbackView.vue')
const rmd = read('src/views/game/RequestMediaDetailView.vue')

// 两页：手写胶囊的类名（模板＋样式＋浅色覆盖）清零
check('RF 无 rf-watch 残留', !rf.includes('rf-watch'))
check('RMD 无 rmd-watch 残留', !rmd.includes('rmd-watch'))
// 两页：不再自带直达 /library 的胶囊链接
check('RF 无 to="/library" 直达链接', !rf.includes('to="/library"'))
check('RMD 无 to="/library" 直达链接', !rmd.includes('to="/library"'))
// 两页：顶栏的返回＋标题保留（只删胶囊，不动顶栏本体）
check('RF 顶栏保留（rf-topbar＋rf-back）', rf.includes('rf-topbar') && rf.includes('rf-back'))
check('RMD 顶栏保留（rmd-topbar＋rmd-back）', rmd.includes('rmd-topbar') && rmd.includes('rmd-back'))

// 全站扫描：类名不许在任何 .vue/.css 里复活
for (const f of walk(join(ROOT, 'src'))) {
  const t = readFileSync(f, 'utf8')
  check(`全站无 rf-watch/rmd-watch：${f.slice(ROOT.length + 1)}`, !t.includes('rf-watch') && !t.includes('rmd-watch'))
}
// 全站视图层：剥注释后不再有「网页观影」字样（胶囊文案只许统一组件持有）
for (const f of walk(join(ROOT, 'src/views'))) {
  const t = stripComments(readFileSync(f, 'utf8'))
  check(`视图层无手写「网页观影」：${f.slice(ROOT.length + 1)}`, !t.includes('网页观影'))
}

// 保留方仍在：统一顶栏挂载点与胶囊组件本体不动
const app = read('src/App.vue')
check('App.vue 账户区仍挂 UhdTopBar', app.includes('<UhdTopBar') && app.includes('isAccountArea'))
check('UhdTopBar 仍挂 UhdWatchPill', read('src/components/uhd/UhdTopBar.vue').includes('<UhdWatchPill'))

console.log(`\nwatchPillDedupe: ${passed} passed`)
