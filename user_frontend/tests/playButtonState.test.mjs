// 详情页主钮三态测试（2026-11-18 参考站 差异定案 G-99）：
// 运行：cd user_frontend && node tests/playButtonState.test.mjs
// 定案（reports/参考站-gap-decisions-20261118.md G-99）：剧集主钮三态——
//  ① 有未看完的续播点 →「继续播放」（弹层定位该集、起播带续播点）；
//  ② 无续播点但有下一未看集 →「播放下一集」（定位第一个未看集、从头播）；
//  ③ 无观看记录或全看完 →「立即播放」（定位第一集）。
// 电影无下一集概念，恒「立即播放」，三态不许改坏电影形态。
// 判定边界：看到一半＝①（续播优先于②）；某集已看完且后面有未看＝②；
// 全看完＝③；无任何记录＝③；计数拿不到时保守落③（不误报「下一集」）。
// 测法：判定纯函数 playButtonKindOf 活在 MediaDetailView.vue 的标记段里
// （文件范围只动详情页本体），本测试抽取标记段写临时 .ts 真执行五情形，
// 不是抄一份逻辑在测试里自证；另加源码契约断言详情页接线在位。
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const mdv = readFileSync(join(ROOT, 'src/views/panel/MediaDetailView.vue'), 'utf8')
const script = mdv.slice(mdv.indexOf('<script'), mdv.indexOf('</script>'))

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}
function checkEq(name, actual, expected) {
  assert.strictEqual(actual, expected, `${name}（实际 ${JSON.stringify(actual)}）`)
  passed += 1
  console.log(`PASS ${name}`)
}

/* ---------- 抽取纯判定段真执行 ---------- */
const START = '// === G-99 playButtonKindOf 开始 ==='
const END = '// === G-99 playButtonKindOf 结束 ==='
const sIdx = script.indexOf(START)
const eIdx = script.indexOf(END)
check('T0 判定段标记在位（开始/结束）', sIdx !== -1 && eIdx > sIdx)
const block = script.slice(sIdx + START.length, eIdx)
const tmpFile = join(tmpdir(), `playButtonState-${process.pid}.ts`)
writeFileSync(tmpFile, `${block}\nexport { playButtonKindOf, PLAY_BUTTON_LABELS }\n`)
let mod
try {
  mod = await import(pathToFileURL(tmpFile).href)
} finally {
  rmSync(tmpFile, { force: true })
}
const kindOf = mod.playButtonKindOf
const LABELS = mod.PLAY_BUTTON_LABELS
check('T1 判定函数与标签表可执行导出', typeof kindOf === 'function' && !!LABELS)

/* ---------- 五情形（判定边界逐条写清） ---------- */
// ① 续播点：第一集看到一半（已看完 0 集、后面全未看）→ 续播，不是「下一集」
checkEq(
  'S1 第一集看到一半 → resume',
  kindOf({ isSeries: true, hasResumePoint: true, watchedCount: 0, totalCount: 10 }),
  'resume',
)
// ① 续播优先于②：已看完 3 集、第 4 集看到一半、后面还有未看 → 仍续播
checkEq(
  'S2 中间集看到一半（续播优先于下一集） → resume',
  kindOf({ isSeries: true, hasResumePoint: true, watchedCount: 3, totalCount: 10 }),
  'resume',
)
checkEq('S3 resume 文案「继续播放」', LABELS[kindOf({ isSeries: true, hasResumePoint: true, watchedCount: 3, totalCount: 10 })], '继续播放')
// ② 下一未看集：已看完 2 集、无续播点、后面有未看 → 播放下一集（定位第一个未看集）
checkEq(
  'S4 已看完前 2 集且无续播点 → next',
  kindOf({ isSeries: true, hasResumePoint: false, watchedCount: 2, totalCount: 10 }),
  'next',
)
checkEq('S5 next 文案「播放下一集」', LABELS[kindOf({ isSeries: true, hasResumePoint: false, watchedCount: 2, totalCount: 10 })], '播放下一集')
// ③ 全看完：已看完＝总集数 → 立即播放（定位第一集）
checkEq(
  'S6 全看完 → immediate',
  kindOf({ isSeries: true, hasResumePoint: false, watchedCount: 10, totalCount: 10 }),
  'immediate',
)
// ③ 无记录：已看 0 集、无续播点 → 立即播放（第一集）
checkEq(
  'S7 无任何观看记录 → immediate',
  kindOf({ isSeries: true, hasResumePoint: false, watchedCount: 0, totalCount: 10 }),
  'immediate',
)
checkEq('S8 immediate 文案「立即播放」', LABELS.immediate, '立即播放')
// ③ 计数缺失：总集数/未看数拿不到（都 0）→ 保守 immediate，不误报「下一集」
checkEq(
  'S9 集计数拿不到 → immediate',
  kindOf({ isSeries: true, hasResumePoint: false, watchedCount: 0, totalCount: 0 }),
  'immediate',
)
// 电影恒立即播放：哪怕带续播信号也不进三态（无下一集概念）
checkEq(
  'S10 电影（带续播信号） → immediate',
  kindOf({ isSeries: false, hasResumePoint: true, watchedCount: 0, totalCount: 0 }),
  'immediate',
)
checkEq(
  'S11 电影（无记录） → immediate',
  kindOf({ isSeries: false, hasResumePoint: false, watchedCount: 0, totalCount: 0 }),
  'immediate',
)

/* ---------- 详情页接线契约 ---------- */
check('C1 playLabel 由三态标签表驱动', script.includes('PLAY_BUTTON_LABELS[playButtonKind.value]'))
check(
  'C2 三态输入喂续看信号（resumeEntry 或条目断点）',
  script.includes('hasResumePoint:') && script.includes('resumeEntry.value'),
)
check(
  'C3 已看计数走 UnplayedItemCount/RecursiveItemCount 口径',
  script.includes('UnplayedItemCount') && script.includes('RecursiveItemCount'),
)
check('C4 旧主钮长文案「上次看到第X季第X集」已删', !script.includes('上次看到第'))
check(
  'C5 主钮点击仍只开选集弹层（定位在弹层内现行口径）',
  mdv.includes('class="md-btn md-btn--play" @click="sheetOpen = true"'),
)

console.log(`\n${passed} passed`)
