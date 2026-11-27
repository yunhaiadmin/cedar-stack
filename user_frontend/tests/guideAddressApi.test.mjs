// 新人指南页服务器地址改后端下发测试（2026-11-27 用户亲点）：
// 运行：cd user_frontend && node tests/guideAddressApi.test.mjs
// 口径：指南页里的服务器地址不许再出现在前端源码里——改由后端
//  GET /api/user/guide/server-address 下发，只有有播放资格的登录
//  用户才拿到地址本体。前端三态沿用 #540：未加载/无资格/拉不到
//  一律显示「开通订阅后显示服务器地址」（fail-closed，不闪出）。
// 测法：显示判定纯函数 guideAddressState 活在 NewbieGuideView.vue
//  的标记段里，本测试抽取标记段真执行各情形；另加源码契约断言
//  地址字面量在前端源码零残留、接口路径与接线在位。
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const view = readFileSync(join(ROOT, 'src/views/game/NewbieGuideView.vue'), 'utf8')
const script = view.slice(view.indexOf('<script'), view.indexOf('</script>'))
const ADDR = 'https://cdn' + '.aetrix.love'

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}
function checkEq(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, `${name}（实际 ${JSON.stringify(actual)}）`)
  passed += 1
  console.log(`PASS ${name}`)
}

/* ---------- 源码契约：地址字面量零残留 ---------- */
check('C1 指南页源码无服务器地址字面量', !view.includes(ADDR) && !view.includes('cdn' + '.aetrix.love'))
const guideApiPath = join(ROOT, 'src/api/guide.ts')
check('C2 地址接口模块存在', existsSync(guideApiPath))
const guideApi = existsSync(guideApiPath) ? readFileSync(guideApiPath, 'utf8') : ''
check('C3 接口模块无服务器地址字面量', !guideApi.includes(ADDR) && !guideApi.includes('cdn' + '.aetrix.love'))
check('C4 接口路径正确', guideApi.includes('/api/user/guide/server-address'))
check('C5 页面接线 getGuideServerAddress', script.includes('getGuideServerAddress'))
check('C6 无资格占位文案在位', view.includes('开通订阅后显示服务器地址'))

/* ---------- 抽取纯判定段真执行 ---------- */
const START = '// === guideAddressState 开始 ==='
const END = '// === guideAddressState 结束 ==='
const sIdx = script.indexOf(START)
const eIdx = script.indexOf(END)
check('T0 判定段标记在位（开始/结束）', sIdx !== -1 && eIdx > sIdx)
const block = script.slice(sIdx + START.length, eIdx)
const tmpFile = join(tmpdir(), `guideAddressState-${process.pid}.ts`)
writeFileSync(tmpFile, `${block}\nexport { guideAddressState }\n`)
let mod
try {
  mod = await import(pathToFileURL(tmpFile).href)
} finally {
  rmSync(tmpFile, { force: true })
}
const state = mod.guideAddressState
check('T1 判定函数可执行导出', typeof state === 'function')

/* ---------- guideAddressState 三态 ---------- */
checkEq('S1 有资格+有地址 → 显示',
  state({ visible: true, server_address: ADDR }), { show: true, address: ADDR })
checkEq('S2 无资格（visible=false）→ 隐藏',
  state({ visible: false }), { show: false, address: '' })
checkEq('S3 未加载（null）→ 隐藏',
  state(null), { show: false, address: '' })
checkEq('S4 visible=true 但地址缺失 → 隐藏（fail-closed）',
  state({ visible: true }), { show: false, address: '' })
checkEq('S5 visible=true 但地址为空串 → 隐藏',
  state({ visible: true, server_address: '  ' }), { show: false, address: '' })
checkEq('S6 响应残缺（空对象）→ 隐藏',
  state({}), { show: false, address: '' })

/* ---------- 模板接线：地址只许经判定状态渲染 ---------- */
check('C7 地址经 addressState 渲染', view.includes('addressState.address') && view.includes('addressState.show'))

console.log(`\n全部 ${passed} 项通过`)
