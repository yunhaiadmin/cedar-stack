// 活跃会话合并模型测试（2026-11-18 UHDNow 差异定案 G-60/G-62~G-66）：
// 运行：cd user_frontend && node tests/activeSessions.test.mjs
// 测法：纯函数活在 src/utils/userAgent.ts 里（UA 解析＋会话时间绝对格式），
// 本测试直接 import 真执行；面板接线另加源码契约断言。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { parseUserAgent, formatSessionTime, sessionFilterCounts } from '../src/utils/userAgent.ts'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
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

/* ---------- UA 解析 ---------- */
checkEq('Chrome on Windows',
  JSON.stringify(parseUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')),
  JSON.stringify({ os: 'Windows', browser: 'Chrome', kind: 'desktop' }))
checkEq('Safari on iPhone',
  JSON.stringify(parseUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1')),
  JSON.stringify({ os: 'iOS', browser: 'Safari', kind: 'mobile' }))
checkEq('Safari on macOS',
  JSON.stringify(parseUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15')),
  JSON.stringify({ os: 'macOS', browser: 'Safari', kind: 'desktop' }))
checkEq('空 UA 回落未知',
  JSON.stringify(parseUserAgent('')),
  JSON.stringify({ os: '未知系统', browser: '未知浏览器', kind: 'desktop' }))
checkEq('Android Chrome',
  JSON.stringify(parseUserAgent('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.43 Mobile Safari/537.36')),
  JSON.stringify({ os: 'Android', browser: 'Chrome', kind: 'mobile' }))

/* ---------- 时间绝对格式 YYYY-MM-DD HH:mm ---------- */
checkEq('会话时间绝对格式', formatSessionTime('2026-11-18T10:05:00'), '2026-11-18 10:05')
checkEq('空时间不渲染', formatSessionTime(null), '—')
checkEq('无到期不渲染', formatSessionTime(undefined), '—')

/* ---------- 三筛选计数（媒体客户端只进「全部」） ---------- */
const items = [
  { kind: 'web' }, { kind: 'web' }, { kind: 'device' }, { kind: 'danmaku' },
]
checkEq('筛选计数', JSON.stringify(sessionFilterCounts(items)),
  JSON.stringify({ all: 4, web: 2, danmaku: 1 }))

/* ---------- 面板源码契约 ---------- */
const panel = readFileSync(join(ROOT, 'src/components/profile/ActiveSessionsPanel.vue'), 'utf8')
check('面板有三筛选', panel.includes('全部') && panel.includes('网页登录') && panel.includes('弹幕 Token'))
check('面板有锁定虚线胶囊', panel.includes('已锁定') && panel.includes('未锁定'))
check('面板调会话聚合接口', panel.includes('sessionsList'))
const secApi = readFileSync(join(ROOT, 'src/api/security.ts'), 'utf8')
check('会话聚合接口字面在位', secApi.includes('/api/user/security/sessions'))
check('面板不写死上限', !panel.includes('最多 10 个') && panel.includes('limit'))

const settings = readFileSync(join(ROOT, 'src/views/game/AccountSettingsView.vue'), 'utf8')
check('设置页活跃会话组用合并面板', settings.includes('ActiveSessionsPanel'))
check('设置页不再挂旧设备入口', !settings.includes('DevicesEntry'))

console.log(`\n${passed} passed`)
