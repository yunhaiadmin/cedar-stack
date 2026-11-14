// 网页软解决策回归测试（2026-11-14 批 2）：
// 运行：cd user_frontend && node tests/softDecode.test.mjs
// 惯例与 tests/checkinReward.test.mjs 一致：node 直接运行、不引入
// vitest/jest；被测模块是纯决策逻辑，不触 DOM、不 import 播放器包。
import { register } from 'node:module'
import assert from 'node:assert'

register('./softDecode.hooks.mjs', import.meta.url)

const {
  shouldSoftDecode,
  softDecodeCodecSubset,
  wasmPathForDecoder,
  fetchSoftDecodeEnabled,
  __resetSoftDecodeEnabledCache,
} = await import('../src/utils/softDecode.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

const src = (video, audio, container = 'mp4') => ({
  Id: 's1',
  Container: container,
  MediaStreams: [
    { Type: 'Video', Codec: video, Width: 1920, Height: 1080 },
    ...(audio ? [{ Type: 'Audio', Codec: audio }] : []),
  ],
})
const unsupported = (badVideo, badAudio) => ({
  status: 'unsupported',
  badVideo,
  badAudio,
  videoName: badVideo ? 'HEVC' : '',
  audioName: badAudio ? 'AC3' : '',
  hardBlock: true,
})

// --- 分支 1：探测失败 + 子集内 + 开关开 → 走软解 ---
check(
  'hevc+ac3 unsupported + enabled => soft',
  shouldSoftDecode(unsupported(true, true), src('hevc', 'ac3'), true),
  true,
)
check(
  'h264+aac unsupported + enabled => soft',
  shouldSoftDecode(unsupported(true, false), src('h264', 'aac'), true),
  true,
)
check(
  'hevc no-audio unsupported + enabled => soft',
  shouldSoftDecode(unsupported(true, false), src('hevc', null), true),
  true,
)
check(
  'hevc+dts unsupported + enabled => soft',
  shouldSoftDecode(unsupported(false, true), src('hevc', 'dts'), true),
  true,
)

// --- 分支 2：开关关 → 不请求 wasm、不走软解 ---
check(
  'hevc+ac3 unsupported + disabled => no soft',
  shouldSoftDecode(unsupported(true, true), src('hevc', 'ac3'), false),
  false,
)

// --- 探测没说解不了 → 不走软解 ---
check(
  'supported verdict => no soft',
  shouldSoftDecode(
    { status: 'supported', badVideo: false, badAudio: false, videoName: '', audioName: '', hardBlock: false },
    src('hevc', 'ac3'),
    true,
  ),
  false,
)
check(
  'unknown verdict => no soft',
  shouldSoftDecode(
    { status: 'unknown', badVideo: false, badAudio: false, videoName: '', audioName: '', hardBlock: false },
    src('hevc', 'ac3'),
    true,
  ),
  false,
)

// --- 子集外编码 → 不走软解（AV1/Opus 不在 wasm 集里） ---
check(
  'av1 video => no soft',
  shouldSoftDecode(unsupported(true, false), src('av1', 'aac'), true),
  false,
)
check(
  'opus audio => no soft',
  shouldSoftDecode(unsupported(false, true), src('h264', 'opus'), true),
  false,
)
check(
  'subset reason av1',
  softDecodeCodecSubset(src('av1', 'aac')).reason,
  'video-codec-out-of-subset',
)
check(
  'subset reason opus',
  softDecodeCodecSubset(src('h264', 'opus')).reason,
  'audio-codec-out-of-subset',
)
check(
  'subset ok hevc+eac3',
  softDecodeCodecSubset(src('hevc', 'eac3')).supported,
  true,
)

// --- wasm 取件映射：只认 simd 变体，不在表里的编解码返回 undefined ---
check('wasm h264', wasmPathForDecoder(27), '/wasm/decode/h264-simd.wasm')
check('wasm hevc', wasmPathForDecoder(173), '/wasm/decode/hevc-simd.wasm')
check('wasm aac', wasmPathForDecoder(86018), '/wasm/decode/aac-simd.wasm')
check('wasm ac3', wasmPathForDecoder(86019), '/wasm/decode/ac3-simd.wasm')
check('wasm dts', wasmPathForDecoder(86020), '/wasm/decode/dca-simd.wasm')
check('wasm eac3', wasmPathForDecoder(86056), '/wasm/decode/eac3-simd.wasm')
check('wasm av1 undefined', wasmPathForDecoder(226), undefined)
check('wasm opus undefined', wasmPathForDecoder(86055 - 1), undefined)

// --- 开关读取：公开端点只读布尔值；取不到按关处理；同页只读一次 ---
const realFetch = globalThis.fetch
try {
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    return { ok: true, json: async () => ({ enabled: true }) }
  }
  __resetSoftDecodeEnabledCache()
  check('fetch enabled true', await fetchSoftDecodeEnabled(), true)
  check('fetch cached once', calls, 1)
  check('fetch cached second call', await fetchSoftDecodeEnabled(), true)
  check('fetch still once', calls, 1)

  globalThis.fetch = async () => {
    throw new Error('network down')
  }
  __resetSoftDecodeEnabledCache()
  check('fetch throws => false', await fetchSoftDecodeEnabled(), false)

  globalThis.fetch = async () => ({ ok: false, json: async () => ({}) })
  __resetSoftDecodeEnabledCache()
  check('fetch non-ok => false', await fetchSoftDecodeEnabled(), false)
} finally {
  globalThis.fetch = realFetch
  __resetSoftDecodeEnabledCache()
}

console.log(`\n${passed} passed`)
