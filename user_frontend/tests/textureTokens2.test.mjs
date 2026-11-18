// 质感令牌级第二批契约测试（2026-11-18）：阴影收编＋弹层收敛＋彩虹钮分档＋按压立档＋转场字阶收敛。
// 运行：cd user_frontend && node tests/textureTokens2.test.mjs
// 真源：reports/uhdnow-texture-gap-table-20261118.md §1 #4/#5/#9/#12/#16/#17/#18/#19、§3 R3/R5/R6、§5 补测回填。
// 口径：
//  #1 阴影：随手 box-shadow 全迁令牌。新立 13 档（sheet/card-d/card-lav/
//     member-l/member-d/panel-lg/float/soft/knob/thumb/thumb-d/focus/tab），
//     其余落既有档（card-l/seg/modal/玻璃四档/s-shadow-pill）。
//     豁免（非阴影或冻结体系）：1px 环形描边（inset 0 0 0 1px，描边替代）、
//     焦点环（体系令牌）、box-shadow:none、-webkit-box-shadow 自动填充 hack、
//     纸面硬影（v4-paper 冻结体系）、各体系自有令牌（modal-glass/tq/paper/utb）、
//     裸 inset 高光线（UhdTopBar/GiftCodeSheet/TrafficPurchaseModal）、
//     BindDriveGuide 硬底边（0 2px 0）、LandingView pin-glass 光晕与上向辉光。
//  #2 弹层：遮罩两档（深 45% 黑＋blur 6 / 浅 rgb(247,248,250)＋blur 16），
//     大浮件圆角收齐 24（准备卡 20 除外，已落 #306）；入场统一 UhdModal
//     一套（fade .52s＋blur-in .56s＋ease-pop），ui/Modal 迁令牌。
//     ui/Modal 遮罩 var(--au-scrim) 是暗房影院 0.7 刻意深档，不动。
//  #3 彩虹钮：UhdButton 增 42 级中档；--uhd-rainbow 按 2026-11-18 真机录屏
//     纠正改 135deg 不对称（左侧鲜明、向右淡出；桌面采样色只作左缘参考），
//     --uhd-rainbow-border 同值、flow 版双周期再生；conic 版（Logo 三角）不动。
//     底色改中性深色低透明玻璃（alpha ≤0.5、中性色相）；填色单立不透明
//     --uhd-rainbow-fill；LandingView 深暖覆盖清零；幽灵钮已合规锁死。
//  #4 按压：--uhd-press 三值（缩放 0.97／亮度 0.96／时长 micro 0.15s），
//     主钮与胶囊统一引用；位移类 :active（translateY）与背景类 :active 不动；
//     v4-paper 冻结体系与 :focus 缩放不动。
//  #5 转场字阶：删 index.css 重复页面转场定义（走 App.vue 一处）；
//     详情简介 15px/26px；设置行高 --uhd-row-h 52px（.set-row/.set-rowbtn/
//     .pc-row/.sec-rowbtn/.uhd-row 本体；会话 90px 与步骤 56px 变体不动）。
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
function blockOf(text, sel) {
  const m = text.match(new RegExp(`\\.${sel}(?![\\w-])[^{]*\\{([^}]*)\\}`))
  return m ? m[1] : ''
}
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(vue|css)$/.test(name)) out.push(p)
  }
  return out
}

/* ---------- 阴影新令牌（uhd-tokens.css） ---------- */
const tokens = read('src/styles/uhd-tokens.css')
const NEW_SHADOWS = ['sheet', 'card-d', 'card-lav', 'member-l', 'member-d',
  'panel-lg', 'float', 'soft', 'knob', 'thumb', 'thumb-d', 'focus', 'tab']
for (const n of NEW_SHADOWS) {
  check(`T1 阴影令牌 --uhd-shadow-${n} 已定义`, tokens.includes(`--uhd-shadow-${n}:`))
}
const memberL = tokens.match(/--uhd-shadow-member-l:\s*([^;]+);/)
check('T2 会员卡浅色阴影含补测采样 rgb(205,201,229)',
  !!memberL && memberL[1].includes('205, 201, 229'))
const memberD = tokens.match(/--uhd-shadow-member-d:\s*([^;]+);/)
check('T2 会员卡深色阴影含补测采样 rgb(23,23,31)，注释记页底 rgb(19,20,21)',
  !!memberD && memberD[1].includes('23, 23, 31') && tokens.includes('rgb(19,20,21)'))

/* ---------- 阴影随手值清零（全 src 扫描） ---------- */
const SHADOW_ALLOW = [
  ['BindDriveGuide.vue', '0 2px 0 rgba(0, 0, 0, 0.18)', '硬底边非阴影'],
  ['LandingView.vue', '0 -18px 50px', '上向辉光无对应令牌'],
  ['LandingView.vue', 'inset 0 48px 90px', 'pin-glass 玻璃辉光'],
  ['v4-paper.css', '3px 3px 0 rgba(42, 38, 32, 0.12)', '纸面硬影冻结体系'],
  // rebase 到 main 1c2c063e（#307）后新增：UhdMemberCard 是 #307 收敛后的唯一会员卡实现，
  // 其阴影是 #307 自家实测值（pages-2 契约 M7/M8 锁定），与 tokens-2 的 member-l/member-d
  // 令牌值数位不同（浅：0 2px 8px -2px＋0 24px 38px -20px；深：0 2px 8px -2px＋0 20px 36px -18px
  // ＋顶部高光 rgb(130,125,159)）；.ov-card 已随 #307 删除，两枚令牌暂无引用方。
  // 两套值以哪套为准须 parent 定夺，rebase 时不许静默改任一已验值，故此处豁免。
  ['UhdMemberCard.vue', '0 2px 8px -2px rgb(205, 201, 229)', '会员卡浅色阴影 #307 实测值，与 member-l 令牌值不同'],
  ['UhdMemberCard.vue', 'inset 0 1px 0 rgb(130, 125, 159)', '会员卡深色阴影＋顶部高光 #307 实测值，与 member-d 令牌值不同'],
]
function shadowOk(file, val) {
  const v = val.replace(/\s+/g, ' ').trim()
  if (v === 'none') return true
  if (v.includes('var(--')) return true // 任一体系令牌
  if (/^inset 0 0 0 1px/.test(v)) return true // 环形描边（边框替代物）
  if (/^inset 0 1px 0 rgba\(255, 255, 255, [\d.]+\)$/.test(v)) return true // 裸高光线
  return SHADOW_ALLOW.some(([f, frag]) => file.endsWith(f) && v.includes(frag))
}
const srcFiles = walk(join(ROOT, 'src'))
const shadowBad = []
for (const f of srcFiles) {
  const raw = readFileSync(f, 'utf8')
  // 注释清零（保留换行以保行号）
  const text = raw.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  const re = /(?<!-)box-shadow\s*:\s*([^;]+);/g
  let m
  while ((m = re.exec(text))) {
    if (!shadowOk(f, m[1])) {
      const line = text.slice(0, m.index).split('\n').length
      shadowBad.push(`${f.replace(ROOT + '/', '')}:${line} ${m[1].replace(/\s+/g, ' ').trim()}`)
    }
  }
}
if (shadowBad.length) console.log('  未迁移：\n  ' + shadowBad.join('\n  '))
check(`T3 随手 box-shadow 全迁令牌（豁免 ${SHADOW_ALLOW.length} 处）`,
  shadowBad.length === 0)

/* ---------- 弹层遮罩两档（modal-glass.css） ---------- */
const mg = read('src/styles/modal-glass.css')
check('T4 深色遮罩令牌 45% 黑＋blur 6',
  mg.includes('--modal-scrim-dark: rgba(0, 0, 0, 0.45)') && mg.includes('--modal-scrim-dark-filter: blur(6px)'))
check('T4 浅色遮罩令牌 rgb(247,248,250)＋blur 16',
  mg.includes('--modal-scrim-light: rgba(247, 248, 250, 0.72)') && mg.includes('--modal-scrim-light-filter: blur(16px)'))
const scrimDark = [
  ['src/components/uhd/UhdModal.vue', 'uhd-modal__overlay'],
  ['src/components/profile/BindDriveSheet.vue', 'bds-overlay'],
  ['src/components/profile/ServerLinesSheet.vue', 'sls-overlay'],
  ['src/components/TgBindModal.vue', 'modal-mask'],
]
for (const [f, sel] of scrimDark) {
  const blk = blockOf(read(f), sel)
  check(`T5 ${f.split('/').pop()} .${sel} 引深色遮罩档（背景＋滤镜）`,
    blk.includes('var(--modal-scrim-dark)') && blk.includes('var(--modal-scrim-dark-filter)'))
}
const landing = read('src/views/marketing/LandingView.vue')
check('T5 LandingView .m-scrim 引深色遮罩档（背景＋滤镜）',
  /\.m-scrim\s*\{[^}]*background:\s*var\(--modal-scrim-dark\)[^}]*backdrop-filter:\s*var\(--modal-scrim-dark-filter\)/.test(landing))
const psh = read('src/components/player/PlayerSheet.vue')
check('T5 PlayerSheet 遮罩只换背景值、不加 blur（浮动气泡面）',
  blockOf(psh, 'wp-sheet-root').includes('var(--modal-scrim-dark)') && !blockOf(psh, 'wp-sheet-root').includes('var(--modal-scrim-dark-filter)'))
const psp = read('src/components/panel/PlayerSchemeSheet.vue')
check('T5 选版本弹层浅色态引浅色遮罩档',
  blockOf(psp, 'psp-mask').includes('var(--modal-scrim-light)') && blockOf(psp, 'psp-mask').includes('var(--modal-scrim-light-filter)'))
check('T5 选版本弹层深色桥接引深色遮罩档',
  psp.includes('body.uhd-stream-dark .psp-mask') && /body\.uhd-stream-dark \.psp-mask\s*\{[^}]*var\(--modal-scrim-dark\)/.test(psp))
check('T5 ui/Modal 遮罩暗房深档 var(--au-scrim) 不动',
  read('src/components/ui/Modal.vue').includes('background: var(--au-scrim)'))

/* ---------- 弹层入场统一 UhdModal 令牌 ---------- */
const modal = read('src/components/ui/Modal.vue')
const modalStyle = modal.slice(modal.indexOf('<style'))
check('T6 ui/Modal 遮罩进出走 --uhd-dur-fade/--uhd-ease-standard',
  modalStyle.includes('var(--uhd-dur-fade)') && modalStyle.includes('var(--uhd-ease-standard)'))
check('T6 ui/Modal 内容进场走 --uhd-dur-blur-in/--uhd-ease-pop',
  modalStyle.includes('var(--uhd-dur-blur-in)') && modalStyle.includes('var(--uhd-ease-pop)'))
check('T6 ui/Modal 内容离场走 --uhd-dur-base（0.2s，与原值一致）',
  modalStyle.includes('var(--uhd-dur-base)'))
const modalAnim = modalStyle.slice(modalStyle.indexOf('/* ==================== 动画'),
  modalStyle.indexOf('/* ==================== 动效降级'))
check('T6 ui/Modal 入场动画段内 neo 时长清零（关闭钮 150ms 微交互不在此段）',
  !modalAnim.includes('--neo-duration'))
check('T6 UhdModal 仍为基准（fade .52s＋blur-in .56s＋ease-pop）',
  read('src/components/uhd/UhdModal.vue').includes('var(--uhd-dur-fade)') && read('src/components/uhd/UhdModal.vue').includes('var(--uhd-dur-blur-in)'))

/* ---------- 大浮件圆角收齐 24（准备卡 20 除外） ---------- */
check('T7 选版本弹层 .psp-sheet 圆角 24',
  /border-radius:\s*24px/.test(blockOf(psp, 'psp-sheet')))
check('T7 选版本弹层内无 28px 圆角', !psp.includes('border-radius: 28px'))
check('T7 准备播放卡圆角 20 不动（#306 落定）',
  /border-radius:\s*20px/.test(blockOf(read('src/views/WebPlayerView.vue'), 'wp-prep')))

/* ---------- 彩虹钮分档与停点校准 ---------- */
const btn = read('src/components/uhd/UhdButton.vue')
check('T8 UhdButton 增 mid 档（42 级）',
  btn.includes("'mid'") && /height:\s*42px/.test(blockOf(btn, 'uhd-btn--mid')))
check('T8 mid 档内边距 0 22px、字号 body 级',
  blockOf(btn, 'uhd-btn--mid').includes('padding: 0 22px') && blockOf(btn, 'uhd-btn--mid').includes('var(--uhd-fs-body)'))
/* ---------- 彩虹钮分档与停点校准（2026-11-18 真机录屏纠正） ---------- */
function stopsOf(grad) {
  return [...grad.matchAll(/((?:rgba?\([^)]+\)|#[0-9a-f]{6}))\s+([\d.]+)%/gi)]
    .map((m) => ({ color: m[1], pos: Number(m[2]) }))
}
function alphaOf(color) {
  const m = color.match(/rgba?\(([^)]+)\)/)
  if (!m) return 1
  const parts = m[1].split(',').map((s) => Number(s.trim()))
  return parts.length === 4 ? parts[3] : 1
}
const rainbow = tokens.match(/--uhd-rainbow:\s*([^;]+);/)
check('T9 --uhd-rainbow 为 135deg 不对称分布（左鲜明、右淡出）',
  !!rainbow && rainbow[1].includes('135deg'))
check('T9 左缘鲜明段含采样色（绿／青／琥珀／橙红）',
  !!rainbow && ['#a4de91', '#3cdce7', '#f5c165', '#e8766f'].every((h) => rainbow[1].includes(h)))
const rStops = rainbow ? stopsOf(rainbow[1]) : []
check('T9 右半段停点 alpha 均 ≤0.30 且含透明停点',
  rStops.filter((s) => s.pos >= 50).length >= 2 &&
  rStops.filter((s) => s.pos >= 50).every((s) => alphaOf(s.color) <= 0.30) &&
  rStops.some((s) => s.pos >= 50 && alphaOf(s.color) === 0))
const rainbowBorder = tokens.match(/--uhd-rainbow-border:\s*([^;]+);/)
check('T9 --uhd-rainbow-border 与 --uhd-rainbow 同值',
  !!rainbow && !!rainbowBorder && rainbow[1].trim() === rainbowBorder[1].trim())
const flow = tokens.match(/--uhd-rainbow-border-flow:\s*([^;]+);/)
const fStops = flow ? stopsOf(flow[1]) : []
check('T9 flow 版双周期再生（透明居中 50%、绿收尾与 0% 同色无缝）',
  !!flow && fStops.some((s) => s.pos === 50 && alphaOf(s.color) === 0) &&
  flow[1].includes('#a4de91 100%'))
const fill = tokens.match(/--uhd-rainbow-fill:\s*([^;]+);/)
check('T9 fill 版为不透明九停点（填色专用）',
  !!fill && !/rgba\(/.test(fill[1]) && fill[1].includes('#2f8fe0 0%'))
const soft = tokens.match(/--uhd-rainbow-soft:\s*([^;]+);/)
check('T9 soft 版为四处采样内底色的近白洗色',
  !!soft && ['#eae7d6', '#f3f5f5', '#f6eedb', '#f4f6f6'].every((h) => soft[1].includes(h)))
check('T9 conic 版（Logo 三角）不动',
  tokens.includes('--uhd-rainbow-conic:') && /--uhd-rainbow-conic:[^;]*#2f8fe0/.test(tokens))
const btnD = tokens.match(/--uhd-glass-btn-d:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/)
check('T9 主钮底色 alpha ≤0.5 且色相中性（R/B 差 ≤12）',
  !!btnD && Number(btnD[4]) <= 0.5 && Math.abs(Number(btnD[1]) - Number(btnD[3])) <= 12)
const landingV = read('src/views/marketing/LandingView.vue')
check('T9 LandingView 深暖覆盖 rgba(49,40,24) 清零',
  !landingV.includes('49, 40, 24') && !landingV.includes('49,40,24'))
check('T9 幽灵钮为中性透光玻璃＋细亮边＋模糊（已合规，锁死）',
  landingV.includes('background: rgba(11, 15, 17, 0.5)') &&
  landingV.includes('border: 1px solid rgba(255, 255, 255, 0.14)') &&
  landingV.includes('backdrop-filter: blur(14px) saturate(1.4)'))
check('T9 填色用途全走 --uhd-rainbow-fill（徽章／404／确认钮／当前套餐）',
  ['src/components/uhd/UhdChip.vue', 'src/components/uhd/UhdPlanCompare.vue',
   'src/views/NotFoundView.vue', 'src/views/game/SubscriptionPlansView.vue']
    .every((f) => read(f).includes('var(--uhd-rainbow-fill')))

/* ---------- 深色主钮环形-only 透光（返工，2026-11-18） ---------- */
// 根因：旧写法彩虹渐变铺满 border-box，透过 alpha 0.40 玻璃在钮面内晕染成色块；
// 友站是钮面全透光、彩虹只在 1.5px 细环。环切：::before 渐变层 padding=边框宽，
// mask 剔掉 content-box，只剩环形；钮面背景只剩玻璃层。浅色主钮不动。
const btnDark = blockOf(btn, 'uhd-btn--primary.uhd-btn--dark')
const ringM = btn.match(/\.uhd-btn--primary\.uhd-btn--dark::before\s*\{([^}]*)\}/)
const ring = ringM ? ringM[1] : ''
check('T14 深色主钮钮面背景只剩玻璃层（无彩虹 border-box 整面铺底）',
  btnDark.includes('var(--uhd-glass-btn-d)') && !btnDark.includes('var(--uhd-rainbow)') && !btnDark.includes('border-box'))
check('T14 深色主钮保留玻璃底＋pill 档模糊',
  btnDark.includes('var(--uhd-glass-btn-d)') && btnDark.includes('backdrop-filter: var(--uhd-glass-filter)'))
check('T14 彩虹环在 ::before 上（渐变层＋mask 环切＋边框宽 padding）',
  ring.includes('background: var(--uhd-rainbow)') &&
  ring.includes('padding: var(--uhd-rainbow-border-w)') &&
  ring.includes('content-box') &&
  (ring.includes('mask-composite: exclude') || ring.includes('-webkit-mask-composite: xor')))
check('T14 禁用态彩虹环一并退场',
  /\.uhd-btn--primary\.uhd-btn--dark:disabled::before\s*\{[^}]*display:\s*none/.test(btn))
check('T14 浅色主钮不动（淡彩底＋彩虹边框整面写法保留）',
  blockOf(btn, 'uhd-btn--primary').includes('var(--uhd-rainbow-soft) padding-box') &&
  blockOf(btn, 'uhd-btn--primary').includes('var(--uhd-rainbow) border-box'))

/* ---------- 按压反馈立档 ---------- */
check('T10 按压三值已立（缩放 0.97／亮度 0.96／时长 micro）',
  tokens.includes('--uhd-press-scale: 0.97') && tokens.includes('--uhd-press-brightness: 0.96') && tokens.includes('--uhd-press-dur: var(--uhd-dur-micro)'))
check('T10 UhdButton 按压引缩放＋亮度令牌',
  /\.uhd-btn[^{]*:active[^{]*\{[^}]*scale\(var\(--uhd-press-scale\)\)[^}]*brightness\(var\(--uhd-press-brightness\)\)/.test(btn))
const PRESS_SITES = [
  ['src/components/AppHeader.vue', 'nav-link'],
  ['src/components/AppHeader.vue', 'round-btn'],
  ['src/components/AppHeader.vue', 'dropdown-facility'],
  ['src/components/panel/PlayerSchemeSheet.vue', 'psp-start'],
  ['src/components/profile/GiftCodeSheet.vue', 'gc-copy'],
  ['src/components/uhd/UhdWatchPill.vue', 'utb-pill'],
  ['src/views/game/SubscriptionPlansView.vue', 'sub-sticky__cta'],
  ['src/views/game/WalletView.vue', 'w-recharge'],
  ['src/views/game/WalletView.vue', 'w-outline-btn'],
  ['src/views/game/invite-friends.css', 'iv-btn'],
]
for (const [f, sel] of PRESS_SITES) {
  const text = read(f)
  const re = new RegExp(`\\.${sel}:active[^\\{]*\\{[^}]*scale\\(var\\(--uhd-press-scale\\)\\)`)
  check(`T10 ${f.split('/').pop()} .${sel}:active 引按压缩放令牌`, re.test(text))
}
for (const [f, re] of [
  ['src/components/profile/TrafficPurchaseModal.vue', /\.tq-[\w-]+:active[^{]*\{[^}]*scale\(var\(--uhd-press-scale\)\)/],
  ['src/views/LoginView.vue', /\.submit-btn:active[^{]*\{[^}]*scale\(var\(--uhd-press-scale\)\)/],
  ['src/views/game/PersonalCenterView.vue', /\.ov-btn:active[^{]*\{[^}]*scale\(var\(--uhd-press-scale\)\)/],
  ['src/styles/index.css', /\.btn-primary:active[^{]*\{[^}]*scale\(var\(--uhd-press-scale\)\)/],
  ['src/styles/aurora.css', /\.au-btn-primary:active[^{]*\{[^}]*scale\(var\(--uhd-press-scale\)\)/],
]) {
  check(`T10 ${f.split('/').pop()} 按压引缩放令牌`, re.test(read(f)))
}
// :active 内的字面缩放清零（v4-paper 冻结体系除外）
const pressBad = []
for (const f of srcFiles) {
  if (f.endsWith('v4-paper.css')) continue
  const lines = readFileSync(f, 'utf8').split('\n')
  let inActive = false
  let depth = 0
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i]
    if (!inActive && ln.includes(':active') && ln.includes('{')) { inActive = true; depth = 0 }
    if (inActive) {
      depth += (ln.match(/\{/g) || []).length - (ln.match(/\}/g) || []).length
      if (/scale\(0\.(96|98|99)\)/.test(ln)) pressBad.push(`${f.replace(ROOT + '/', '')}:${i + 1} ${ln.trim()}`)
      if (depth <= 0 && ln.includes('}')) inActive = false
    }
  }
}
if (pressBad.length) console.log('  残留：\n  ' + pressBad.join('\n  '))
check('T10 :active 内字面缩放 0.96/0.98/0.99 清零', pressBad.length === 0)

/* ---------- 转场与字阶内部收敛 ---------- */
const indexCss = read('src/styles/index.css')
check('T11 index.css 重复页面转场定义已删（只留 App.vue 一处）',
  !indexCss.includes('transition: opacity 0.2s ease'))
check('T11 index.css 减动效覆盖保留（.page-enter-active 仍被 transition:none 兜住）',
  indexCss.includes('.page-enter-active') && indexCss.includes('transition: none !important'))
check('T11 App.vue 保留页面转场（enter-only 180ms 淡入）',
  read('src/App.vue').includes('.page-enter-active'))
const mdv = read('src/views/panel/MediaDetailView.vue')
check('T12 详情简介收 15px/26px',
  /font-size:\s*15px/.test(blockOf(mdv, 'md-overview')) && /line-height:\s*26px/.test(blockOf(mdv, 'md-overview')))
check('T13 设置行高令牌 --uhd-row-h: 52px 已立', tokens.includes('--uhd-row-h: 52px'))
for (const [f, sel] of [
  ['src/views/game/AccountSettingsView.vue', 'set-row'],
  ['src/views/game/AccountSettingsView.vue', 'set-rowbtn'],
  ['src/components/profile/PcEntryRow.vue', 'pc-row'],
  ['src/components/profile/TotpPanel.vue', 'sec-rowbtn'],
  ['src/components/uhd/UhdStatusRow.vue', 'uhd-row'],
]) {
  check(`T13 ${f.split('/').pop()} .${sel} 行高引 --uhd-row-h`,
    blockOf(read(f), sel).includes('min-height: var(--uhd-row-h)'))
}
const usr = read('src/components/uhd/UhdStatusRow.vue')
check('T13 会话行 90px 变体不动', /min-height:\s*90px/.test(blockOf(usr, 'uhd-row--session')))
check('T13 步骤行 56px 变体不动', /min-height:\s*56px/.test(blockOf(usr, 'uhd-row--step')))

console.log(`\n${passed} passed`)
