// MSE 精确取簇路由回归测试（2026-11-25 第十一轮）：
// 原生 demuxer 对 HEVC MKV 冷跳是顺序扫描（十轮诊断定案），容器
// mkv 且本机 MSE+HEVC 通时改走 AVPlayer MSE 管线（libmedia 自带
// demuxer 按 Cues 跳簇），冷跳不再扫全程。不支持就保持原生行为。
//
// 模型：
//   - 门控是纯函数（mseSeekCandidates / probeMseSeekSupport /
//     mseSeekGate / shouldMseSeekRoute），isTypeSupported 用注入
//     的问询函数替，不触真实浏览器；
//   - MSE seek 路径走假播放器 fakeAvPlayer.mjs（与 softSeekResume
//     同口径）：useMSE 由 FakeAVPlayer.nextUseMSE 预置——true =
//     平台走了 MSE 管线，false = 被 libmedia 否回解码管线（否决
//     看门狗必须判失败）；缺省 undefined = 旧行为（wasm 路）。
// 运行：cd user_frontend && node tests/mseSeekRoute.test.mjs
import { register } from 'node:module'
import assert from 'node:assert'

register('./mseSeekRoute.hooks.mjs', import.meta.url)

const {
  mseSeekCandidates,
  probeMseSeekSupport,
  mseSeekGate,
  shouldMseSeekRoute,
  createSoftPlayer,
} = await import('../src/utils/softDecode.ts')
const { instances: fakePlayers, default: FakeAVPlayer } = await import('./fakeAvPlayer.mjs')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const withStreams = (video, audio, container = 'mkv') => ({
  Id: 's1',
  Container: container,
  MediaStreams: [
    { Index: 0, Type: 'Video', Codec: video, Width: 1920, Height: 760 },
    ...(audio ? [{ Index: 1, Type: 'Audio', Codec: audio }] : []),
  ],
})
const noStreams = (container = 'mkv') => ({ Id: 's1', Container: container, MediaStreams: [] })

const allowAll = () => true
const denyAll = () => false
const allowOnly = (...mimes) => (m) => mimes.includes(m)

// --- 候选串：有流信息走真实参数；无流信息 + mkv 走合成 HEVC 候选 ---
check(
  'candidates hevc+ac3 streams: real params, main10 first',
  mseSeekCandidates(withStreams('hevc', 'ac3')).map((c) => c.mime),
  [
    'video/mp4; codecs="hvc1.2.4.L150.B0"',
    'video/mp4; codecs="hev1.2.4.L150.B0"',
    'video/mp4; codecs="hvc1.1.6.L150.B0"',
    'video/mp4; codecs="hvc1"',
    'audio/mp4; codecs="ac-3"',
  ],
)
check(
  'candidates h264 streams: avc1',
  mseSeekCandidates(withStreams('h264', 'aac')).map((c) => c.mime),
  [
    'video/mp4; codecs="avc1.640028"',
    'video/mp4; codecs="avc1"',
    'audio/mp4; codecs="mp4a.40.2"',
    'audio/mp4; codecs="mp4a.40.5"',
  ],
)
check(
  'candidates empty mkv: synthetic hevc + common audio',
  mseSeekCandidates(noStreams('mkv')).map((c) => c.mime),
  [
    'video/mp4; codecs="hvc1.1.6.L120.90"',
    'video/mp4; codecs="hev1.1.6.L120.90"',
    'video/mp4; codecs="hvc1.2.4.L150.B0"',
    'video/mp4; codecs="hvc1"',
    'audio/mp4; codecs="ac-3"',
    'audio/mp4; codecs="ec-3"',
    'audio/mp4; codecs="mp4a.40.2"',
  ],
)
check('candidates empty mp4: none', mseSeekCandidates(noStreams('mp4')), [])
check(
  'candidates empty no-container: none',
  mseSeekCandidates({ Id: 's1', MediaStreams: [] }),
  [],
)

// --- 预检：合成候选走同一套闸门（视频须带参数串被认，音频至少一条被认） ---
check(
  'preflight empty mkv all supported => pass',
  probeMseSeekSupport(noStreams('mkv'), allowAll),
  { supported: true, gate: 'pass' },
)
check(
  'preflight empty mkv video denied => video-unsupported',
  probeMseSeekSupport(noStreams('mkv'), denyAll),
  { supported: false, gate: 'video-unsupported' },
)
check(
  'preflight empty mkv audio denied => audio-unsupported',
  probeMseSeekSupport(
    noStreams('mkv'),
    allowOnly('video/mp4; codecs="hvc1.1.6.L120.90"'),
  ),
  { supported: false, gate: 'audio-unsupported' },
)
check(
  'preflight empty mkv bare hvc1 only => video-unsupported',
  probeMseSeekSupport(noStreams('mkv'), allowOnly('video/mp4; codecs="hvc1"')),
  { supported: false, gate: 'video-unsupported' },
)
check(
  'preflight empty mkv no mse api => no-mse-api',
  probeMseSeekSupport(noStreams('mkv')),
  { supported: false, gate: 'no-mse-api' },
)
check(
  'preflight hevc+ac3 streams all supported => pass',
  probeMseSeekSupport(withStreams('hevc', 'ac3'), allowAll),
  { supported: true, gate: 'pass' },
)
check(
  'preflight hevc streams audio denied => audio-unsupported',
  probeMseSeekSupport(
    withStreams('hevc', 'ac3'),
    allowOnly('video/mp4; codecs="hvc1.2.4.L150.B0"'),
  ),
  { supported: false, gate: 'audio-unsupported' },
)
check(
  'gate empty mkv no mse api',
  mseSeekGate({ mseApi: '', entries: [] }, noStreams('mkv')),
  'no-mse-api',
)

// --- 路由决策：只认 mkv；有流信息时只认 HEVC 视频；无流信息按 HEVC 猜 ---
check('route mkv hevc streams + mse => true', shouldMseSeekRoute(withStreams('hevc', 'ac3'), true), true)
check('route mkv h264 streams + mse => false', shouldMseSeekRoute(withStreams('h264', 'aac'), true), false)
check('route mkv av1 streams + mse => false', shouldMseSeekRoute(withStreams('av1', 'aac'), true), false)
check('route mkv empty + mse => true', shouldMseSeekRoute(noStreams('mkv'), true), true)
check('route mp4 hevc + mse => false', shouldMseSeekRoute(withStreams('hevc', 'ac3', 'mp4'), true), false)
check('route mkv hevc + no mse => false', shouldMseSeekRoute(withStreams('hevc', 'ac3'), false), false)
check(
  'route MKV uppercase container => true',
  shouldMseSeekRoute(withStreams('hevc', 'ac3', 'MKV'), true),
  true,
)

// --- MSE seek 路径（假播放器）：构造挂 checkUseMSE，seek 走看门狗落定 ---
const PLAY_URL =
  'https://cdn.test/emby/Videos/0123456789abcdef0123456789abcdef/stream.mkv?MediaSourceId=0123456789abcdef0123456789abcdef:mount_relay'

function makeHooks() {
  const calls = { seeking: [], seekFailed: [], time: [], error: [] }
  return {
    calls,
    hooks: {
      onFirstVideo: () => {},
      onFirstAudio: () => {},
      onPlaying: () => {},
      onPaused: () => {},
      onTime: (s) => calls.time.push(s),
      onEnded: () => {},
      onError: (m) => calls.error.push(m),
      onSeekingChange: (v) => calls.seeking.push(v),
      onSeekFailed: (m) => calls.seekFailed.push(m),
    },
  }
}

{
  // MSE 管线已接通：checkUseMSE 挂上、useMSE 为真，seek 发到实例、
  // 时间推进后看门狗落定，不判失败。
  FakeAVPlayer.nextUseMSE = true
  const { calls, hooks } = makeHooks()
  const before = fakePlayers.length
  let handle
  try {
    handle = await createSoftPlayer({}, PLAY_URL, hooks, {
      ext: 'mkv',
      mse: true,
      startTimeoutMs: 5000,
      seekTimeoutMs: 5000,
      seekResumeMs: 200,
      seekResumeSustainMs: 50,
      seekPollMs: 20,
    })
    const player = fakePlayers[before]
    assert.ok(player, 'fake player instance created')
    check('mse seek: checkUseMSE mounted', typeof player.options.checkUseMSE, 'function')
    check('mse seek: checkUseMSE says yes', player.options.checkUseMSE(), true)
    check('mse seek: useMSE true', player.useMSE, true)
    const p = handle.seek(3247)
    player.emit('time', 3247000n)
    await sleep(50)
    player.tick(0.6)
    await sleep(100)
    player.tick(0.6)
    await p
    await sleep(900)
    check('mse seek: target issued to player', player.seekCalls, [3247000n])
    check('mse seek: completes without failure', calls.seekFailed, [])
    check('mse seek: seeking pill clears', calls.seeking, [true, false])
  } finally {
    FakeAVPlayer.nextUseMSE = undefined
    if (handle) await handle.destroy()
  }
}

{
  // MSE 否决：libmedia 内部否回解码管线（useMSE=false），构造必须
  // 判失败——不许静默掉进 wasm 解码（4K 崩页的那条路）。
  FakeAVPlayer.nextUseMSE = false
  const { hooks } = makeHooks()
  let rejected = null
  try {
    await createSoftPlayer({}, PLAY_URL, hooks, {
      ext: 'mkv',
      mse: true,
      startTimeoutMs: 5000,
    })
  } catch (e) {
    rejected = e instanceof Error ? e.message : String(e)
  } finally {
    FakeAVPlayer.nextUseMSE = undefined
  }
  check('mse veto: construction rejects', (rejected || '').includes('mse pipeline vetoed'), true)
}

console.log(`\n${passed} passed`)
