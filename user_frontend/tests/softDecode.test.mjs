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
  softDecodeResolutionGate,
  softDecodePipelineDecision,
  probeSoftMseSupport,
  runSoftMsePreflight,
  softMseGate,
  mseMimeCandidates,
  SOFTDECODE_MSE_ENABLED,
  SOFT_START_TIMEOUT_MS,
  SOFT_4K_BLOCK_MESSAGE,
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
check(
  'avplayer preload depth raised for relay soft decode (preLoadTime 10s)',
  softDecodeSrc.includes('preLoadTime: 10'),
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

// --- 软解分辨率护栏（2026-11-17 事故）：4K HEVC 软解在 iOS Safari ---
// 《多喜一家人》第 1 集 3840×2160 HEVC＋EAC3 走 wasm 软解，纯软件
// 解 4K 把 WebContent 内存打爆、页面被系统反复杀（Safari 报「重复
// 出现问题」、同窗 6 次 PlaybackInfo 重入）。护栏口径：只有本次确实
// 走软解管线（softPipeline=true，即 shouldSoftDecode 已放行）且所选
// MediaSource 视频流宽≥3840 或高≥2160 才拦；原生路径永不拦；
// 1080p 软解照常放行。判定用源自带 MediaStreams 的宽高，不用条目级字段。
const sizedSrc = (w, h, video = 'hevc', audio = 'eac3') => ({
  Id: 'sized1',
  Container: 'mkv',
  MediaStreams: [
    { Type: 'Video', Codec: video, Width: w, Height: h },
    ...(audio ? [{ Type: 'Audio', Codec: audio }] : []),
  ],
})
check(
  'gate: 4K hevc soft pipeline => blocked',
  softDecodeResolutionGate(sizedSrc(3840, 2160), true),
  { blocked: true, width: 3840, height: 2160, reason: 'ax-soft-decode' },
)
check(
  'gate: 1080p hevc soft pipeline => allowed',
  softDecodeResolutionGate(sizedSrc(1920, 1080), true),
  { blocked: false, width: 1920, height: 1080, reason: 'below-4k' },
)
check(
  'gate: 4K hevc native pipeline => allowed',
  softDecodeResolutionGate(sizedSrc(3840, 2160), false),
  { blocked: false, width: 3840, height: 2160, reason: 'native-pipeline' },
)
check(
  'gate: DCI 4K 4096x2160 soft => blocked',
  softDecodeResolutionGate(sizedSrc(4096, 2160), true).blocked,
  true,
)
check(
  'gate: portrait 2160x3840 soft => blocked',
  softDecodeResolutionGate(sizedSrc(2160, 3840), true).blocked,
  true,
)
check(
  'gate: width-only line 3840x1080 soft => blocked',
  softDecodeResolutionGate(sizedSrc(3840, 1080), true).blocked,
  true,
)
check(
  'gate: height-only line 1920x2160 soft => blocked',
  softDecodeResolutionGate(sizedSrc(1920, 2160), true).blocked,
  true,
)
check(
  'gate: just below line 3839x2159 soft => allowed',
  softDecodeResolutionGate(sizedSrc(3839, 2159), true).blocked,
  false,
)
check(
  'gate: 2K 2560x1440 soft => allowed',
  softDecodeResolutionGate(sizedSrc(2560, 1440), true).blocked,
  false,
)
check(
  'gate: missing dimensions soft => allowed',
  softDecodeResolutionGate(sizedSrc(null, null), true),
  { blocked: false, width: 0, height: 0, reason: 'below-4k' },
)
check(
  'gate: no video stream soft => allowed',
  softDecodeResolutionGate(
    { Id: 'nv', Container: 'mkv', MediaStreams: [{ Type: 'Audio', Codec: 'eac3' }] },
    true,
  ),
  { blocked: false, width: 0, height: 0, reason: 'no-video-stream' },
)
check(
  'gate: block message copy',
  SOFT_4K_BLOCK_MESSAGE,
  '这部片子是 4K 画质，手机浏览器放不动，请用客户端 App 观看',
)

// --- 弹幕软解门控恢复契约（回退 #258 的软解放行，恢复 cb93c074 口径）---
// #258 给软解路径放行了弹幕层，与 4K 崩溃同批上线；先回退到软解下
// 无弹幕的旧口径排除叠加因素：弹幕层不渲染、tick 不冒弹幕、发送后
// 不当场冒出、点弹幕钮只给「软解模式暂不支持弹幕」提示。
const wpvSrc = readFileSync(
  new URL('../src/views/WebPlayerView.vue', import.meta.url),
  'utf8',
)
check(
  'danmaku layer gated off during soft decode',
  wpvSrc.includes('v-if="danmakuSiteOn && danmaku.enabled && !softActive"'),
  true,
)
check(
  'danmaku tick skips during soft decode',
  wpvSrc.includes('!danmaku.value.enabled ||\n    softActive.value ||\n    phase.value'),
  true,
)
check(
  'danmaku send does not spawn during soft decode',
  wpvSrc.includes('if (danmaku.value.enabled && !softActive.value) spawnDanmaku(res.item)'),
  true,
)
check(
  'openDanmaku refuses during soft decode with toast',
  wpvSrc.includes(
    "function openDanmaku(): void {\n  if (softActive.value) {\n    showToast('软解模式暂不支持弹幕')\n    return\n  }\n  danmakuOpen.value = true\n}",
  ),
  true,
)

// --- 护栏接线契约：起播软解前先过护栏判定，命中走提示卡文案 ---
check(
  'player consults resolution gate before creating soft player',
  wpvSrc.includes('softDecodeResolutionGate(source, true).blocked'),
  true,
)
check(
  'player shows gate block message on the notice card',
  wpvSrc.includes('SOFT_4K_BLOCK_MESSAGE'),
  true,
)

// --- 4K MSE 分流决策（2026-11-17 晚）：达 4K 线的软解源第一顺位 ---
// 走 libmedia 自带 MSE 原生管线（帧交系统播、不进页面内存）；预检
// 不过/开关关回到 #260 的护栏提示卡。WebCodecs 硬解尝试（#261）已
// 被真机证明照样崩页、整段退役。未达 4K 线的软解源永远走 wasm、
// 行为不变。阈值复用 softDecodeResolutionGate，不另立一套。
check(
  'pipeline: 4K + switch on + mse supported => mse',
  softDecodePipelineDecision(sizedSrc(3840, 2160), true, { enabled: true, supported: true }),
  { pipeline: 'mse', width: 3840, height: 2160, reason: 'ax-mse' },
)
check(
  'pipeline: 4K + mse unsupported => blocked',
  softDecodePipelineDecision(sizedSrc(3840, 2160), true, { enabled: true, supported: false }),
  { pipeline: 'blocked', width: 3840, height: 2160, reason: 'mse-unsupported' },
)
check(
  'pipeline: 4K + switch off => blocked even if mse supported',
  softDecodePipelineDecision(sizedSrc(3840, 2160), true, { enabled: false, supported: true }),
  { pipeline: 'blocked', width: 3840, height: 2160, reason: 'mse-switch-off' },
)
check(
  'pipeline: 1080p + mse supported => wasm unchanged',
  softDecodePipelineDecision(sizedSrc(1920, 1080), true, { enabled: true, supported: true }),
  { pipeline: 'wasm', width: 1920, height: 1080, reason: 'below-4k' },
)
check(
  'pipeline: 1080p + switch off => wasm unchanged',
  softDecodePipelineDecision(sizedSrc(1920, 1080), true, { enabled: false, supported: false }),
  { pipeline: 'wasm', width: 1920, height: 1080, reason: 'below-4k' },
)
check(
  'pipeline: 4K native pipeline => wasm decision (never consulted)',
  softDecodePipelineDecision(sizedSrc(3840, 2160), false, { enabled: true, supported: true }),
  { pipeline: 'wasm', width: 3840, height: 2160, reason: 'native-pipeline' },
)
check(
  'pipeline: DCI 4K + mse supported => mse',
  softDecodePipelineDecision(sizedSrc(4096, 2160), true, { enabled: true, supported: true }).pipeline,
  'mse',
)
check('mse switch defaults on (real-device verified 2026-11-17)', SOFTDECODE_MSE_ENABLED, true)

// --- MSE mime 候选：按流的真实参数拼串（候选策略照探针页 #279，---
// 参数改按所选流的 Profile/Level/BitDepth 现拼，不写死样本值）。
const mseSample = {
  Id: 'mse1',
  Container: 'mkv',
  MediaStreams: [
    { Index: 0, Type: 'Video', Codec: 'hevc', Width: 3840, Height: 2160, Profile: 'Main 10', Level: 150, BitDepth: 10 },
    { Index: 1, Type: 'Audio', Codec: 'eac3', Channels: 6 },
    { Index: 2, Type: 'Audio', Codec: 'aac', Channels: 2 },
  ],
}
check(
  'mse mime: hevc video candidates from real params (Main10 L150)',
  mseMimeCandidates(mseSample).filter((c) => c.kind === 'video').map((c) => c.mime),
  [
    'video/mp4; codecs="hvc1.2.4.L150.B0"',
    'video/mp4; codecs="hev1.2.4.L150.B0"',
    'video/mp4; codecs="hvc1.1.6.L150.B0"',
    'video/mp4; codecs="hvc1"',
  ],
)
check(
  'mse mime: Main profile L153 leads with 1.6 param',
  mseMimeCandidates({
    Id: 'x',
    MediaStreams: [{ Index: 0, Type: 'Video', Codec: 'hevc', Profile: 'Main', Level: 153, BitDepth: 8 }],
  }).filter((c) => c.kind === 'video').map((c) => c.mime)[0],
  'video/mp4; codecs="hvc1.1.6.L153.B0"',
)
check(
  'mse mime: missing profile/level falls back to Main10 L150 lead',
  mseMimeCandidates(sizedSrc(3840, 2160)).filter((c) => c.kind === 'video').map((c) => c.mime)[0],
  'video/mp4; codecs="hvc1.2.4.L150.B0"',
)
check(
  'mse mime: h264 video candidates',
  mseMimeCandidates({
    Id: 'x',
    MediaStreams: [{ Index: 0, Type: 'Video', Codec: 'h264', Width: 3840, Height: 2160 }],
  }).filter((c) => c.kind === 'video').map((c) => c.mime),
  ['video/mp4; codecs="avc1.640028"', 'video/mp4; codecs="avc1"'],
)
check(
  'mse mime: audio candidates for eac3 + aac tracks',
  mseMimeCandidates(mseSample).filter((c) => c.kind === 'audio').map((c) => c.mime),
  ['audio/mp4; codecs="ec-3"', 'audio/mp4; codecs="mp4a.40.2"', 'audio/mp4; codecs="mp4a.40.5"'],
)

// --- MSE 预检闸门（移植探针页 gateForAttempt 的默认选轨口径）：---
// 视频须有带参数候选被认（只认裸标签不算——libmedia 内部用的就是
// 带参数串），且默认音轨（第一条音轨）候选被认，才许构造播放器；
// 不过闸就不构造，绝不让 libmedia 内部否掉 MSE 后掉进解码管线。
check(
  'mse probe: no MSE API in environment => no-mse-api',
  probeSoftMseSupport(mseSample),
  { supported: false, gate: 'no-mse-api' },
)
check(
  'mse probe: all candidates supported => pass',
  probeSoftMseSupport(mseSample, () => true),
  { supported: true, gate: 'pass' },
)
check(
  'mse gate: all supported => pass',
  softMseGate(runSoftMsePreflight(mseSample, () => true), mseSample),
  'pass',
)
check(
  'mse gate: only bare video tag supported => video-unsupported',
  softMseGate(
    runSoftMsePreflight(mseSample, (mime) => mime === 'video/mp4; codecs="hvc1"' || mime.startsWith('audio/')),
    mseSample,
  ),
  'video-unsupported',
)
check(
  'mse gate: first audio track unsupported => audio-unsupported',
  softMseGate(runSoftMsePreflight(mseSample, (mime) => !mime.startsWith('audio/')), mseSample),
  'audio-unsupported',
)
check(
  'mse gate: no audio stream => pass on video alone',
  softMseGate(runSoftMsePreflight(sizedSrc(3840, 2160, 'hevc', null), () => true), sizedSrc(3840, 2160, 'hevc', null)),
  'pass',
)
check(
  'mse gate: no MSE API => no-mse-api',
  softMseGate(runSoftMsePreflight(mseSample), mseSample),
  'no-mse-api',
)

// --- MSE 接线契约：视图只在 4K 分流命中时把 mse 标志交给构造， ---
// 起播失败与播后报错都回落 #260 提示卡（showSoft4kBlocked 出现于
// 分流拦截、起播 catch、播后 onError 三处调用点）；构造侧以
// checkUseMSE 改判 + useMSE 看门狗保证真走 MSE，被否回解码管线
// 立刻销毁回落；WebCodecs 硬解路径整段退役、不许留作可选项。
check(
  'player probes MSE support for 4K sources',
  wpvSrc.includes('probeSoftMseSupport(source)'),
  true,
)
check(
  'player passes mse flag into soft player creation',
  wpvSrc.includes('mse: mseAttempt'),
  true,
)
check(
  'player falls back to 4K block card on mse failure (3 call sites)',
  wpvSrc.split('showSoft4kBlocked(source, verdict)').length - 1 >= 3,
  true,
)
check(
  'soft player forces MSE via checkUseMSE callback in mse mode',
  softDecodeSrc.includes('checkUseMSE: () => true'),
  true,
)
check(
  'soft player watchdog vetoes decoder fallback via useMSE flag',
  softDecodeSrc.includes('player.useMSE === false'),
  true,
)
check(
  'soft player keeps decoder switches off (no webcodecs decode path)',
  softDecodeSrc.includes('enableHardware: false') &&
    softDecodeSrc.includes('enableWebCodecs: false'),
  true,
)
check(
  'webcodecs hardware attempt retired from soft decode module',
  !softDecodeSrc.includes('probeSoftHardwareDecode') &&
    !softDecodeSrc.includes('SOFTDECODE_HARDWARE_ENABLED'),
  true,
)
check(
  'webcodecs hardware attempt retired from player view',
  !wpvSrc.includes('probeSoftHardwareDecode') &&
    !wpvSrc.includes('SOFTDECODE_HARDWARE_ENABLED'),
  true,
)

// --- seek 看门狗（2026-11-25 生产实测事故）：软解拖动后静默冻结 ---
// 《黑洞表面》1080p WEB-DL（H.264 mkv）走站内软解：起播正常、连续
// 播放无卡顿，但拖到 52% 后 60 秒以上不恢复——进度冻结、按钮仍是
// 播放态、无报错、无转圈。根因在接线：seek 打出去就没人管了——
// 视图层 .catch(()=>{}) 吞掉拒绝、包装层没订阅 libmedia 的 seeked
// 事件、也没有任何超时看门狗；AVPlayer 内部 seek 一旦卡住（demux
// 定位要经中继对 2.4GB 文件做远距离 Range 取流），UI 没有任何一条
// 路能知道。看门狗口径（SoftSeekGuard，纯状态机）：
// - begin(目标秒) 武装计时；seeked 事件/进度到达目标 → complete 撤防；
// - 计时到期或底层 seek promise 拒绝（fail）→ 先重试一次
//   （onRetry，重新下发 seek），重试仍无结果才 onTimeout 判失败，
//   且只报一次；
// - 新 seek 顶替在飞的 seek 时重试计数清零，不许拿旧 seek 的账判死
//   新 seek；dispose 后不再有任何回调。
const {
  SoftSeekGuard,
  softSeekArrived,
  SOFT_SEEK_TIMEOUT_MS,
  SOFT_SEEK_MAX_RETRIES,
  SOFT_SEEK_RESUME_MS,
  SOFT_SEEK_RESUME_ADVANCE_S,
} = await import('../src/utils/softDecode.ts')

function fakeTimers() {
  const timers = new Map()
  let seq = 0
  return {
    setTimer: (fn, ms) => {
      const id = ++seq
      timers.set(id, { fn, ms })
      return id
    },
    clearTimer: (id) => {
      timers.delete(id)
    },
    fireAll: () => {
      const pending = [...timers.values()]
      timers.clear()
      for (const t of pending) t.fn()
    },
    pendingMs: () => [...timers.values()].map((t) => t.ms),
    get size() {
      return timers.size
    },
  }
}

function makeGuard() {
  const timers = fakeTimers()
  const calls = { retry: [], timeout: 0, timeoutPhases: [] }
  const guard = new SoftSeekGuard({
    timeoutMs: 15000,
    onRetry: (attempt) => calls.retry.push(attempt),
    onTimeout: (phase) => {
      calls.timeout += 1
      calls.timeoutPhases.push(phase)
    },
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })
  return { guard, timers, calls }
}

check('seek guard: default timeout is 15s', SOFT_SEEK_TIMEOUT_MS, 15000)
check('seek guard: default max retries is 1', SOFT_SEEK_MAX_RETRIES, 1)

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  check('seek guard: pending after begin', guard.pending, true)
  guard.complete()
  check('seek guard: complete disarms', guard.pending, false)
  check('seek guard: complete clears timer', timers.size, 0)
  timers.fireAll()
  check('seek guard: no retry/timeout after complete', [calls.retry.length, calls.timeout], [0, 0])
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  timers.fireAll()
  check('seek guard: first timeout retries once', calls.retry, [1])
  check('seek guard: still pending during retry', guard.pending, true)
  guard.complete()
  timers.fireAll()
  check('seek guard: retry then complete => no timeout report', calls.timeout, 0)
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  timers.fireAll()
  timers.fireAll()
  check('seek guard: retry exhausted => timeout reported', calls.timeout, 1)
  check('seek guard: not pending after timeout', guard.pending, false)
  timers.fireAll()
  guard.fail()
  check('seek guard: timeout reported only once', calls.timeout, 1)
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  guard.fail()
  check('seek guard: rejection retries immediately (no timer wait)', calls.retry, [1])
  check('seek guard: timer re-armed after rejection retry', timers.size, 1)
  guard.fail()
  check('seek guard: second rejection => timeout reported', calls.timeout, 1)
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(100)
  timers.fireAll()
  check('seek guard: first seek used its retry', calls.retry, [1])
  guard.begin(500)
  timers.fireAll()
  check('seek guard: superseding seek resets retry budget', calls.retry, [1, 1])
  check('seek guard: superseded seek not judged dead', calls.timeout, 0)
  guard.complete()
  check('seek guard: superseding seek completes clean', guard.pending, false)
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  guard.dispose()
  timers.fireAll()
  check('seek guard: dispose silences everything', [calls.retry.length, calls.timeout], [0, 0])
}

// --- seek 到达判定：seeked 事件之外，进度读数到达目标附近也算完成 ---
// 关键帧落点可能略早于目标（容差 2 秒），越过目标更不用说；还停在
// 旧位置附近的读数不许误判完成（那正是卡死形态）。
check('seek arrived: lands 1s before target (keyframe)', softSeekArrived(300, 299), true)
check('seek arrived: exactly at tolerance edge', softSeekArrived(300, 298), true)
check('seek arrived: still near old position', softSeekArrived(300, 120), false)
check('seek arrived: just outside tolerance', softSeekArrived(300, 297.9), false)
check('seek arrived: played past target within elapsed window', softSeekArrived(300, 305, 10), true)
check('seek arrived: NaN reading never completes', softSeekArrived(300, NaN), false)
check(
  'seek arrived: backward seek, pre-landing reading at old far position must not complete',
  softSeekArrived(100, 3000, 0),
  false,
)

// --- seek 两阶段：到达后确认恢复（2026-11-25 第二轮复测事故） ---
// 第一轮看门狗只看位置：复测里时间永远停在目标值，轮询第一次就判
// 到达撤防。arrive() 转入确认阶段：确认窗口内时间不推进同样走
// 重试→判失败；暂停态由调用方直接 complete，不进确认阶段。
check('seek resume: default confirm window is 5s', SOFT_SEEK_RESUME_MS, 5000)
check('seek resume: default advance is 0.5s', SOFT_SEEK_RESUME_ADVANCE_S, 0.5)

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  check('seek guard: seeking timer is 15s', [...timers.pendingMs()], [15000])
  guard.arrive()
  check('seek guard: confirming after arrive', guard.confirming, true)
  check('seek guard: still pending while confirming', guard.pending, true)
  check('seek guard: arrive switches to confirm timer', [...timers.pendingMs()], [SOFT_SEEK_RESUME_MS])
  guard.arrive()
  check('seek guard: second arrive is a no-op', [timers.size, ...timers.pendingMs()], [1, SOFT_SEEK_RESUME_MS])
  timers.fireAll()
  check('seek guard: confirm timeout retries once', calls.retry, [1])
  check('seek guard: retry returns to seeking phase', [guard.confirming, ...timers.pendingMs()], [false, 15000])
  guard.complete()
  timers.fireAll()
  check('seek guard: retry then complete => no timeout report', calls.timeout, 0)
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  guard.arrive()
  timers.fireAll()
  check('seek guard: confirm retry used once', calls.retry, [1])
  guard.arrive()
  timers.fireAll()
  check('seek guard: confirm timeout after retry reports failure', calls.timeout, 1)
  check('seek guard: timeout phase is confirming', calls.timeoutPhases, ['confirming'])
  check('seek guard: not pending after confirm timeout', guard.pending, false)
  timers.fireAll()
  check('seek guard: confirm timeout reported only once', calls.timeout, 1)
}

{
  const { guard, timers } = makeGuard()
  guard.arrive()
  check('seek guard: arrive when idle is a no-op', timers.size, 0)
  guard.begin(300)
  guard.arrive()
  guard.complete()
  check('seek guard: complete from confirming disarms', [guard.pending, timers.size], [false, 0])
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(100)
  guard.arrive()
  timers.fireAll()
  check('seek guard: first seek used its retry in confirming', calls.retry, [1])
  guard.begin(500)
  check('seek guard: new seek during confirming resets to seeking timer', [...timers.pendingMs()], [15000])
  timers.fireAll()
  check('seek guard: superseding seek resets retry budget', calls.retry, [1, 1])
  check('seek guard: superseded seek not judged dead', calls.timeout, 0)
  guard.complete()
}

{
  const { guard, timers, calls } = makeGuard()
  guard.begin(300)
  guard.arrive()
  guard.dispose()
  timers.fireAll()
  check('seek guard: dispose from confirming silences everything', [calls.retry.length, calls.timeout], [0, 0])
}
check(
  'seek arrived: backward seek landed near target',
  softSeekArrived(100, 101, 1),
  true,
)

// --- 结构断言：包装层必须真的接上看门狗与 seeked 事件 ---
check(
  'soft player subscribes seeked event for the seek guard',
  softDecodeSrc.includes("player.on('seeked'"),
  true,
)
check(
  'soft player seek goes through the guard',
  softDecodeSrc.includes('seekGuard.begin('),
  true,
)
check(
  'view shows a seeking indicator for soft seeks',
  wpvSrc.includes('softSeeking'),
  true,
)
check(
  'view handles soft seek failure with a visible error',
  wpvSrc.includes('onSeekFailed'),
  true,
)


console.log(`\n${passed} passed`)
