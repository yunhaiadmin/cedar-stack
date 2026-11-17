// 4K MSE 探针决策回归测试（2026-11-17）：
// 运行：cd user_frontend && node tests/probe4k.test.mjs
// 惯例与 tests/softDecode.test.mjs 一致：node 直接运行、不引入
// vitest/jest；只测纯决策（尝试计划 / 选轨 / mime 拼串 / 闸门 /
// 结论短码 / 流摘要），不触 DOM、不真构造播放器。
import { register } from 'node:module'
import assert from 'node:assert'

register('./probe4k.hooks.mjs', import.meta.url)

const {
  planProbeAttempts,
  matchLibmediaStream,
  makeProbeFindBestStream,
  summarizeLibmediaStream,
  mseMimeProbes,
  gateForAttempt,
  outcomeOf,
} = await import('../src/utils/probe4k.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

// 样本源：《多喜一家人》第 1 集（0=hevc 视频、1=eac3、2/3=aac）
const sample = {
  Id: 'src1',
  Container: 'mkv',
  MediaStreams: [
    { Index: 0, Type: 'Video', Codec: 'hevc', Width: 3840, Height: 2160 },
    { Index: 1, Type: 'Audio', Codec: 'eac3', Channels: 6 },
    { Index: 2, Type: 'Audio', Codec: 'aac', Channels: 2 },
    { Index: 3, Type: 'Audio', Codec: 'aac', Channels: 2 },
  ],
}

// ---------- 尝试计划 ----------

const plans = planProbeAttempts(sample)
check('计划条数（默认+eac后两条aac）', plans.length, 3)
check('默认尝试', [plans[0].key, plans[0].audioIndex, plans[0].targetAudioIndex, plans[0].audioCodec, plans[0].videoIndex], ['default', null, 1, 'eac3', 0])
check('强制aac#2', [plans[1].key, plans[1].audioIndex, plans[1].targetAudioIndex], ['audio-2', 2, 2])
check('强制aac#3', [plans[2].key, plans[2].audioIndex, plans[2].targetAudioIndex], ['audio-3', 3, 3])
check(
  '单音轨源只有默认尝试',
  planProbeAttempts({ Id: 's', MediaStreams: [
    { Index: 0, Type: 'Video', Codec: 'hevc' },
    { Index: 1, Type: 'Audio', Codec: 'aac' },
  ] }).length,
  1,
)
check(
  '无音轨源',
  planProbeAttempts({ Id: 's', MediaStreams: [{ Index: 0, Type: 'Video', Codec: 'hevc' }] })
    .map((p) => [p.key, p.targetAudioIndex]),
  [['default', null]],
)

// ---------- 流识别 ----------

const indexed = [{ index: 0 }, { index: 1 }, { index: 2 }, { index: 3 }]
check('按index认轨', matchLibmediaStream(indexed, 2), { stream: indexed[2], method: 'by-index' })
const bare = [{}, {}, {}]
check('无index退回位置', matchLibmediaStream(bare, 1), { stream: bare[1], method: 'by-position' })
check('认不到', matchLibmediaStream(indexed, 9).method, 'none')
check('目标为空', matchLibmediaStream(indexed, null).method, 'none')

// ---------- findBestStream 口子 ----------

const seen = []
const picker = makeProbeFindBestStream(plans[1], (t, m) => seen.push([t, m]))
check('选视频', picker(indexed, 0), indexed[0])
check('选被强制的音轨', picker(indexed, 1), indexed[2])
check('字幕类型不选', picker(indexed, 3), undefined)
check('命中方式留痕', seen, [[0, 'by-index'], [1, 'by-index']])

// ---------- 流摘要 ----------

check(
  '摘要带具名codecpar',
  summarizeLibmediaStream({ index: 2, disposition: 1, codecpar: { codecId: 173, codecType: 0 } }),
  { index: 2, disposition: 1, codecparKind: 'object', codecId: 173, codecType: 0 },
)
check(
  '摘要指针型codecpar',
  summarizeLibmediaStream({ index: 1, codecpar: 4096 }),
  { index: 1, disposition: null, codecparKind: 'number' },
)
check('摘要空值', summarizeLibmediaStream(null), { kind: 'object' })

// ---------- mime 拼串 ----------

const probes = mseMimeProbes(sample)
check('视频首串为真实参数hvc1', probes[0].mime, 'video/mp4; codecs="hvc1.2.4.L150.B0"')
check('视频候选条数', probes.filter((p) => p.kind === 'video').length, 4)
check(
  'eac3探针',
  probes.filter((p) => p.kind === 'audio' && p.audioIndex === 1).map((p) => p.mime),
  ['audio/mp4; codecs="ec-3"'],
)
check(
  'aac探针两串',
  probes.filter((p) => p.kind === 'audio' && p.audioIndex === 2).map((p) => p.mime),
  ['audio/mp4; codecs="mp4a.40.2"', 'audio/mp4; codecs="mp4a.40.5"'],
)

// ---------- 闸门 ----------

const pre = (entries, mseApi = 'ManagedMediaSource') => ({
  mseApi,
  crossOriginIsolated: false,
  entries,
  webcodecs: [],
})
const entry = (kind, mime, supported, audioIndex = null) => ({ kind, codec: '', mime, audioIndex, supported })
const videoOk = entry('video', 'video/mp4; codecs="hvc1.2.4.L150.B0"', true)
const videoBareOnly = [
  entry('video', 'video/mp4; codecs="hvc1.2.4.L150.B0"', false),
  entry('video', 'video/mp4; codecs="hvc1"', true),
]
const eac3No = entry('audio', 'audio/mp4; codecs="ec-3"', false, 1)
const eac3Ok = entry('audio', 'audio/mp4; codecs="ec-3"', true, 1)
const aacOk = entry('audio', 'audio/mp4; codecs="mp4a.40.2"', true, 2)
const aacNo = entry('audio', 'audio/mp4; codecs="mp4a.40.2"', false, 2)

check('无MSE接口', gateForAttempt(pre([], ''), plans[0]), 'no-mse-api')
check('只认裸标签不算视频过关', gateForAttempt(pre([...videoBareOnly, eac3Ok]), plans[0]), 'video-unsupported')
check('eac3不认堵默认尝试', gateForAttempt(pre([videoOk, eac3No, aacOk]), plans[0]), 'audio-unsupported')
check('aac认则强制尝试放行', gateForAttempt(pre([videoOk, eac3No, aacOk]), plans[1]), 'pass')
check('aac不认堵强制尝试', gateForAttempt(pre([videoOk, aacNo]), plans[1]), 'audio-unsupported')
check('全认放行默认尝试', gateForAttempt(pre([videoOk, eac3Ok]), plans[0]), 'pass')

// ---------- 结论短码 ----------

const baseResult = {
  attempt: 'default', label: '', audioIndex: null, audioCodec: 'eac3',
  gate: 'pass', useMSE: null, firstVideoRendered: false, firstAudioRendered: false,
  videoObserved: false, hasVideo: null, hasAudio: null, currentTimeStart: null,
  currentTimeEnd: null, error: '', startMs: null, abortedDecoderFallback: false,
  selectionMethods: {}, streamDump: [], elapsedMs: 0,
}
check('闸门结论', outcomeOf({ ...baseResult, gate: 'audio-unsupported' }), 'gate-audio-unsupported')
check('被否回解码管线', outcomeOf({ ...baseResult, abortedDecoderFallback: true }), 'vetoed-decoder')
check('MSE出画', outcomeOf({ ...baseResult, useMSE: true, firstVideoRendered: true }), 'mse-playing')
check('MSE出画（video兜底信号）', outcomeOf({ ...baseResult, useMSE: true, videoObserved: true }), 'mse-playing')
check('报错', outcomeOf({ ...baseResult, error: 'boom' }), 'error')
check('超时', outcomeOf({ ...baseResult }), 'timeout')

console.log(`\n${passed} passed`)
