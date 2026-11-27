// 新人指南页服务器地址订阅门测试（2026-11-27 用户亲点）：
// 运行：cd user_frontend && node tests/guideAddressGate.test.mjs
// 口径：指南页里的服务器地址只有有播放资格的用户能看到——
//  有效订阅（订阅 store activeSub，子账号由后端按主账号解析）或团队
//  套餐资格（后端 teams.play_eligible_team 前端口径：团队 active 且
//  未过期）。fail-closed：任一状态未落定先隐藏，绝不先闪出地址再收起；
//  无资格时地址位置显示「开通订阅后显示服务器地址」。
// 测法：判定纯函数 guideAddressVisible / teamPlayEligible 活在
//  NewbieGuideView.vue 的标记段里，本测试抽取标记段真执行各情形，
//  不是抄一份逻辑在测试里自证；另加源码契约断言两处地址都在
//  v-if="showServerAddress" 门后、占位文案与接线在位。
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const view = readFileSync(join(ROOT, 'src/views/game/NewbieGuideView.vue'), 'utf8')
const script = view.slice(view.indexOf('<script'), view.indexOf('</script>'))

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
const START = '// === guideAddressGate 开始 ==='
const END = '// === guideAddressGate 结束 ==='
const sIdx = script.indexOf(START)
const eIdx = script.indexOf(END)
check('T0 判定段标记在位（开始/结束）', sIdx !== -1 && eIdx > sIdx)
const block = script.slice(sIdx + START.length, eIdx)
const tmpFile = join(tmpdir(), `guideAddressGate-${process.pid}.ts`)
writeFileSync(tmpFile, `${block}\nexport { guideAddressVisible, teamPlayEligible }\n`)
let mod
try {
  mod = await import(pathToFileURL(tmpFile).href)
} finally {
  rmSync(tmpFile, { force: true })
}
const visible = mod.guideAddressVisible
const teamOk = mod.teamPlayEligible
check('T1 判定函数可执行导出', typeof visible === 'function' && typeof teamOk === 'function')

/* ---------- guideAddressVisible 三态 + 团队腿 ---------- */
// ① 有有效订阅 → 显示（团队腿还没落定也不许挡订阅用户）
checkEq(
  'S1 有订阅 → 显示',
  visible({ subLoaded: true, hasActiveSub: true, teamSettled: false, teamEligible: false }),
  true,
)
// ② 无订阅、无团队资格（都落定）→ 隐藏
checkEq(
  'S2 无订阅无团队 → 隐藏',
  visible({ subLoaded: true, hasActiveSub: false, teamSettled: true, teamEligible: false }),
  false,
)
// ③ 加载中（订阅未落定）→ 隐藏（fail-closed，不许先闪出）
checkEq(
  'S3 加载中 → 隐藏',
  visible({ subLoaded: false, hasActiveSub: false, teamSettled: false, teamEligible: false }),
  false,
)
// ③' 订阅未落定，哪怕团队腿已落定有资格 → 仍隐藏（订阅腿是前置门）
checkEq(
  'S3b 订阅未落定 → 隐藏',
  visible({ subLoaded: false, hasActiveSub: false, teamSettled: true, teamEligible: true }),
  false,
)
// ④ 无订阅但有团队资格（都落定）→ 显示
checkEq(
  'S4 团队资格 → 显示',
  visible({ subLoaded: true, hasActiveSub: false, teamSettled: true, teamEligible: true }),
  true,
)
// ⑤ 无订阅、团队腿未落定 → 隐藏（fail-closed）
checkEq(
  'S5 团队未落定 → 隐藏',
  visible({ subLoaded: true, hasActiveSub: false, teamSettled: false, teamEligible: true }),
  false,
)

/* ---------- teamPlayEligible（后端 play_eligible_team 同口径） ---------- */
const NOW = Date.parse('2026-11-27T00:00:00Z')
checkEq('G1 active 且未过期 → 有资格', teamOk({ status: 'active', plan_expires_at: '2026-12-27T00:00:00Z' }, NOW), true)
checkEq('G2 active 但已过期 → 无资格', teamOk({ status: 'active', plan_expires_at: '2026-11-26T00:00:00Z' }, NOW), false)
checkEq('G3 active 但到期为空 → 无资格', teamOk({ status: 'active', plan_expires_at: null }, NOW), false)
checkEq('G4 待开通（pending_activation）→ 无资格', teamOk({ status: 'pending_activation', plan_expires_at: '2026-12-27T00:00:00Z' }, NOW), false)
checkEq('G5 无团队 → 无资格', teamOk(null, NOW), false)
checkEq('G6 到期时刻恰为现在 → 无资格', teamOk({ status: 'active', plan_expires_at: '2026-11-27T00:00:00Z' }, NOW), false)

/* ---------- 源码契约：地址只许在门后出现 ---------- */
const ADDR = 'https://cdn.aetrix.love'
const addrCount = view.split(ADDR).length - 1
checkEq('C1 地址在页面里共 2 处（看片章 + 常见问题）', addrCount, 2)
check('C2 看片章地址在 v-if 门后', view.includes(`<span v-if="showServerAddress" class="guide-addr">${ADDR}</span>`))
check('C3 常见问题地址在 v-if 门后', view.includes(`<span v-if="showServerAddress">${ADDR}</span>`))
check('C4 无订阅占位文案在位', view.includes('开通订阅后显示服务器地址'))
check('C5 接线：订阅 store ensureLoaded', script.includes('useSubscriptionStore') && script.includes('ensureLoaded'))
check('C6 接线：团队详情 teamsApi.detail', script.includes('teamsApi.detail()'))

console.log(`\n全部 ${passed} 项通过`)
