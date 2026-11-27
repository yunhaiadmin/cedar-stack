// 安装应用页服务器地址资格门测试（2026-11-27 用户亲点）：
// 运行：cd user_frontend && node tests/installAppAddressGate.test.mjs
// 口径：安装页的服务器地址原先由后端对任何登录用户无条件下发，
//  与新人指南页同一个口子。#542 已堵指南页，本单堵安装页：后端
//  GET /api/user/install-app 按播放资格下发，无资格整字段不带
//  server_address。前端归一化：字段缺失/空串一律归一为 null，
//  页面凭 null 不渲染地址本体与复制按钮、只显示「开通订阅后显示
//  服务器地址」（fail-closed：未加载也是 null，不闪出）。
// 测法：归一化纯函数 normalizeInstallAppPayload 活在
//  src/api/installApp.ts 的标记段里，本测试抽取标记段真执行各
//  情形；另加源码契约断言：地址字面量在前端源码零残留、页面
//  接线与占位文案在位。
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const apiTs = readFileSync(join(ROOT, 'src/api/installApp.ts'), 'utf8')
const view = readFileSync(join(ROOT, 'src/views/game/InstallAppView.vue'), 'utf8')
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
check('C1 接口模块无服务器地址字面量', !apiTs.includes(ADDR) && !apiTs.includes('cdn' + '.aetrix.love'))
check('C2 页面源码无服务器地址字面量', !view.includes(ADDR) && !view.includes('cdn' + '.aetrix.love'))
check('C3 接口路径正确', apiTs.includes('/api/user/install-app'))
check('C4 地址类型可缺省（string | null）', apiTs.includes('server_address: string | null'))
check('C5 无资格占位文案在位', view.includes('开通订阅后显示服务器地址'))

/* ---------- 抽取归一化段真执行 ---------- */
const START = '// === installAppNormalize 开始 ==='
const END = '// === installAppNormalize 结束 ==='
const sIdx = apiTs.indexOf(START)
const eIdx = apiTs.indexOf(END)
check('T0 归一化段标记在位（开始/结束）', sIdx !== -1 && eIdx > sIdx)
const block = apiTs.slice(sIdx + START.length, eIdx).replace(/^export /gm, '')
const tmpFile = join(tmpdir(), `installAppNormalize-${process.pid}.ts`)
writeFileSync(tmpFile, `${block}\nexport { normalizeInstallAppPayload }\n`)
let mod
try {
  mod = await import(pathToFileURL(tmpFile).href)
} finally {
  rmSync(tmpFile, { force: true })
}
const normalize = mod.normalizeInstallAppPayload
check('T1 归一化函数可执行导出', typeof normalize === 'function')

/* ---------- normalizeInstallAppPayload 各情形 ---------- */
const PLAT = [{ key: 'ios', label: 'iOS', devices: 'iPhone / iPad', store: 'App Store', url: 'https://example.com/emby-ios' }]
checkEq('S1 有地址 → 原样保留、平台照常',
  normalize({ server_address: ADDR, platforms: PLAT }), { server_address: ADDR, platforms: PLAT })
checkEq('S2 无 server_address 字段 → null，平台照常',
  normalize({ platforms: PLAT }), { server_address: null, platforms: PLAT })
checkEq('S3 地址为空串 → null',
  normalize({ server_address: '', platforms: [] }), { server_address: null, platforms: [] })
checkEq('S4 地址为纯空白 → null',
  normalize({ server_address: '  ', platforms: [] }), { server_address: null, platforms: [] })
checkEq('S5 响应为 null → 全缺省',
  normalize(null), { server_address: null, platforms: [] })
checkEq('S6 响应残缺（空对象）→ 全缺省',
  normalize({}), { server_address: null, platforms: [] })
checkEq('S7 platforms 非数组 → 空数组',
  normalize({ server_address: ADDR, platforms: 'x' }), { server_address: ADDR, platforms: [] })

/* ---------- 模板接线：地址凭归一化结果渲染 ---------- */
check('C6 页面有 serverAddress 计算属性', view.includes('const serverAddress = computed'))
check('C7 地址区凭 serverAddress 分支渲染', view.includes('v-if="serverAddress"'))
check('C8 复制逻辑走 serverAddress', view.includes('serverAddress.value'))
const template = view.slice(0, view.indexOf('<script'))
check('C9 模板不再直读 payload.server_address（只许经 serverAddress 计算属性）', !template.includes('payload?.server_address'))

console.log(`\n全部 ${passed} 项通过`)
