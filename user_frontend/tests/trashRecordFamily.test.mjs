// 垃圾桶语义族契约测试（2026-11-18 UHDNow 差异定案·垃圾桶语义族批）：
// 运行：cd user_frontend && node tests/trashRecordFamily.test.mjs
// 定案（reports/uhdnow-gap-decisions-20261118.md 垃圾桶族总纲）：四处垃圾桶
// 统一语义＝「移除观看记录」——只清播放进度与已看标记、不删条目不删文件、
// 点按二次确认；移除必须走真接口 Emby DELETE PlayedItems（setPlayed(false)），
// 不许拿门户 hide 冒充。覆盖五条：
//  G-103 详情页第二副钮互斥状态机：有观看记录→垃圾桶、无记录→铃铛，二者不同时出现；
//  G-105 电影随 G-103：无记录只有收藏一枚、有记录才加垃圾桶（铃铛本就剧集专属）；
//  G-111 季头补「移除整季」钮：清本季全部观看记录，确认弹窗写明「只清观看记录，不删片源」；
//  G-119 每集行右钮双态：未看集＝眼睛（标记已看）、已看集＝垃圾桶（移除该集记录）；
//  G-140 选集弹层头图右钮双态：未看＝眼睛（标记已看）、已看＝垃圾桶（移除记录后变回眼睛）。
// 惯例同 tests/playerForms.test.mjs：node 直接运行的源码契约检查，
// 因为这些形态活在 .vue 里，无法用纯模块单测直接驱动。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const mdv = readFileSync(join(ROOT, 'src/views/panel/MediaDetailView.vue'), 'utf8')
const sel = readFileSync(join(ROOT, 'src/components/panel/SeriesEpisodeList.vue'), 'utf8')
const sheet = readFileSync(join(ROOT, 'src/components/panel/PlayerSchemeSheet.vue'), 'utf8')

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}

/* ---------- G-103 / G-105 详情页副钮互斥状态机 ---------- */
check(
  'M1 铃铛钮与垃圾桶互斥：v-if 带 !canRemoveRecord',
  mdv.includes('v-if="followVisible && !canRemoveRecord"'),
)
check('M2 旧三钮并存写法已删（铃铛不再只判 followVisible）', !mdv.includes('v-if="followVisible"\n'))
check(
  'M3 垃圾桶钮仍以 canRemoveRecord 门控',
  /v-if="canRemoveRecord"[^>]*aria-label="移除观看记录"|aria-label="移除观看记录"/.test(mdv) &&
    mdv.includes('v-if="canRemoveRecord"'),
)
check('M4 「已看完/正在观看」状态标保留', mdv.includes('已看完') && mdv.includes('正在观看'))
check(
  'M5 移除走真接口 setPlayed(false)（DELETE PlayedItems）',
  mdv.includes('embyProtocol.setPlayed(it.Id, false)'),
)
check(
  'M6 铃铛仍为剧集专属（loadFollowState 非 Series 早退）',
  mdv.includes("it.Type !== 'Series'"),
)

/* ---------- G-119 每集行右钮双态 ---------- */
check('S1 SEL 引入 Trash2 图标', sel.includes('Trash2'))
check(
  'S2 已看集行渲染垃圾桶钮（aria-label 移除观看记录）',
  sel.includes('aria-label="移除观看记录"') && sel.includes('<Trash2'),
)
check(
  'S3 垃圾桶钮挂在 row.played 分支',
  /v-if="row\.played"[^>]*class="ep-eye/.test(sel) || /class="ep-eye[^"]*"[^>]*v-if="row\.played"/.test(sel),
)
check(
  'S4 未看集保留眼睛钮「标为已看」',
  sel.includes("'标为已看'") || sel.includes('标为已看'),
)
check(
  'S5 逐集移除先经二次确认（epRemoveRow 状态）',
  sel.includes('epRemoveRow'),
)
check(
  'S6 逐集移除确认后走真接口 setPlayed(false)',
  sel.includes('embyProtocol.setPlayed(row.item.Id, false)'),
)
check(
  'S7 逐集移除后本地回未看态并清断点',
  sel.includes('PlaybackPositionTicks: 0'),
)

/* ---------- G-111 移除整季 ---------- */
check('S8 季头有「移除整季」钮', sel.includes('移除整季'))
check(
  'S9 移除整季钮按本季有无记录门控（seasonHasRecord）',
  sel.includes('seasonHasRecord'),
)
check(
  'S10 整季移除先经二次确认（seasonRemoveSec 状态）',
  sel.includes('seasonRemoveSec'),
)
check(
  'S11 整季确认弹窗写明「只清观看记录，不删片源」',
  sel.includes('只清观看记录，不删片源'),
)
check(
  'S12 整季移除逐集走真接口 setPlayed(false)',
  /confirmRemoveSeason[\s\S]*?setPlayed\(r\.item!\.Id, false\)|setPlayed\([^\n]*\.Id, false\)/.test(sel),
)

/* ---------- G-140 选集弹层头图右钮双态 ---------- */
check('P1 Sheet 引入 Trash2 图标', sheet.includes('Trash2'))
check(
  'P2 已看态头图钮为垃圾桶（aria-label 移除观看记录）',
  sheet.includes('aria-label="移除观看记录"') && sheet.includes('<Trash2'),
)
check(
  'P3 垃圾桶点击只开二次确认、不直接切标记',
  sheet.includes('@click="playedRemoveOpen = true"'),
)
check(
  'P4 未看态头图钮为眼睛（aria-label 标记为已看）',
  sheet.includes("playedState ? '移除观看记录' : '标记为已看'") || sheet.includes('标记为已看'),
)
check(
  'P5 确认移除走真接口 setPlayed(id, false)',
  sheet.includes('embyProtocol.setPlayed(id, false)'),
)
check(
  'P6 弹层确认文案写明「只清观看记录，不删片源」',
  sheet.includes('只清观看记录，不删片源'),
)
check(
  'P7 移除后 playedState 回 false（钮变回眼睛）',
  /confirmRemovePlayed[\s\S]*?playedState\.value = false/.test(sheet),
)

console.log(`\n${passed} passed`)
