// 准备播放中央卡回归测试（2026-11-18，差异清单 G-168/170/172/175/176）：
// 运行：cd user_frontend && node tests/prepStatus.test.mjs
// 惯例与 tests/softDecode.test.mjs 一致：node 直接运行、不引入
// vitest/jest；被测模块是纯文案决策逻辑，不触 DOM。
// 另带源码结构断言：中央卡形态（包裹层＋恒定末行）与旧底部浮层
// （4 段进度条＋副行）不许双轨残留。
import assert from 'node:assert'
import { readFileSync } from 'node:fs'

const { prepStatusText, formatReceivedMb, PREP_DECODER_HINT } =
  await import('../src/utils/prepStatus.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

const base = {
  step: 0,
  slow: false,
  receivedBytes: null,
  container: 'mkv',
  softActive: false,
  softLoadDone: false,
}

// --- G-170：初始阶段无状态行 ---
check('step0 无状态行', prepStatusText({ ...base, step: 0 }), '')

// --- 打开源：解析完成、源已选定（step1）；软解 load 未完的 step2 仍是打开源 ---
check('step1 正在打开源', prepStatusText({ ...base, step: 1 }), '正在打开源')
check(
  '软解 step2 load 未完仍是打开源',
  prepStatusText({ ...base, step: 2, softActive: true, softLoadDone: false }),
  '正在打开源',
)

// --- 分析容器：原生 step2 起、软解 load 完成后；容器名小写带括号 ---
check(
  '原生 step2 分析容器（mkv）',
  prepStatusText({ ...base, step: 2 }),
  '正在分析容器（mkv）',
)
check(
  '容器名大写输入转小写',
  prepStatusText({ ...base, step: 2, container: 'MKV' }),
  '正在分析容器（mkv）',
)
check(
  '软解 load 完成 step2 分析容器',
  prepStatusText({ ...base, step: 2, softActive: true, softLoadDone: true }),
  '正在分析容器（mkv）',
)
check(
  'step3（metadata 已到、未 canplay）仍是分析容器',
  prepStatusText({ ...base, step: 3 }),
  '正在分析容器（mkv）',
)
check(
  '无容器信息时不带括号',
  prepStatusText({ ...base, step: 2, container: '' }),
  '正在分析容器',
)

// --- G-172/G-175：慢态主行＋真实已接收字节（一位小数、无空格） ---
check(
  '慢态带字节数',
  prepStatusText({ ...base, step: 2, slow: true, receivedBytes: 100_000 }),
  '网速较慢，仍在读取（已接收 0.1MB）',
)
check(
  '慢态字节数 1.4MB 变体',
  prepStatusText({ ...base, step: 1, slow: true, receivedBytes: 1_400_000 }),
  '网速较慢，仍在读取（已接收 1.4MB）',
)
check(
  '慢态无字节数据源时不编数',
  prepStatusText({ ...base, step: 2, slow: true, receivedBytes: null }),
  '网速较慢，仍在读取',
)
check('formatReceivedMb 0.1', formatReceivedMb(100_000), '0.1')
check('formatReceivedMb 1.4', formatReceivedMb(1_449_999), '1.4')
check('formatReceivedMb 0', formatReceivedMb(0), '0.0')

// --- G-176：末行恒定解码器提示，MB 数是我方 wasm 套件实测（5,907,642 B ≈ 5.9 MB），不许照抄友站 2–4 MB ---
check(
  '解码器提示文案（实测体积）',
  PREP_DECODER_HINT,
  '首次播放需要下载解码器（约 5.9 MB），缓存后下次秒开。',
)

// --- 结构断言：WebPlayerView 中央卡形态、旧浮层退役干净 ---
const wpv = readFileSync(new URL('../src/views/WebPlayerView.vue', import.meta.url), 'utf8')
check('WPV 有中央卡包裹层', wpv.includes('wp-prep-wrap'), true)
check('WPV 用恒定末行常量', wpv.includes('PREP_DECODER_HINT'), true)
check('WPV 状态行走 prepStatusText', wpv.includes('prepStatusText'), true)
check('WPV 无 4 段进度条残留', wpv.includes('wp-prep-dots'), false)
check('WPV 无副行残留', wpv.includes('prepSub'), false)
check('WPV 无旧步骤表残留', wpv.includes('PREP_STEPS'), false)
check('WPV 无我方独有首步文案', wpv.includes('正在解析版本与线路'), false)
check('WPV 无我方独有末步文案', wpv.includes('就绪，正在起播'), false)
check('WPV 无旧安抚副文案', wpv.includes('源响应有点慢'), false)

// --- 结构断言：softDecode 的真实字节口径（libmedia getStats().bufferReceiveBytes） ---
const soft = readFileSync(new URL('../src/utils/softDecode.ts', import.meta.url), 'utf8')
check('softDecode 读 bufferReceiveBytes', soft.includes('bufferReceiveBytes'), true)
check('softDecode handle 有 receivedBytes', soft.includes('receivedBytes()'), true)
check('softDecode 支持 onReceivedBytes 回调', soft.includes('onReceivedBytes'), true)

console.log(`\n${passed} passed`)
