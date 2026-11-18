// 质感第二波·单面收尾（2026-11-18）：底栏浮坞＋片库顶栏/海报＋详情返回＋会员卡收敛＋定价＋社区下拉。
// 运行：cd user_frontend && node tests/texturePages2.test.mjs
// 真源：reports/uhdnow-texture-gap-table-20261118.md §1 #1/#3/#7/#10/E4/E6/E7、§5 补测回填。
//  #1 会员卡：2026-11-18 按友站公开样式表真值重做（reports/member-card-friend-css-20261118.md）——
//     卡框 max-width 425、aspect-ratio 1.586、圆角 40px（专用令牌）、padding 32px；
//     卡面 155deg 半透底＋backdrop blur/saturate＋slab/face 合并阴影；foil 4px 条纹＋
//     prism 彩虹斜带遮罩；bevel 1.5px 环切。旧采样形态（白底蓝带/紫蓝渐变底/228 高）已作废。
//  #3 底栏：用户中心移动底栏由实心纸条迁玻璃浮坞（slab 档＋坞影束），与片库坞同口径。
//  #7/#20/E6 片库顶栏升 slab 档、海报圆角 13→16（骨架同步）；浅态选中项已是白底胶囊，无需改。
//  #10 详情返回钮升 pill 档。E4 定价：顶部 250px 淡彩虹洗色、卡 287×454/间距 24/圆角 24、
//     彩虹钮 235×36（UhdButton 现有 sm 档 38px 为最接近档，取 sm、差 2px 记档内）。
//  E7 社区下拉：圆角 20、行图标块 36（行高随之约 56）；下拉本体为深色玻璃，
//     友站浅态描边/图标底色值不套深色面，只对圆角与图标块尺寸。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
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
function blockOf(css, cls) {
  // 规则一律顶格写；锚行首，避免注释里提及类名时抓错规则体
  const m = css.match(new RegExp(`^\\.${cls}(?![\\w-])[^{]*\\{([^}]*)\\}`, 'm'))
  return m ? m[1] : ''
}

/* ---------- 1. 用户中心移动底栏迁玻璃浮坞（v4-paper.css） ---------- */
const paper = read('src/styles/v4-paper.css')
const tabbar = blockOf(paper, 'v4-tabbar')
check('B1 底栏引 slab 档玻璃 filter＋shadow',
  tabbar.includes('var(--uhd-glass-slab-filter)') && tabbar.includes('var(--uhd-glass-slab-shadow)'))
check('B2 底栏底色为玻璃档（不再是实心纸色）',
  (tabbar.includes('var(--uhd-glass-d)') || tabbar.includes('var(--uhd-glass-l)'))
  && !tabbar.includes('var(--paper-card)')
  && !tabbar.includes('#FFFDF6'))
check('B3 底栏浮坞形态：圆角 28、左右留边、底部悬空',
  /border-radius:\s*28px/.test(tabbar) && /left:\s*12px/.test(tabbar)
  && /right:\s*12px/.test(tabbar) && /bottom:\s*calc\(10px \+ env\(safe-area-inset-bottom\)\)/.test(tabbar))
check('B4 底栏摘除实心纸条墨线顶边与底部纸色延伸条',
  !/border-top:\s*1\.5px solid/.test(tabbar) && !/^\.v4-tabbar::after\s*\{/m.test(paper))

/* ---------- 2. 片库顶栏升 slab 档＋海报圆角（StreamTopbar/StreamPosterCard/StreamHomeView） ---------- */
const topbar = read('src/components/stream/StreamTopbar.vue')
const topbarStyle = topbar.slice(topbar.indexOf('<style'))
const capsule = blockOf(topbarStyle, 'st-capsule')
check('L1 片库顶栏胶囊引 slab 档玻璃 filter＋shadow',
  capsule.includes('var(--uhd-glass-slab-filter)') && capsule.includes('var(--uhd-glass-slab-shadow)'))
check('L2 片库顶栏胶囊无裸写 blur(12px)',
  !/backdrop-filter:\s*blur\(12px\)/.test(capsule) && !/-webkit-backdrop-filter:\s*blur\(12px\)/.test(capsule))
const poster = read('src/components/stream/StreamPosterCard.vue')
check('L3 海报卡圆角 16（友站实测约 16）',
  /border-radius:\s*16px/.test(blockOf(poster.slice(poster.indexOf('<style')), 'sp-poster')))
const home = read('src/views/panel/StreamHomeView.vue')
check('L4 骨架海报圆角同步 16',
  /border-radius:\s*16px/.test(blockOf(home.slice(home.indexOf('<style')), 'sl-skel__poster')))

/* ---------- 3. 详情返回钮升 pill 档（MediaDetailView） ---------- */
const detail = read('src/views/panel/MediaDetailView.vue')
const detailStyle = detail.slice(detail.indexOf('<style'))
const mdBack = blockOf(detailStyle, 'md-back')
check('D1 详情返回钮引 pill 档玻璃 filter＋shadow',
  mdBack.includes('var(--uhd-glass-pill-filter)') && mdBack.includes('var(--uhd-glass-pill-shadow)'))
check('D2 详情返回钮无裸写 blur(12px)',
  !/backdrop-filter:\s*blur\(12px\)/.test(mdBack) && !/-webkit-backdrop-filter:\s*blur\(12px\)/.test(mdBack))

/* ---------- 4. 会员卡收敛为 UhdMemberCard 唯一实现 ---------- */
const center = read('src/views/game/PersonalCenterView.vue')
check('M1 概览改用 UhdMemberCard 组件',
  center.includes("import UhdMemberCard from '@/components/uhd/UhdMemberCard.vue'")
  && /<UhdMemberCard\b/.test(center))
check('M2 概览无残留 .ov-card 实现（模板＋样式全清）',
  !center.includes('ov-card'))
const member = read('src/components/uhd/UhdMemberCard.vue')
const memberStyle = member.slice(member.indexOf('<style'))
const card = blockOf(memberStyle, 'uhd-member__card')
check('M3 会员卡框 max-width 425、aspect-ratio 1.586、圆角 40px 专用令牌（友站真值；旧固定 228 高已废）',
  /max-width:\s*425px/.test(blockOf(memberStyle, 'uhd-member')) && /aspect-ratio:\s*1\.586/.test(card)
  && !/min-height/.test(card) && card.includes('var(--uhd-radius-member)')
  && /--uhd-radius-member:\s*40px/.test(read('src/styles/uhd-tokens.css')))
check('M4 浅色卡面 155deg 半透白底＋blur(24px) saturate(160%)＋边 #ffffff4d（友站真值）',
  card.includes('linear-gradient(155deg, #ffffffbd 0%, #ffffff94 40%, #ffffff85 100%)')
  && card.includes('blur(24px) saturate(160%)') && card.includes('#ffffff4d'))
check('M5 foil 条纹 4px 周期＋prism 彩虹＋115deg 斜带遮罩 38%/46%–58%/66%（友站真值）',
  memberStyle.includes('transparent 0 3px, #ffffff80 3px 4px')
  && memberStyle.includes('oklch(calc(var(--prism-l) + .04) var(--prism-c) 62)')
  && memberStyle.includes('#0000 38%, #000 46% 58%, #0000 66%'))
check('M6 深色卡面 155deg 微光底＋blur(20px) saturate(160%)＋边 #ffffff0a（友站真值）',
  memberStyle.includes('linear-gradient(155deg, #ffffff14 0%, #ffffff0a 30%, #ffffff08 75%, #ffffff0a 100%)')
  && memberStyle.includes('blur(20px) saturate(160%)') && memberStyle.includes('#ffffff0a'))
check('M7 bevel 边框环：1.5px padding＋mask 环切＋两态 145deg 渐变（友站真值）',
  /padding:\s*1\.5px/.test(blockOf(memberStyle, 'uhd-member__bevel'))
  && memberStyle.includes('mask-composite: exclude')
  && memberStyle.includes('linear-gradient(145deg, #fff 0%, #ffffff80 30%, #14161c1f 68%, #fffc 100%)')
  && memberStyle.includes('linear-gradient(145deg, #ffffff80 0%, #ffffff1f 30%, #ffffff08 68%, #ffffff40 100%)'))
check('M8 阴影经 member 令牌且为友站真值锚点（浅 oklch /.38、深 oklch /.5）',
  memberStyle.includes('var(--uhd-shadow-member-l)') &&
  memberStyle.includes('var(--uhd-shadow-member-d)') &&
  /--uhd-shadow-member-l:[^;]*oklch\(55% \.2 285\/\.38\)/.test(read('src/styles/uhd-tokens.css')) &&
  /--uhd-shadow-member-d:[^;]*oklch\(55% \.2 285\/\.5\)/.test(read('src/styles/uhd-tokens.css')))
check('M9 卡名约 28px 粗体', /font-size:\s*28px/.test(blockOf(memberStyle, 'uhd-member__plan')))
check('M10 右上品牌标浅色 37×40、深色 33×39',
  /width:\s*37px/.test(blockOf(memberStyle, 'uhd-member__logo'))
  && memberStyle.includes('33px') && memberStyle.includes('39px'))

/* ---------- 5. 定价页洗色与卡尺寸（PricingView＋UhdPlanCompare） ---------- */
const pricing = read('src/views/marketing/PricingView.vue')
const pricingStyle = pricing.slice(pricing.indexOf('<style'))
check('P1 定价页浅态顶部 250px 淡彩虹洗色（三采样色齐全）',
  pricingStyle.includes('rgb(249, 227, 222)') && pricingStyle.includes('rgb(222, 242, 237)')
  && pricingStyle.includes('rgb(252, 251, 254)') && pricingStyle.includes('250px'))
const plans = read('src/components/uhd/UhdPlanCompare.vue')
const plansStyle = plans.slice(plans.indexOf('<style'))
const col = blockOf(plansStyle, 'uhd-plans__col')
check('P2 套餐卡 287 宽（列宽 287）＋间距 24',
  /287px/.test(blockOf(plansStyle, 'uhd-plans')) && /gap:\s*24px/.test(blockOf(plansStyle, 'uhd-plans')))
check('P3 套餐卡高 454（min-height）＋圆角 24',
  /min-height:\s*454px/.test(col) && /border-radius:\s*24px/.test(col))
check('P4 套餐彩虹钮取 sm 档（38px，最接近友站 36 的现有档）',
  plans.includes('size="sm"'))

/* ---------- 6. 社区下拉菜单（UhdNavPill） ---------- */
const nav = read('src/components/uhd/UhdNavPill.vue')
const navStyle = nav.slice(nav.indexOf('<style'))
check('N1 下拉面圆角 20', /border-radius:\s*20px/.test(blockOf(navStyle, 'uhd-nav__dropdown')))
check('N2 行图标块 36×36',
  /width:\s*36px/.test(blockOf(navStyle, 'uhd-nav__drop-icon'))
  && /height:\s*36px/.test(blockOf(navStyle, 'uhd-nav__drop-icon')))

console.log(`\n${passed} passed`)
