// 网页软解决策回归测试（2026-11-14 批 2）：
// 运行：cd user_frontend && node tests/softDecode.test.mjs
// 惯例与 tests/userLogout.test.mjs 一致：node 直接运行、不引入
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
  variantOfStreamUrl,
  relayVariantUrl,
  resolveSoftDecodeUrl,
  pickSoftRelayUrl,
  humanizeSoftError,
  SOFT_START_TIMEOUT_MS,
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

// --- 变体读取：variant 参数优先，其次 MediaSourceId 后缀（服务端同口径） ---
const EA = 'https://ea.example/emby/Videos/g1/stream.mkv'
check(
  'variant from param',
  variantOfStreamUrl(`${EA}?MediaSourceId=g1&variant=drive_direct`),
  'drive_direct',
)
check(
  'variant from msid suffix',
  variantOfStreamUrl(`${EA}?MediaSourceId=g1:byos_drive_direct`),
  'byos_drive_direct',
)
check(
  'variant param wins over suffix',
  variantOfStreamUrl(`${EA}?MediaSourceId=g1:drive_relay&variant=drive_direct`),
  'drive_direct',
)
check('variant none', variantOfStreamUrl(`${EA}?MediaSourceId=g1&static=true`), '')

// --- 直链变体换中继：双保险位一起换；非直链/无标记原样 ---
check(
  'drive_direct -> drive_relay (param+suffix)',
  relayVariantUrl(`${EA}?MediaSourceId=g1:drive_direct&variant=drive_direct&api_key=k`),
  `${EA}?MediaSourceId=g1%3Adrive_relay&variant=drive_relay&api_key=k`,
)
check(
  'byos_drive_direct -> byos_drive_relay (suffix only)',
  relayVariantUrl(`${EA}?MediaSourceId=g1:byos_drive_direct`),
  `${EA}?MediaSourceId=g1%3Abyos_drive_relay&variant=byos_drive_relay`,
)
check(
  'drive_relay stays',
  relayVariantUrl(`${EA}?MediaSourceId=g1:drive_relay&variant=drive_relay`),
  `${EA}?MediaSourceId=g1:drive_relay&variant=drive_relay`,
)
check(
  'local_file stays',
  relayVariantUrl(`${EA}?MediaSourceId=g1&variant=local_file`),
  `${EA}?MediaSourceId=g1&variant=local_file`,
)
check('unmarked stays', relayVariantUrl(`${EA}?MediaSourceId=g1`), `${EA}?MediaSourceId=g1`)

// --- 取流地址裁决：假探测驱动，不碰真网络 ---
const probeReturning = (table) => async (url, redirect) => {
  const key = url.includes('variant=byos_drive_relay')
    ? 'byos'
    : url.includes('variant=drive_relay')
      ? 'drive'
      : 'orig'
  return table[key] ?? 'fail'
}
check(
  'marked direct skips probe',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1&variant=drive_direct`, {
    probe: async () => {
      throw new Error('probe must not run')
    },
  }),
  `${EA}?MediaSourceId=g1&variant=drive_relay`,
)
check(
  'unmarked bytes => original',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`, {
    probe: probeReturning({ orig: 'bytes' }),
  }),
  `${EA}?MediaSourceId=g1`,
)
check(
  'unmarked redirect => byos relay when it serves bytes',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`, {
    probe: probeReturning({ orig: 'redirect', byos: 'bytes' }),
  }),
  `${EA}?MediaSourceId=g1&variant=byos_drive_relay`,
)
check(
  'unmarked redirect => drive relay as second candidate',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`, {
    probe: probeReturning({ orig: 'redirect', byos: 'fail', drive: 'bytes' }),
  }),
  `${EA}?MediaSourceId=g1&variant=drive_relay`,
)
check(
  'unmarked redirect + all probes fail => original (player reports real error)',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`, {
    probe: probeReturning({ orig: 'redirect' }),
  }),
  `${EA}?MediaSourceId=g1`,
)
check(
  'unmarked probe fail => original',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`, {
    probe: probeReturning({ orig: 'fail', byos: 'bytes' }),
  }),
  `${EA}?MediaSourceId=g1`,
)
check(
  'relative url absolutized (worker fetch cannot parse relative)',
  await resolveSoftDecodeUrl('/emby/Videos/g1/stream.mkv?MediaSourceId=g1', {
    base: 'https://ea.example/play/123',
    probe: probeReturning({ orig: 'bytes' }),
  }),
  'https://ea.example/emby/Videos/g1/stream.mkv?MediaSourceId=g1',
)

// --- 默认探测（真 fetchProbe，桩 fetch）：结论一出必须掐断正文 ---
// 2026-11-16 生产实证：探测命中的地址回 200 整流时正文没掐断，
// 整文件在后台继续下载（单发漏下 90MB+），与播放器取流抢带宽。
{
  const realFetch = globalThis.fetch
  let cancelled = 0
  globalThis.fetch = async () => ({
    status: 206,
    type: 'basic',
    body: {
      cancel: async () => {
        cancelled += 1
      },
    },
  })
  try {
    const decided = await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`)
    check('default probe bytes => original url', decided, `${EA}?MediaSourceId=g1`)
    check('default probe cancels body after deciding', cancelled, 1)
  } finally {
    globalThis.fetch = realFetch
  }
}

// --- 起播守卫时长：慢源冷起播（demux 大文件 + 四件 wasm 下载） ---
// 实测全程约 45 秒，旧 45 秒守卫在解码器加载完、首帧未出时误杀。
check('soft start timeout default is 90s', SOFT_START_TIMEOUT_MS, 90000)

// --- 本例形态（2026-11-16）：挂载原件 + 本人 Drive 副本，H264+AC3 ---
// PlaybackInfo 下发 [drive_direct（副本 usercontent 302）, usercopy_relay]；
// 软解取流必须确定性选同条目的 usercopy_relay 兄弟源，而不是把
// drive_direct 机械换成 drive_relay（该条目 drive_relay 不可用、409）。
check(
  'h264+ac3 unsupported + enabled => soft (this case codecs)',
  shouldSoftDecode(unsupported(false, true), src('h264', 'ac3'), true),
  true,
)
const srcOf = (variant) => ({
  Id: `g1:${variant}`,
  DirectStreamUrl: `${EA}?MediaSourceId=g1%3A${variant}&variant=${variant}`,
})
check(
  'sibling pick: drive_direct selected => usercopy_relay source',
  pickSoftRelayUrl(
    [srcOf('drive_direct'), srcOf('usercopy_relay')],
    'g1:drive_direct',
  ),
  srcOf('usercopy_relay').DirectStreamUrl,
)
check(
  'sibling pick: relay selected itself => its own url',
  pickSoftRelayUrl(
    [srcOf('drive_direct'), srcOf('usercopy_relay')],
    'g1:usercopy_relay',
  ),
  srcOf('usercopy_relay').DirectStreamUrl,
)
check(
  'sibling pick: usercopy relay beats shared drive_relay',
  pickSoftRelayUrl(
    [srcOf('drive_direct'), srcOf('drive_relay'), srcOf('usercopy_relay')],
    'g1:drive_direct',
  ),
  srcOf('usercopy_relay').DirectStreamUrl,
)
check(
  'sibling pick: drive_relay when no usercopy sibling',
  pickSoftRelayUrl([srcOf('drive_direct'), srcOf('drive_relay')], 'g1:drive_direct'),
  srcOf('drive_relay').DirectStreamUrl,
)
check(
  'sibling pick: byos direct => byos relay',
  pickSoftRelayUrl(
    [srcOf('byos_drive_direct'), srcOf('byos_drive_relay')],
    'g1:byos_drive_direct',
  ),
  srcOf('byos_drive_relay').DirectStreamUrl,
)
check(
  'sibling pick: single direct source => empty (legacy flow)',
  pickSoftRelayUrl([srcOf('drive_direct')], 'g1:drive_direct'),
  '',
)
check(
  'sibling pick: unknown selection => empty',
  pickSoftRelayUrl([srcOf('drive_direct'), srcOf('usercopy_relay')], 'nope'),
  '',
)

// --- 无标记回落探测：usercopy_relay 进候选（byos 之后、drive 之前） ---
const probeReturning2 = (table) => async (url, redirect) => {
  const key = url.includes('variant=byos_drive_relay')
    ? 'byos'
    : url.includes('variant=usercopy_relay')
      ? 'usercopy'
      : url.includes('variant=drive_relay')
        ? 'drive'
        : 'orig'
  return table[key] ?? 'fail'
}
check(
  'unmarked redirect => usercopy relay when it serves bytes',
  await resolveSoftDecodeUrl(`${EA}?MediaSourceId=g1`, {
    probe: probeReturning2({ orig: 'redirect', byos: 'fail', usercopy: 'bytes' }),
  }),
  `${EA}?MediaSourceId=g1&variant=usercopy_relay`,
)

// --- 失败人话：分类命中 + libmedia 噪声前缀剥离 ---
check('humanize timeout', humanizeSoftError('soft decode start timeout'), '启动超时，网络或片源太慢')
check(
  'humanize open stream',
  humanizeSoftError('[packages/avplayer/src/AVPlayer.ts][line 1858] [fatal]: open stream failed, ret: -2, taskId: abc'),
  '视频流打不开，没能从服务器取到数据',
)
check('humanize wasm', humanizeSoftError('WebAssembly compile failed'), '解码器组件加载失败')
check('humanize empty', humanizeSoftError(''), '未知错误')
check(
  'humanize strips noise',
  humanizeSoftError('[packages/x.ts][line 9] [fatal]: something odd happened, taskId: zz'),
  'something odd happened,',
)

// --- 结构断言：AVPlayer 只能按 URL 加载原始构建，不许再走包导入 ---
// 经 rollup 重打包后它内部的 Worker 启动代码会报 W is not defined、
// load() 永远不返回（2026-11-15 生产软解无声卡死的第一层根因）。
import { readFileSync, existsSync, readdirSync } from 'node:fs'

const softDecodeSrc = readFileSync(
  new URL('../src/utils/softDecode.ts', import.meta.url),
  'utf8',
)
check(
  'player loaded via vite-ignore URL import',
  softDecodeSrc.includes('@vite-ignore') &&
    softDecodeSrc.includes("'/libmedia/1.3.1/avplayer.js'"),
  true,
)
check(
  'no bare package import of @libmedia/avplayer',
  !softDecodeSrc.includes("import('@libmedia/avplayer')") &&
    !softDecodeSrc.includes("from '@libmedia/avplayer'"),
  true,
)
const libmediaDir = new URL('../public/libmedia/1.3.1/', import.meta.url)
check('hosted player entry exists', existsSync(new URL('avplayer.js', libmediaDir)), true)
check(
  'hosted player entry is the raw webpack build',
  readFileSync(new URL('avplayer.js', libmediaDir), 'utf8').includes('webpackChunklibmedia'),
  true,
)
check(
  'hosted player chunks complete (>=30)',
  readdirSync(libmediaDir).filter((f) => f.endsWith('.avplayer.js')).length >= 30,
  true,
)

console.log(`\n${passed} passed`)
