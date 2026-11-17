// 详情页切条目竞态回归测试（MediaDetailView load() 无序号守卫）：
// 运行：cd user_frontend && node tests/detailLoadRace.test.mjs
// 场景：详情页 A → B 快速切换，A 的 getItem 慢回包；旧响应不许覆盖新条目，
// 旧 load 的 error/finally 与三个子加载（推荐/续看/追剧）不许污染新条目状态。
// 惯例同 tests/playerPrefs.test.mjs：node 直接运行，不引入 vitest/jest；
// 本文件是源码契约检查（与 scripts/check_*.py 同类）：断言守卫接线点都在位，
// 因为竞态守卫活在 .vue 的 <script> 里，无法用纯模块单测直接驱动。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = readFileSync(join(ROOT, 'src/views/panel/MediaDetailView.vue'), 'utf8')
const script = src.slice(src.indexOf('<script'), src.indexOf('</script>'))

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}

/** 取函数体：从签名起，到下一个顶层 async function / const / watch 为止 */
function bodyOf(signature, nextMarkers) {
  const start = script.indexOf(signature)
  assert.ok(start !== -1, `找不到 ${signature}`)
  let end = script.length
  for (const m of nextMarkers) {
    const i = script.indexOf(m, start + signature.length)
    if (i !== -1) end = Math.min(end, i)
  }
  return script.slice(start, end)
}

function count(hay, needle) {
  return hay.split(needle).length - 1
}

// 1) 守卫存在：load() 入口取号（与 LibraryBrowseView/SearchView 的 seq 同型）
check('loadSeq 声明在位', script.includes('let loadSeq = 0'))
const loadBody = bodyOf('async function load() {', ['\nasync function toggleFavorite'])
check('load() 入口取号', loadBody.includes('const my = ++loadSeq'))

// 2) 旧 getItem 慢回包不许覆盖：赋值 item.value 之前先验号
const getItemAt = loadBody.indexOf('await embyProtocol.getItem(itemId.value)')
assert.ok(getItemAt !== -1, 'load() 里找不到 getItem 调用')
const assignAt = loadBody.indexOf('item.value = ', getItemAt)
assert.ok(assignAt !== -1, 'load() 里找不到 item.value 赋值')
check(
  '旧 getItem 回包先验号再赋值',
  loadBody.slice(getItemAt, assignAt).includes('if (my !== loadSeq) return'),
)

// 3) 旧 load 的 error 不许清空新条目：catch 里先验号再清 item/写 error
const catchAt = loadBody.indexOf('} catch (err) {')
assert.ok(catchAt !== -1, 'load() 里找不到 catch')
const clearAt = loadBody.indexOf('item.value = null', catchAt)
assert.ok(clearAt !== -1, 'catch 里找不到 item.value = null')
check(
  '旧 load 的 catch 先验号再清条目',
  loadBody.slice(catchAt, clearAt).includes('if (my !== loadSeq) return'),
)

// 4) 旧 load 的 finally 不许提前撤 loading：只当号仍是最新才清
const finallyAt = loadBody.indexOf('} finally {', catchAt)
assert.ok(finallyAt !== -1, 'load() 里找不到 finally')
check(
  '旧 load 的 finally 只当最新才清 loading',
  loadBody.slice(finallyAt).includes('if (my === loadSeq)') &&
    loadBody.indexOf('loading.value = false', finallyAt) !== -1,
)

// 5) 三个子加载接上同一号：调用时传 my，落共享状态前验号
check('推荐子加载调用传号', loadBody.includes('loadRecommendations(item.value, my)'))
check('续看子加载调用传号', loadBody.includes('loadResume(my)'))
check('追剧子加载调用传号', loadBody.includes('loadFollowState(my)'))
const recBody = bodyOf('async function loadRecommendations(', ['\nasync function load() {'])
check('推荐子加载落状态前验号', recBody.includes('if (my !== loadSeq) return'))
const resumeBody = bodyOf('async function loadResume(', ['\n/** 播放主按钮文案'])
check('续看子加载落状态前验号', resumeBody.includes('if (my !== loadSeq) return'))
const followBody = bodyOf('async function loadFollowState(', ['\nasync function toggleFollow'])
check(
  '追剧子加载两次 await 后都验号',
  count(followBody, 'if (my !== loadSeq) return') >= 2,
)

// 5b) 第四个子加载（FE-14 弹幕）同样不许旧响应污染新条目：load() 里调用，
// 它用条目 id 自守（await 后比对 item.value?.Id 与调用时记下的 id）
check('弹幕子加载在 load() 里调用', loadBody.includes('void loadDanmaku()'))
const danmakuBody = bodyOf('async function loadDanmaku(', ['\nconst danmakuNote'])
check(
  '弹幕子加载落状态前验条目 id',
  danmakuBody.includes('if (item.value?.Id !== it.Id) return'),
)

// 6) 切条目必走新 load：watch(itemId) 触发 load()，每次取新号
const watchAt = script.indexOf('watch(itemId, () => {')
assert.ok(watchAt !== -1, '找不到 watch(itemId)')
check('切条目触发 load()', script.slice(watchAt, watchAt + 200).includes('void load()'))

console.log(`\nRESULT: PASS (${passed} 项)`)
