// 选版本弹层·不支持网页播放拦截契约测试（G-148/G-152）：
// 运行：cd user_frontend && node tests/schemeDvGate.test.mjs
// 友站实证（录屏 B 帧 f0115/f0224）：拦的不是 4K，是 Dolby Vision——
// DV 版本卡中右有金黄描边小胶囊「不支持网页播放」、该版不可选中、
// 全部版本不可播时开始钮变深灰禁用「暂无可播放版本」；
// 4K HDR10 被选中时开始钮照常彩虹描边可点。
// 判定红线：playable=false 当且仅当 rangeLabel(video)==='Dolby Vision'
// （VideoRange 原值含 dolby，或 DisplayTitle 含 'Dolby Vision'）；
// 绝不按分辨率/码率/大小拦；it.Versions 多版本形态无动态范围字段，恒可播。
// 惯例同 tests/playerForms.test.mjs：node 直接运行的源码契约检查，
// 因为这些形态活在 .vue 里，无法用纯模块单测直接驱动。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const scheme = readFileSync(join(ROOT, 'src/components/panel/PlayerSchemeSheet.vue'), 'utf8')

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}

/* ---------- 判定规则 ---------- */
check(
  'D1 VersionCard 带 playable 字段',
  /interface VersionCard \{[\s\S]*?playable: boolean/.test(scheme),
)
check(
  'D2 source 形态 playable 只由 rangeLabel 判 DV',
  scheme.includes("playable: rangeLabel(video) !== 'Dolby Vision'"),
)
check(
  'D3 rangeLabel 的 VideoRange dolby 分支在位（DV 命中口径）',
  scheme.includes("if (/dolby/i.test(raw)) return 'Dolby Vision'"),
)
check(
  'D4 rangeLabel 的 DisplayTitle 兜底含 Dolby Vision（DV 命中口径）',
  scheme.includes("for (const tok of ['Dolby Vision', 'HDR10+', 'HDR10', 'HLG', 'HDR'])"),
)
check(
  'D5 Versions 形态恒可播（无动态范围字段不拦）',
  scheme.includes('playable: true'),
)
check(
  'D6 playable 判定不引用分辨率/码率/大小（不许按 4K 拦）',
  !/playable:[^\n]*(Height|Width|Bitrate|Size|4K)/.test(scheme),
)

/* ---------- 不可播版本卡形态 ---------- */
check(
  'V1 不可播徽标渲染条件 !v.playable',
  scheme.includes('v-if="!v.playable"'),
)
check('V2 徽标文案「不支持网页播放」', scheme.includes('不支持网页播放'))
check(
  'V3 徽标用金黄描边胶囊样式 psp-ver__dv-badge',
  scheme.includes('psp-ver__dv-badge') && scheme.includes('#d4a017'),
)
check(
  'V4 卡点击走 selectVersion 且内含不可播守卫',
  scheme.includes('@click="selectVersion(v)"') &&
    /function selectVersion\(v: VersionCard\) \{\s*if \(!v\.playable\) return/.test(scheme),
)
check(
  'V5 卡点击不再直写 selectedVersionId',
  !scheme.includes('@click="selectedVersionId = v.id"') &&
    !scheme.includes('@keydown.enter.prevent="selectedVersionId = v.id"'),
)

/* ---------- 默认选中与开始钮 ---------- */
check(
  'S1 默认选中跳过不可播（find playable）',
  scheme.includes('playableCards.find((c) => c.playable)'),
)
check(
  'S2 全部不可播计算在位 noPlayableVersion',
  scheme.includes('const noPlayableVersion = computed') &&
    scheme.includes('versions.value.every((v) => !v.playable)'),
)
check(
  'S3 开始钮在全部不可播时 disabled',
  scheme.includes(':disabled="noPlayableVersion"'),
)
check('S4 开始钮禁用文案「暂无可播放版本」', scheme.includes('暂无可播放版本'))
check(
  'S5 开始钮禁用态样式 psp-start--none（深灰底）',
  scheme.includes('psp-start--none') && scheme.includes('#333338'),
)
check(
  'S6 start() 内有不可播守卫（点击无动作）',
  scheme.includes('if (noPlayableVersion.value) return'),
)
check(
  'S7 版本计数文案不改（仍按 versions.length）',
  scheme.includes('{{ versions.length }} 个可选版本'),
)

console.log(`\n${passed} passed`)
