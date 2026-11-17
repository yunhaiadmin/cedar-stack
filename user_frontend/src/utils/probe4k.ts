/**
 * 4K MSE 探针（一次性真机诊断，2026-11-17）：验证「4K 改走 libmedia
 * 自带 MSE 管线」在用户 iPhone Safari 上到底通不通。根因与路线见
 * 仓库外报告《网页 4K 播放路线调查》（帧进页面内存排队撞 iOS 上限；
 * MSE 把画面交给系统播、不进页面内存，是友站的第一顺位）。
 *
 * libmedia 1.3.1 的管线选择（public/libmedia/1.3.1 产物逐行实证）：
 * AVPlayer.play() 内部 checkUseMSE 先用 findBestStream 选最佳音/视频
 * 流做 ManagedMediaSource.isTypeSupported 检查——不过关直接否掉
 * MSE、应用回调没机会开口；之后才轮到构造选项里的 checkUseMSE
 * 回调改判。findBestStream 同样留了构造选项口子，可由应用层指定
 * 选哪条流。本探针的打法：
 *
 * 1. 预检（纯页面 API）：按流的真实参数拼 MSE mime 串问
 *    isTypeSupported；WebCodecs 也按真实参数（Main10/L5.0）复核，
 *    与 #261 写错的探测参数（Main/8 位/L3.1）形成对照证据。
 * 2. 逐次尝试：checkUseMSE 回调恒回 true 请求改判 MSE；第一次用
 *    libmedia 默认选轨（本样本即 eac3 主轨），之后用 findBestStream
 *    依次强制每条备选音轨各试一次。
 * 3. 安全闸门：每次尝试先过预检——目标音轨/视频的 mime 平台不认
 *    就不构造播放器。内部一旦否掉 MSE 就会掉进解码管线
 *    （wasm/WebCodecs），那正是 4K 崩页的路，探针不许把用户页面
 *    再送进去；侥幸漏网的由看门狗在 play 后读实例 useMSE 标志，
 *    发现不是 MSE 立刻销毁，帧来不及在页面内存里积压。
 *
 * 与正常播放路径的关系：只借取流口径（fetchPlaybackInfo +
 * pickSoftRelayUrl/resolveSoftDecodeUrl，与 WebPlayerView 软解同一
 * 条链）；WebPlayerView 与 createSoftPlayer 一字不动。探针页只对
 * staff 渲染、结果回传端点只对 staff 放行（backend/api/probe_4k.py）。
 */
import { normalizeAudioCodec, normalizeVideoCodec } from './decodeSupport'
import {
  SOFT_PLAYER_SCRIPT_URL,
  resolveSoftDecodeUrl,
  wasmPathForDecoder,
} from './softDecode'

/** 探针样本：《多喜一家人》第 1 集（item 2688161）的条目 guid */
export const PROBE_4K_SAMPLE_GUID = 'e5ddc1ecca84fbb218254fb4b21b5e31'

export interface ProbeStreamLike {
  Index: number
  Type: string
  Codec?: string
  Width?: number | null
  Height?: number | null
  Channels?: number | null
  BitDepth?: number | null
}

export interface ProbeSourceLike {
  Id: string
  Container?: string
  MediaStreams?: ProbeStreamLike[]
}

// ---------- 尝试计划（纯函数） ----------

export interface ProbeAttemptPlan {
  /** 回传后端的尝试标识：default / audio-<流Index> */
  key: string
  /** 页面展示与留痕用的人话标签 */
  label: string
  /** findBestStream 强制的音轨流 Index；null = 用 libmedia 默认选轨 */
  audioIndex: number | null
  /** 本次尝试目标音轨的流 Index（默认尝试 = 第一条音轨） */
  targetAudioIndex: number | null
  /** 本次尝试目标音轨的归一化编码 */
  audioCodec: string
  /** 视频流 Index（findBestStream 选视频用） */
  videoIndex: number | null
}

/**
 * 尝试序列：默认选轨一次（libmedia 自己的 findBestStream，本样本
 * 会选第一条音轨 eac3）＋其余每条音轨各强制一次。顺序即执行顺序：
 * 先主轨、被堵/报错再逐条换备选轨——与任务口径一致。
 */
export function planProbeAttempts(source: ProbeSourceLike): ProbeAttemptPlan[] {
  const streams = source.MediaStreams || []
  const audios = streams.filter((s) => s.Type === 'Audio')
  const video = streams.find((s) => s.Type === 'Video')
  const videoIndex = video ? video.Index : null
  if (!audios.length) {
    return [
      {
        key: 'default',
        label: '默认选轨（源无音轨）',
        audioIndex: null,
        targetAudioIndex: null,
        audioCodec: '',
        videoIndex,
      },
    ]
  }
  const first = audios[0]
  const plans: ProbeAttemptPlan[] = [
    {
      key: 'default',
      label: `默认选轨（首条音轨 ${normalizeAudioCodec(first.Codec)} #${first.Index}）`,
      audioIndex: null,
      targetAudioIndex: first.Index,
      audioCodec: normalizeAudioCodec(first.Codec),
      videoIndex,
    },
  ]
  for (const a of audios.slice(1)) {
    plans.push({
      key: `audio-${a.Index}`,
      label: `强制音轨 ${normalizeAudioCodec(a.Codec)} #${a.Index}`,
      audioIndex: a.Index,
      targetAudioIndex: a.Index,
      audioCodec: normalizeAudioCodec(a.Codec),
      videoIndex,
    })
  }
  return plans
}

// ---------- libmedia 流识别与选轨（纯函数） ----------

/** libmedia formatContext.streams 的元素（内部对象，只读不改） */
export interface LibmediaStreamLike {
  index?: unknown
  disposition?: unknown
  codecpar?: unknown
  [key: string]: unknown
}

export type StreamMatchMethod = 'by-index' | 'by-position' | 'first' | 'none'

/**
 * 在 libmedia 的流清单里按文件流 Index 认一条流：优先读元素自带的
 * index 属性（demux 层 formatContext.streams 带 index，产物实证）；
 * 读不到退回数组位置（流清单与文件流同序）。命中方式如实回带，
 * 认错轨时结论里能看出来。
 */
export function matchLibmediaStream(
  streams: LibmediaStreamLike[] | undefined | null,
  targetIndex: number | null,
): { stream: LibmediaStreamLike | undefined; method: StreamMatchMethod } {
  if (!streams?.length || targetIndex == null) {
    return { stream: undefined, method: 'none' }
  }
  const byIndex = streams.find(
    (s) => typeof s?.index === 'number' && s.index === targetIndex,
  )
  if (byIndex) return { stream: byIndex, method: 'by-index' }
  const byPos = streams[targetIndex]
  if (byPos) return { stream: byPos, method: 'by-position' }
  return { stream: undefined, method: 'none' }
}

/**
 * 给 AVPlayer 的 findBestStream 构造选项：视频按计划的 videoIndex、
 * 音频按计划强制的 audioIndex 选；其余类型（字幕 = 3 等）一律不选
 * （探针不挂字幕）。视频认不到时退回第一条流兜底——总比让 libmedia
 * 以为没视频强。每次调用的命中方式经 sink 留痕。
 */
export function makeProbeFindBestStream(
  plan: ProbeAttemptPlan,
  sink?: (mediaType: number, method: StreamMatchMethod) => void,
) {
  return (
    streams: LibmediaStreamLike[],
    mediaType: number,
  ): LibmediaStreamLike | undefined => {
    if (mediaType === 0) {
      const r = matchLibmediaStream(streams, plan.videoIndex)
      if (r.stream) {
        sink?.(0, r.method)
        return r.stream
      }
      sink?.(0, 'first')
      return streams?.[0]
    }
    if (mediaType === 1) {
      const r = matchLibmediaStream(streams, plan.audioIndex)
      sink?.(1, r.method)
      return r.stream
    }
    return undefined
  }
}

/**
 * 把 libmedia 内部流对象压成可回传的安全摘要：只取 index /
 * disposition 与 codecpar 的具名属性（muxer 层 codecpar 带 codecId /
 * codecType 具名口径）；BigInt 等不可序列化字段一律不碰，任何异常
 * 都吞掉——摘要失败不能拖垮探针。
 */
export function summarizeLibmediaStream(
  s: LibmediaStreamLike | null | undefined,
): Record<string, unknown> {
  if (!s || typeof s !== 'object') return { kind: typeof s }
  const out: Record<string, unknown> = {}
  try {
    out.index = typeof s.index === 'number' ? s.index : null
  } catch {
    out.index = null
  }
  try {
    out.disposition = typeof s.disposition === 'number' ? s.disposition : null
  } catch {
    out.disposition = null
  }
  try {
    const cp = s.codecpar as Record<string, unknown> | null | undefined
    out.codecparKind = cp === null ? 'null' : typeof cp
    if (cp && typeof cp === 'object') {
      out.codecId = typeof cp.codecId === 'number' ? cp.codecId : null
      out.codecType = typeof cp.codecType === 'number' ? cp.codecType : null
    }
  } catch {
    out.codecparKind = 'error'
  }
  return out
}

// ---------- MSE mime 预检（纯拼串 + 浏览器问询） ----------

export interface MseMimeProbe {
  kind: 'video' | 'audio'
  codec: string
  mime: string
  /** 音频探针归属的流 Index；视频探针为 null */
  audioIndex: number | null
}

/**
 * 按流的真实参数拼 isTypeSupported 候选串。视频口径与 libmedia
 * 内部一致（hvc1 标签）；HEVC 档位按本样本实测（Main10 / level
 * 5.0 = L150）领衔，再补 Main 档与裸标签——平台若只认裸标签、
 * 不认带参数串，libmedia 用带参数串照样会被否，这个区别本身
 * 就是结论的一部分，逐串留痕不许合并。
 */
export function mseMimeProbes(source: ProbeSourceLike): MseMimeProbe[] {
  const streams = source.MediaStreams || []
  const probes: MseMimeProbe[] = []
  const video = streams.find((s) => s.Type === 'Video')
  const vc = normalizeVideoCodec(video?.Codec)
  if (vc === 'hevc') {
    for (const param of ['hvc1.2.4.L150.B0', 'hev1.2.4.L150.B0', 'hvc1.1.6.L150.B0', 'hvc1']) {
      probes.push({ kind: 'video', codec: 'hevc', mime: `video/mp4; codecs="${param}"`, audioIndex: null })
    }
  } else if (vc === 'h264') {
    for (const param of ['avc1.640028', 'avc1']) {
      probes.push({ kind: 'video', codec: 'h264', mime: `video/mp4; codecs="${param}"`, audioIndex: null })
    }
  }
  for (const a of streams.filter((s) => s.Type === 'Audio')) {
    const ac = normalizeAudioCodec(a.Codec)
    if (ac === 'eac3') {
      probes.push({ kind: 'audio', codec: 'eac3', mime: 'audio/mp4; codecs="ec-3"', audioIndex: a.Index })
    } else if (ac === 'ac3') {
      probes.push({ kind: 'audio', codec: 'ac3', mime: 'audio/mp4; codecs="ac-3"', audioIndex: a.Index })
    } else if (ac === 'aac') {
      probes.push({ kind: 'audio', codec: 'aac', mime: 'audio/mp4; codecs="mp4a.40.2"', audioIndex: a.Index })
      probes.push({ kind: 'audio', codec: 'aac', mime: 'audio/mp4; codecs="mp4a.40.5"', audioIndex: a.Index })
    }
  }
  return probes
}

export interface PreflightEntry extends MseMimeProbe {
  /** true/false = 平台明确答复；null = 无 MSE API 或问询异常 */
  supported: boolean | null
}

export interface ProbePreflight {
  /** 实际用的 MSE 接口：iOS 17.1+ 是 ManagedMediaSource */
  mseApi: 'ManagedMediaSource' | 'MediaSource' | ''
  crossOriginIsolated: boolean
  entries: PreflightEntry[]
  /** WebCodecs 按真实参数复核（与 #261 的错误参数对照留证） */
  webcodecs: Array<{ codec: string; supported: boolean | null }>
}

interface MseCtorLike {
  isTypeSupported(mime: string): boolean
}

interface WebCodecsCtorLike {
  isConfigSupported(cfg: Record<string, unknown>): Promise<{ supported?: boolean }>
}

/** 在真机上跑一遍预检：MSE mime 矩阵 + 环境位 + WebCodecs 真实参数复核 */
export async function runMsePreflight(source: ProbeSourceLike): Promise<ProbePreflight> {
  const w = window as unknown as {
    ManagedMediaSource?: MseCtorLike
    MediaSource?: MseCtorLike
    VideoDecoder?: WebCodecsCtorLike
    crossOriginIsolated?: boolean
  }
  const mse = w.ManagedMediaSource || w.MediaSource
  const mseApi = w.ManagedMediaSource
    ? 'ManagedMediaSource'
    : w.MediaSource
      ? 'MediaSource'
      : ''
  const entries: PreflightEntry[] = mseMimeProbes(source).map((p) => {
    let supported: boolean | null = null
    if (mse) {
      try {
        supported = !!mse.isTypeSupported(p.mime)
      } catch {
        supported = null
      }
    }
    return { ...p, supported }
  })
  const webcodecs: ProbePreflight['webcodecs'] = []
  const video = (source.MediaStreams || []).find((s) => s.Type === 'Video')
  if (normalizeVideoCodec(video?.Codec) === 'hevc') {
    for (const codec of ['hev1.2.4.L150.B0', 'hvc1.2.4.L150.B0']) {
      let supported: boolean | null = null
      if (w.VideoDecoder) {
        try {
          const res = await w.VideoDecoder.isConfigSupported({
            codec,
            codedWidth: video?.Width || 3840,
            codedHeight: video?.Height || 2160,
            hardwareAcceleration: 'prefer-hardware',
          })
          supported = !!res?.supported
        } catch {
          supported = null
        }
      }
      webcodecs.push({ codec, supported })
    }
  }
  return {
    mseApi: mseApi as ProbePreflight['mseApi'],
    crossOriginIsolated: !!w.crossOriginIsolated,
    entries,
    webcodecs,
  }
}

// ---------- 尝试闸门与结论（纯函数） ----------

export type AttemptGate = 'pass' | 'no-mse-api' | 'video-unsupported' | 'audio-unsupported'

/**
 * 构造播放器前的闸门：视频任一带参数候选被认（裸标签不算——
 * libmedia 内部用的是带参数串，只认裸标签它照样否）且目标音轨
 * 任一候选被认，才许构造。闸门不过就跳过本次尝试：libmedia 内部
 * 否掉 MSE 后会掉进解码管线，探针不许拿用户页面去试那条崩页路。
 */
export function gateForAttempt(pre: ProbePreflight, plan: ProbeAttemptPlan): AttemptGate {
  if (!pre.mseApi) return 'no-mse-api'
  // 带参数串 = codec 参数里带 '.'（如 hvc1.2.4.L150.B0）；裸标签不算
  const parameterized = (mime: string) => (mime.split('"')[1] || '').includes('.')
  const videoOk = pre.entries.some(
    (e) => e.kind === 'video' && e.supported === true && parameterized(e.mime),
  )
  if (!videoOk) return 'video-unsupported'
  if (plan.targetAudioIndex == null) return 'pass'
  const audioOk = pre.entries.some(
    (e) => e.kind === 'audio' && e.audioIndex === plan.targetAudioIndex && e.supported === true,
  )
  return audioOk ? 'pass' : 'audio-unsupported'
}

export interface ProbeAttemptResult {
  attempt: string
  label: string
  audioIndex: number | null
  audioCodec: string
  gate: AttemptGate
  /** 实例 useMSE 标志（play 后读到）；没构造/没读到为 null */
  useMSE: boolean | null
  firstVideoRendered: boolean
  firstAudioRendered: boolean
  /** 容器里 <video> 实际在走（readyState/时间推进）——事件没发时的兜底信号 */
  videoObserved: boolean
  hasVideo: boolean | null
  hasAudio: boolean | null
  /** 出画后 8 秒观察窗首尾的播放头（秒），看时间有没有真推进 */
  currentTimeStart: number | null
  currentTimeEnd: number | null
  error: string
  /** 起播到出画的耗时（毫秒）；没出画为 null */
  startMs: number | null
  /** 被内部否回解码管线、看门狗抢先销毁 */
  abortedDecoderFallback: boolean
  /** findBestStream 各媒体类型的命中方式（0=视频 1=音频） */
  selectionMethods: Record<string, string>
  /** checkUseMSE 回调拿到的 libmedia 流清单摘要 */
  streamDump: Array<Record<string, unknown>>
  elapsedMs: number
}

/** 尝试结果 → 回传后端的结论短码 */
export function outcomeOf(r: ProbeAttemptResult): string {
  if (r.gate !== 'pass') return `gate-${r.gate}`
  if (r.abortedDecoderFallback) return 'vetoed-decoder'
  if (r.useMSE === true && (r.firstVideoRendered || r.videoObserved)) return 'mse-playing'
  if (r.error) return 'error'
  return 'timeout'
}

// ---------- 单次尝试执行（浏览器） ----------

type ProbeAVPlayerInstance = {
  load(source: string, options?: { ext?: string }): Promise<void>
  play(): Promise<void>
  destroy(): Promise<void>
  hasAudio(): boolean
  hasVideo(): boolean
  on(event: string, handler: (arg?: unknown) => void): void
  readonly currentTime: bigint
  /** play() 内 checkUseMSE 的判定结果（产物实证的实例属性） */
  useMSE?: boolean
}

/** 等首帧的上限：中继冷起播实测可达数十秒，给足 60 秒 */
const PROBE_FIRST_FRAME_TIMEOUT_MS = 60000
/** 出画后再观察 8 秒：看播放头推进、确认不是闪一帧就死 */
const PROBE_OBSERVE_MS = 8000

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/**
 * 跑一次尝试：闸门不过直接留痕返回（不构造播放器）；构造时
 * checkUseMSE 恒回 true 请求 MSE、按计划挂 findBestStream；
 * play 后看门狗轮询 useMSE——不是 MSE 立刻销毁（解码管线就是
 * 崩页的那条路，帧积压以百毫秒计时、抢在它前面收场）。
 */
export async function runProbeAttempt(
  container: HTMLDivElement,
  rawUrl: string,
  source: ProbeSourceLike,
  plan: ProbeAttemptPlan,
  pre: ProbePreflight,
): Promise<ProbeAttemptResult> {
  const startedAt = performance.now()
  const result: ProbeAttemptResult = {
    attempt: plan.key,
    label: plan.label,
    audioIndex: plan.audioIndex,
    audioCodec: plan.audioCodec,
    gate: gateForAttempt(pre, plan),
    useMSE: null,
    firstVideoRendered: false,
    firstAudioRendered: false,
    videoObserved: false,
    hasVideo: null,
    hasAudio: null,
    currentTimeStart: null,
    currentTimeEnd: null,
    error: '',
    startMs: null,
    abortedDecoderFallback: false,
    selectionMethods: {},
    streamDump: [],
    elapsedMs: 0,
  }
  const finish = () => {
    result.elapsedMs = Math.round(performance.now() - startedAt)
    return result
  }
  if (result.gate !== 'pass') return finish()

  let player: ProbeAVPlayerInstance | null = null
  try {
    const { default: AVPlayer } = (await import(
      /* @vite-ignore */ SOFT_PLAYER_SCRIPT_URL
    )) as { default: new (options: Record<string, unknown>) => unknown }
    const playUrl = await resolveSoftDecodeUrl(rawUrl)
    let firstVideoAt = 0
    let errorMsg = ''
    const options: Record<string, unknown> = {
      container,
      // 解码管线全关：本探针只问 MSE 通不通；万一被内部否回解码
      // 管线，看门狗也会抢先销毁，不让它真跑起来
      enableHardware: false,
      enableWebCodecs: false,
      preLoadTime: 10,
      getWasm: (type: string, codecId?: number) => {
        if (type === 'decoder') return wasmPathForDecoder(codecId)
        if (type === 'resampler') return '/wasm/resample/resample-simd.wasm'
        if (type === 'stretchpitcher') return '/wasm/stretchpitch/stretchpitch-simd.wasm'
        return undefined
      },
      checkUseMSE: (streams: LibmediaStreamLike[]) => {
        result.streamDump = (streams || []).map(summarizeLibmediaStream)
        return true
      },
    }
    if (plan.audioIndex != null) {
      options.findBestStream = makeProbeFindBestStream(
        plan,
        (mediaType, method) => {
          result.selectionMethods[String(mediaType)] = method
        },
      )
    }
    player = new AVPlayer(options) as unknown as ProbeAVPlayerInstance
    const p = player
    p.on('firstVideoRendered', () => {
      if (!firstVideoAt) firstVideoAt = performance.now()
    })
    p.on('firstAudioRendered', () => {
      result.firstAudioRendered = true
    })
    p.on('error', (err) => {
      if (!errorMsg) {
        errorMsg = err instanceof Error ? err.message : String(err ?? 'player error')
      }
    })
    p.on('timeout', () => {
      if (!errorMsg) errorMsg = 'player timeout event'
    })

    await p.load(playUrl, source.Container ? { ext: source.Container } : undefined)

    let playError = ''
    const playPromise = p.play().catch((e: unknown) => {
      playError = e instanceof Error ? e.message : String(e)
    })
    const deadline = performance.now() + PROBE_FIRST_FRAME_TIMEOUT_MS
    while (performance.now() < deadline) {
      if (typeof p.useMSE === 'boolean' && result.useMSE === null) {
        result.useMSE = p.useMSE
      }
      if (result.useMSE === false) {
        result.abortedDecoderFallback = true
        break
      }
      if (firstVideoAt || errorMsg || playError) break
      const v = container.querySelector('video') as HTMLVideoElement | null
      if (v && (v.readyState >= 2 || v.currentTime > 0)) {
        result.videoObserved = true
        if (!firstVideoAt) firstVideoAt = performance.now()
        break
      }
      await sleep(120)
    }
    if (typeof p.useMSE === 'boolean' && result.useMSE === null) {
      result.useMSE = p.useMSE
    }
    result.firstVideoRendered = !!firstVideoAt
    if (firstVideoAt) result.startMs = Math.round(firstVideoAt - startedAt)
    result.error = (errorMsg || playError || '').slice(0, 500)

    if (result.abortedDecoderFallback) {
      // 不等 playPromise：解码管线已被销毁，它的承诺可能永不落定；
      // .catch 已挂好，不会留未处理拒绝
      try {
        await p.destroy()
      } catch {
        /* 销毁失败不掩盖结论 */
      }
      player = null
      return finish()
    }
    await playPromise

    if ((result.firstVideoRendered || result.videoObserved) && result.useMSE === true) {
      try {
        result.hasVideo = p.hasVideo()
      } catch {
        /* 读不到不算失败 */
      }
      try {
        result.hasAudio = p.hasAudio()
      } catch {
        /* 读不到不算失败 */
      }
      try {
        result.currentTimeStart = Number(p.currentTime) / 1000
      } catch {
        /* 读不到不算失败 */
      }
      await sleep(PROBE_OBSERVE_MS)
      try {
        result.currentTimeEnd = Number(p.currentTime) / 1000
      } catch {
        /* 读不到不算失败 */
      }
      const v = container.querySelector('video') as HTMLVideoElement | null
      if (v && v.currentTime > 0) result.videoObserved = true
    }
    return finish()
  } catch (e) {
    if (!result.error) {
      result.error = (e instanceof Error ? e.message : String(e)).slice(0, 500)
    }
    return finish()
  } finally {
    if (player) {
      try {
        await player.destroy()
      } catch {
        /* 销毁失败不掩盖结论 */
      }
    }
  }
}
