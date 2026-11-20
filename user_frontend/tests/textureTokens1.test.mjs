// 质感令牌级第一批契约测试（2026-11-18）：骨架形态翻转＋播放器面玻璃收编。
// 运行：cd user_frontend && node tests/textureTokens1.test.mjs
// 真源：reports/参考站-texture-gap-table-20261118.md §1 #8/#13/#14、§3 R1/R11。
//  #8 骨架：友站为静止渐变灰（浅态灰值 rgb 233–241），我方原为平板纯色＋
//     opacity 脉冲（sl/fv/lb/md/sv/rf/rmd/wsr/ld-sk-pulse），与自家令牌
//     「禁骨架屏闪烁占位」（ax-tokens.css）自相矛盾——全部翻转为静止
//     渐变灰、摘净脉冲。注：PlayStatusBar 的 psb-pulse 是状态点呼吸、
//     非骨架占位，不在翻转之列。
//  #13/#14 播放器面：WebPlayerView 全部浮件收进 ax 四档玻璃令牌
//     （navpill/pill/slab/float 的 *-filter＋*-shadow＋--ax-glass-d 底），
//     不许再留裸写 backdrop-filter 数值与裸写玻璃底色。准备播放卡后经
//     友站补测实测改口径（2026-11-18 下午）：近黑实底＋顶部渐变高光＋
//     底部亮线＋圆角 20，仅保留 float 外阴影束，见 W11–W15。
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

/* ---------- 骨架令牌（ax-tokens.css） ---------- */
const tokens = read('src/styles/ax-tokens.css')

check('T1 片库骨架令牌 --ax-s-skeleton 已定义（双态两处）',
  (tokens.match(/--ax-s-skeleton:/g) || []).length >= 2)

const lightSkel = tokens.match(/--ax-s-skeleton:\s*linear-gradient\(([^;]+)\);/)
check('T2 浅态骨架为静止 linear-gradient', !!lightSkel)
if (lightSkel) {
  const rgbs = [...lightSkel[1].matchAll(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/g)]
    .map((m) => [Number(m[1]), Number(m[2]), Number(m[3])])
  check('T3 浅态骨架渐变至少两停点', rgbs.length >= 2)
  check('T4 浅态骨架灰值全通道落在 233–241 区间',
    rgbs.every((c) => c.every((v) => v >= 233 && v <= 241)))
}
check('T5 全局骨架令牌 --ax-skel-bg 已定义（深色默认＋浅态覆盖）',
  (tokens.match(/--ax-skel-bg:/g) || []).length >= 2)

/* ---------- 骨架脉冲清零（全 src 扫描） ---------- */
const PULSE_NAMES = ['sl-pulse', 'fv-pulse', 'lb-pulse', 'md-pulse', 'sv-pulse',
  'rf-pulse', 'rmd-pulse', 'wsr-pulse', 'ld-sk-pulse']
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(vue|css)$/.test(name)) out.push(p)
  }
  return out
}
const srcFiles = walk(join(ROOT, 'src'))
for (const name of PULSE_NAMES) {
  const hit = srcFiles.some((f) => readFileSync(f, 'utf8').includes(name))
  check(`P0 骨架脉冲 ${name} 全仓清零`, !hit)
}

/* ---------- 片库面骨架改引令牌 ---------- */
const STREAM_SKEL = [
  'src/views/panel/StreamHomeView.vue',
  'src/views/panel/FavoritesView.vue',
  'src/views/panel/LibraryBrowseView.vue',
  'src/views/panel/MediaDetailView.vue',
  'src/views/panel/SearchView.vue',
]
for (const f of STREAM_SKEL) {
  check(`S1 ${f.split('/').pop()} 骨架引 --ax-s-skeleton`, read(f).includes('var(--ax-s-skeleton)'))
}

/* ---------- 求片/工单面骨架改引全局令牌 ---------- */
const PLAIN_SKEL = [
  'src/components/requests/TicketThread.vue',
  'src/views/game/RequestFeedbackView.vue',
  'src/views/game/RequestMediaDetailView.vue',
  'src/components/requests/WorkSearchResults.vue',
]
for (const f of PLAIN_SKEL) {
  check(`S2 ${f.split('/').pop()} 骨架引 --ax-skel-bg`, read(f).includes('var(--ax-skel-bg)'))
}

/* ---------- 官网骨架：静止渐变（纸面色族保留、脉冲摘除） ---------- */
const landing = read('src/components/ui/LandingSkeleton.vue')
check('S3 LandingSkeleton 骨架块为静止渐变', /\.ld-sk__line\s*\{[^}]*linear-gradient/.test(landing))

/* ---------- 播放器面玻璃收编（WebPlayerView） ---------- */
const wpv = read('src/views/WebPlayerView.vue')
const wpvStyle = wpv.slice(wpv.indexOf('<style'))

check('W1 播放器无裸写 backdrop-filter 数值',
  !/backdrop-filter:\s*blur\(/.test(wpvStyle) && !/-webkit-backdrop-filter:\s*blur\(/.test(wpvStyle))
for (const lit of ['rgba(18, 18, 20, 0.55)', 'rgba(10, 10, 12, 0.78)', 'rgba(10, 10, 12, 0.55)',
  'rgba(22, 22, 25, 0.78)', 'rgba(22, 22, 25, 0.94)', 'rgba(20, 20, 23, 0.88)']) {
  check(`W2 旧玻璃底色 ${lit} 已清零`, !wpvStyle.includes(lit))
}
function blockOf(cls) {
  const m = wpvStyle.match(new RegExp(`\\.${cls}(?![\\w-])[^{]*\\{([^}]*)\\}`))
  return m ? m[1] : ''
}
check('W3 返回钮引 pill 档玻璃（filter＋shadow）',
  blockOf('wp-back').includes('var(--ax-glass-pill-filter)') && blockOf('wp-back').includes('var(--ax-glass-pill-shadow)'))
check('W4 一起看钮引 pill 档玻璃', blockOf('wp-together').includes('var(--ax-glass-pill-filter)'))
check('W5 锁屏钮引 pill 档玻璃', blockOf('wp-lock').includes('var(--ax-glass-pill-filter)'))
check('W6 中央开始钮引 slab 档玻璃', blockOf('wp-start-main').includes('var(--ax-glass-slab-filter)'))
check('W7 信息叠显引 pill 档玻璃', blockOf('wp-info-overlay').includes('var(--ax-glass-pill-filter)'))
check('W8 底部控制条引 slab 档玻璃（filter＋shadow）',
  blockOf('wp-bar').includes('var(--ax-glass-slab-filter)') && blockOf('wp-bar').includes('var(--ax-glass-slab-shadow)'))
check('W9 弹幕发送行引 slab 档玻璃', blockOf('wp-dm-send').includes('var(--ax-glass-slab-filter)'))
check('W10 吐司引 pill 档玻璃', blockOf('wp-toast').includes('var(--ax-glass-pill-filter)'))
const prep = blockOf('wp-prep')
check('W11 准备播放卡近黑实底＋float 外阴影束保留（不再引 --ax-glass-d 透底）',
  prep.includes('rgba(10, 10, 13, 0.96)') && prep.includes('var(--ax-glass-float-shadow)') && !prep.includes('var(--ax-glass-d)'))
check('W12 准备播放卡圆角 20（友站补测实测）', /border-radius:\s*20px/.test(prep))
check('W13 准备播放卡顶部渐变高光（自上而下衰减）',
  /linear-gradient\(180deg,\s*rgba\(255, 255, 255,/.test(prep))
check('W14 准备播放卡底部亮线（底边色亮于其余边）',
  /border-bottom-color:\s*rgba\(255, 255, 255, 0\.3\)/.test(prep))
check('W15 准备播放卡实底下摘除 backdrop-filter（无视觉作用）',
  !prep.includes('backdrop-filter'))

console.log(`\n${passed} passed`)
