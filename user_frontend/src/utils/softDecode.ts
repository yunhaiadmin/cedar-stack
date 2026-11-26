/**
 * 网页软解（2026-11-14 批 2）：浏览器硬解失败时，用站内自编 LGPL wasm
 * 解码器（libmedia AVPlayer + /wasm/decode/*-simd.wasm）兜底。
 *
 * 决策与播放器构造分开，决策是纯函数、可单测：
 * - shouldSoftDecode：探测结论 + 编解码子集 + 开关，三者齐了才走软解；
 * - createSoftPlayer：AVPlayer 只在这里按 URL 动态加载（站内静态
 *   托管的原始构建，见 public/libmedia/README.md——经 vite/rollup
 *   重新打包会打断它内部的 Worker 启动代码，不许走包导入）。
 *
 * 约束（批 1 实证）：只编了 simd 变体；FFmpeg 钉 7.1；wasm 不承诺
 * 4K/HDR——4K 走 libmedia 自带 MSE 原生管线（见下方管线决策一节），
 * MSE 预检不过/被否回就出护栏提示卡，不许拿 wasm 或 WebCodecs 解
 * 4K（解码帧进页面内存排队必爆内存崩页，#260/#261 两度实证）。
 * 软解只覆盖 HEVC/H.264 画面 + AAC/AC3/EAC3/DTS 声音；AV1/Opus 等
 * 不在 wasm 集里，照旧走解码提示卡，不许硬上。
 */
import {
  normalizeAudioCodec,
  normalizeVideoCodec,
  type DecodeSourceLike,
  type DecodeStreamLike,
  type DecodeVerdict,
} from './decodeSupport'

/** 软解覆盖的视频编码（归一化后） */
export const SOFT_VIDEO_CODECS = new Set(['hevc', 'h264'])
/** 软解覆盖的音频编码（归一化后；dca/dts-hd 统一归一成 dts） */
export const SOFT_AUDIO_CODECS = new Set(['aac', 'ac3', 'eac3', 'dts'])

/**
 * AVCodecID（FFmpeg/libmedia）→ 自托管解码器文件名。
 * h264=27 hevc=173 aac=86018 ac3=86019 dts=86020 eac3=86056。
 * 不在表里的编码（AV1/Opus 等）返回 undefined，AVPlayer 拿不到
 * wasm 就会报错——调用方先用 softDecodeCodecSubset 拦住，不许走到这。
 */
export const CODEC_WASM_FILE: Record<number, string> = {
  27: 'h264',
  173: 'hevc',
  86018: 'aac',
  86019: 'ac3',
  86020: 'dca',
  86056: 'eac3',
}

export function wasmPathForDecoder(codecId: number | undefined): string | undefined {
  if (codecId == null) return undefined
  const name = CODEC_WASM_FILE[codecId]
  return name ? `/wasm/decode/${name}-simd.wasm` : undefined
}

export interface CodecSubsetResult {
  supported: boolean
  videoCodec: string
  audioCodec: string
  reason: string
}

/** 这个版本的编解码是否全在软解子集里（视频必有，音频可无） */
export function softDecodeCodecSubset(source: DecodeSourceLike): CodecSubsetResult {
  const streams = source.MediaStreams || []
  const vs = streams.find((s) => s.Type === 'Video')
  const as = streams.find((s) => s.Type === 'Audio')
  const videoCodec = normalizeVideoCodec(vs?.Codec)
  const audioCodec = normalizeAudioCodec(as?.Codec)
  if (!vs || !videoCodec) {
    return { supported: false, videoCodec, audioCodec, reason: 'no-video-codec' }
  }
  if (!SOFT_VIDEO_CODECS.has(videoCodec)) {
    return { supported: false, videoCodec, audioCodec, reason: 'video-codec-out-of-subset' }
  }
  if (as && audioCodec && !SOFT_AUDIO_CODECS.has(audioCodec)) {
    return { supported: false, videoCodec, audioCodec, reason: 'audio-codec-out-of-subset' }
  }
  return { supported: true, videoCodec, audioCodec, reason: '' }
}

/**
 * 是否走软解：探测结论是「浏览器解不了」且坏在音/视频上，且编解码
 * 全在子集里，且开关开着。四者缺一就回 false，调用方按现行路径走
 * （硬拦的出解码提示卡，其余先试着播）。
 */
export function shouldSoftDecode(
  verdict: DecodeVerdict | undefined | null,
  source: DecodeSourceLike,
  enabled: boolean,
): boolean {
  if (!enabled || !verdict) return false
  if (verdict.status !== 'unsupported') return false
  if (!verdict.badVideo && !verdict.badAudio) return false
  return softDecodeCodecSubset(source).supported
}

// ---------- 软解分辨率护栏（4K 不许软解） ----------

/** 4K判定线：宽达此值或高达 SOFT_4K_MIN_HEIGHT 任一命中即按 4K 算 */
export const SOFT_4K_MIN_WIDTH = 3840
export const SOFT_4K_MIN_HEIGHT = 2160

/** 护栏命中时提示卡的文案（人话，指出路：客户端 App 自带解码） */
export const SOFT_4K_BLOCK_MESSAGE =
  '这部片子是 4K 画质，手机浏览器放不动，请用客户端 App 观看'

export interface SoftResolutionGateResult {
  blocked: boolean
  /** 所选源视频流的宽/高（缺失按 0），回带给调用方留痕 */
  width: number
  height: number
  reason: string
}

/**
 * 软解分辨率护栏（纯函数）：只有两个条件同时成立才拦——
 * ① 本次播放确实要走软解管线（softPipeline=true，调用方已过
 *    shouldSoftDecode 放行）；② 所选 MediaSource 视频流分辨率达 4K
 *    （Width≥3840 或 Height≥2160，任一维达线即算：覆盖 DCI 4K
 *    4096×2160 与竖屏 2160×3840）。
 *
 * 为什么拦：4K HEVC 纯 wasm 软件解码在 iOS Safari 上内存爆掉、
 * WebContent 进程被系统反复杀，页面循环崩溃（2026-11-17《多喜
 * 一家人》第 1 集 3840×2160 HEVC＋EAC3 事故：Safari 报「重复
 * 出现问题」、同窗 6 次 PlaybackInfo 重入）。命中时调用方不得
 * 实例化 AVPlayer，直接出提示卡（SOFT_4K_BLOCK_MESSAGE）。
 *
 * 口径：宽高只取 PlaybackInfo 实际所选 MediaSource 的视频流
 * Width/Height，不许用条目级字段凑；原生路径（softPipeline=
 * false）永不拦；宽高缺失按 0 处理、不拦——宁可放行走既有软解
 * 失败回落，也不误拦信息不全的源；非 4K 软解完全不受影响。
 */
export function softDecodeResolutionGate(
  source: DecodeSourceLike,
  softPipeline: boolean,
): SoftResolutionGateResult {
  const vs = (source.MediaStreams || []).find((s) => s.Type === 'Video')
  const width = vs?.Width || 0
  const height = vs?.Height || 0
  if (!softPipeline) return { blocked: false, width, height, reason: 'native-pipeline' }
  if (!vs) return { blocked: false, width, height, reason: 'no-video-stream' }
  if (width >= SOFT_4K_MIN_WIDTH || height >= SOFT_4K_MIN_HEIGHT) {
    return { blocked: true, width, height, reason: 'ax-soft-decode' }
  }
  return { blocked: false, width, height, reason: 'below-4k' }
}

// ---------- 4K MSE 原生管线（2026-11-17 晚，真机探针验通） ----------

/**
 * 4K MSE 关停开关（前端常量，默认开）。关停时 4K 完全回到 #260
 * 护栏行为（不启任何解码器、只出提示卡）。落点与本文件既有的
 * 前端侧口径常量同形态；改值需随前端发一次版。
 *
 * 来历：4K 崩页的根因是帧交付路径——wasm 软解（#260）与 WebCodecs
 * 硬解（#261）都把解码后的 4K 帧（25–33MB/帧）搬进页面内存排队，
 * 撞 iOS 单页约 1.5GB 上限被系统杀，与解码器是否硬解无关（调查
 * 报告《网页 4K 播放路线调查》2026-11-17）。MSE 原生管线把画面
 * 交给系统媒体管线播、帧不进页面内存，是友站的第一顺位；探针页
 * （#279）已在用户 iPhone 上实测一次成功：默认选轨（eac3 主轨）
 * useMSE=true、出画、播放头正常推进（生产库 probe_4k_results
 * 汇总 mse-ok-default）。WebCodecs 硬解尝试路径已整段退役删除，
 * 不许留作可选项——它已被真机证明会崩页。
 */
export const SOFTDECODE_MSE_ENABLED = true

export type SoftPipelineKind = 'wasm' | 'mse' | 'blocked'

export interface SoftPipelineDecisionResult {
  pipeline: SoftPipelineKind
  /** 所选源视频流的宽/高（缺失按 0），回带给调用方留痕 */
  width: number
  height: number
  reason: string
}

/**
 * 软解管线决策（纯函数）：在分辨率护栏之上分流——
 * - 未达 4K 线（或非软解管线）→ 'wasm'：现行 wasm 构造，行为不变；
 * - 达 4K 线 + 开关开 + MSE 预检通过 → 'mse'：以 checkUseMSE 改判
 *   构造 AVPlayer、走原生管线试播；
 * - 达 4K 线但开关关 / 预检不过 → 'blocked'：#260 提示卡。
 * 阈值直接复用 softDecodeResolutionGate，不另立一套判定。
 */
export function softDecodePipelineDecision(
  source: DecodeSourceLike,
  softPipeline: boolean,
  mse: { enabled: boolean; supported: boolean },
): SoftPipelineDecisionResult {
  const gate = softDecodeResolutionGate(source, softPipeline)
  if (!gate.blocked) {
    return { pipeline: 'wasm', width: gate.width, height: gate.height, reason: gate.reason }
  }
  if (!mse.enabled) {
    return { pipeline: 'blocked', width: gate.width, height: gate.height, reason: 'mse-switch-off' }
  }
  if (!mse.supported) {
    return { pipeline: 'blocked', width: gate.width, height: gate.height, reason: 'mse-unsupported' }
  }
  return { pipeline: 'mse', width: gate.width, height: gate.height, reason: 'ax-mse' }
}

// ---------- MSE 预检（移植探针页 #279 已真机验证的逻辑） ----------

export interface MseMimeCandidate {
  kind: 'video' | 'audio'
  codec: string
  mime: string
  /** 音频候选归属的流 Index；视频候选为 null */
  audioIndex: number | null
}

/**
 * HEVC 的 RFC 6381 参数对（本档领衔 + 另一档兜底）：按流的真实
 * Profile / BitDepth / Level 现拼。档次判读：Profile 含 Main10 或
 * BitDepth≥10 按 Main10（2.4）；两者都缺（老数据没探测字段）时 4K
 * HEVC 绝大多数是 Main10，仍按 Main10 领衔、Main 作兜底候选——
 * 闸门只要求任一带参数候选被认，领衔顺序不决定过不过。
 */
function hevcMimeParams(vs: DecodeStreamLike): { main: string; alt: string } {
  const profile = (vs.Profile || '').toLowerCase().replace(/\s+/g, '')
  const hasProfileInfo = !!profile || (typeof vs.BitDepth === 'number' && vs.BitDepth > 0)
  const isMain10 =
    profile.includes('main10') || (vs.BitDepth || 0) >= 10 || !hasProfileInfo
  const level =
    typeof vs.Level === 'number' && vs.Level > 0 ? Math.round(vs.Level) : 150
  const main10 = `2.4.L${level}.B0`
  const main = `1.6.L${level}.B0`
  return isMain10 ? { main: main10, alt: main } : { main, alt: main10 }
}

/**
 * 按流的真实参数拼 isTypeSupported 候选串（候选策略照探针页）：
 * 视频 HEVC 先本档 hvc1/hev1 双标签、再另一档兜底、最后裸标签；
 * H.264 用 High 档参数 + 裸标签。音频逐轨：eac3→ec-3、ac3→ac-3、
 * aac→mp4a.40.2/.40.5。逐串留痕不许合并——平台只认裸标签、
 * 不认带参数串的区别本身就是结论的一部分。
 */
export function mseMimeCandidates(source: DecodeSourceLike): MseMimeCandidate[] {
  const streams = source.MediaStreams || []
  const out: MseMimeCandidate[] = []
  const video = streams.find((s) => s.Type === 'Video')
  const vc = normalizeVideoCodec(video?.Codec)
  if (vc === 'hevc' && video) {
    const { main, alt } = hevcMimeParams(video)
    for (const param of [`hvc1.${main}`, `hev1.${main}`, `hvc1.${alt}`, 'hvc1']) {
      out.push({ kind: 'video', codec: 'hevc', mime: `video/mp4; codecs="${param}"`, audioIndex: null })
    }
  } else if (vc === 'h264') {
    for (const param of ['avc1.640028', 'avc1']) {
      out.push({ kind: 'video', codec: 'h264', mime: `video/mp4; codecs="${param}"`, audioIndex: null })
    }
  }
  for (const a of streams.filter((s) => s.Type === 'Audio')) {
    const ac = normalizeAudioCodec(a.Codec)
    const audioIndex = typeof a.Index === 'number' ? a.Index : null
    if (ac === 'eac3') {
      out.push({ kind: 'audio', codec: 'eac3', mime: 'audio/mp4; codecs="ec-3"', audioIndex })
    } else if (ac === 'ac3') {
      out.push({ kind: 'audio', codec: 'ac3', mime: 'audio/mp4; codecs="ac-3"', audioIndex })
    } else if (ac === 'aac') {
      out.push({ kind: 'audio', codec: 'aac', mime: 'audio/mp4; codecs="mp4a.40.2"', audioIndex })
      out.push({ kind: 'audio', codec: 'aac', mime: 'audio/mp4; codecs="mp4a.40.5"', audioIndex })
    }
  }
  return out
}

export interface SoftMsePreflightEntry extends MseMimeCandidate {
  /** true/false = 平台明确答复；null = 无 MSE API 或问询异常 */
  supported: boolean | null
}

export interface SoftMsePreflight {
  /** 实际用的 MSE 接口：iOS 17.1+ 是 ManagedMediaSource；注入问询函数（测试）时记 'injected' */
  mseApi: 'ManagedMediaSource' | 'MediaSource' | 'injected' | ''
  entries: SoftMsePreflightEntry[]
}

interface MseCtorLike {
  isTypeSupported(mime: string): boolean
}

/** 跑一遍 MSE 预检：候选串逐个问平台 isTypeSupported（同步 API）。第二个参数仅供测试注入；第三个参数供 MSE-seek 路由传入合成候选串（流信息为空时），不传则走真实参数。 */
export function runSoftMsePreflight(
  source: DecodeSourceLike,
  isTypeSupported?: (mime: string) => boolean | null,
  candidates?: MseMimeCandidate[],
): SoftMsePreflight {
  let mseApi: SoftMsePreflight['mseApi'] = ''
  let checker = isTypeSupported
  if (checker) {
    mseApi = 'injected'
  } else if (typeof window !== 'undefined') {
    const w = window as unknown as {
      ManagedMediaSource?: MseCtorLike
      MediaSource?: MseCtorLike
    }
    const mse = w.ManagedMediaSource || w.MediaSource
    mseApi = w.ManagedMediaSource
      ? 'ManagedMediaSource'
      : w.MediaSource
        ? 'MediaSource'
        : ''
    if (mse) {
      checker = (mime: string) => {
        try {
          return !!mse.isTypeSupported(mime)
        } catch {
          return null
        }
      }
    }
  }
  const entries: SoftMsePreflightEntry[] = (candidates ?? mseMimeCandidates(source)).map((c) => {
    let supported: boolean | null = null
    if (checker) {
      try {
        supported = checker(c.mime)
      } catch {
        supported = null
      }
    }
    return { ...c, supported }
  })
  return { mseApi, entries }
}

export type SoftMseGate = 'pass' | 'no-mse-api' | 'video-unsupported' | 'audio-unsupported'

/**
 * 构造播放器前的闸门（移植探针页 gateForAttempt 的默认选轨口径）：
 * 视频任一带参数候选被认（裸标签不算——libmedia 内部用的就是带
 * 参数串，只认裸标签它照样否）且默认音轨（第一条音轨，即正式
 * 播放走 libmedia 默认选轨时会选中的那条）的候选被认，才许构造。
 * 闸门不过就不构造播放器：libmedia 内部否掉 MSE 后会掉进解码
 * 管线，那正是 4K 崩页的路，正式路径同样不许去试。
 */
export function softMseGate(pre: SoftMsePreflight, source: DecodeSourceLike): SoftMseGate {
  if (!pre.mseApi) return 'no-mse-api'
  // 带参数串 = codec 参数里带 '.'（如 hvc1.2.4.L150.B0）；裸标签不算
  const parameterized = (mime: string) => (mime.split('"')[1] || '').includes('.')
  const videoOk = pre.entries.some(
    (e) => e.kind === 'video' && e.supported === true && parameterized(e.mime),
  )
  if (!videoOk) return 'video-unsupported'
  const audios = (source.MediaStreams || []).filter((s) => s.Type === 'Audio')
  if (!audios.length) return 'pass'
  const targetIndex = typeof audios[0].Index === 'number' ? audios[0].Index : null
  const audioOk = pre.entries.some(
    (e) => e.kind === 'audio' && e.audioIndex === targetIndex && e.supported === true,
  )
  return audioOk ? 'pass' : 'audio-unsupported'
}

/** 预检一站式（正式播放器起播前调用）：闸门结论 + 是否放行走 MSE。 */
export function probeSoftMseSupport(
  source: DecodeSourceLike,
  isTypeSupported?: (mime: string) => boolean | null,
): { supported: boolean; gate: SoftMseGate } {
  const gate = softMseGate(runSoftMsePreflight(source, isTypeSupported), source)
  return { supported: gate === 'pass', gate }
}

// ---------- MSE 精确取簇路由（2026-11-25 第十一轮） ----------

/**
 * MSE-seek 路由的候选串：
 * 有流信息时走真实参数（与 4K MSE 路同一套 mseMimeCandidates）；
 * 流信息为空（本站未探测到流，如《黑洞表面》）且容器是 mkv 时，
 * 按 HEVC 猜一组合成候选——只是问平台支不支持，内容真身由
 * AVPlayer load 时再认，认不出由 MSE 否决看门狗 + 原生回落接住。
 * 非 mkv 容器不合成：libmedia 的 mkv demuxer 是 Cues 精确取簇
 * 的载体，其它容器走原生。
 */
export function mseSeekCandidates(source: DecodeSourceLike): MseMimeCandidate[] {
  const streams = source.MediaStreams || []
  if (streams.length) return mseMimeCandidates(source)
  if ((source.Container || '').toLowerCase() !== 'mkv') return []
  const out: MseMimeCandidate[] = []
  for (const param of ['hvc1.1.6.L120.90', 'hev1.1.6.L120.90', 'hvc1.2.4.L150.B0', 'hvc1']) {
    out.push({ kind: 'video', codec: 'hevc', mime: `video/mp4; codecs="${param}"`, audioIndex: null })
  }
  for (const [codec, mime] of [
    ['ac3', 'audio/mp4; codecs="ac-3"'],
    ['eac3', 'audio/mp4; codecs="ec-3"'],
    ['aac', 'audio/mp4; codecs="mp4a.40.2"'],
  ] as const) {
    out.push({ kind: 'audio', codec, mime, audioIndex: null })
  }
  return out
}

/**
 * MSE-seek 路由的闸门：有流信息时复用软解闸门（默认音轨口径）；
 * 流信息为空时只要求视频有带参数 HEVC 候选被认、且至少一条常见
 * 音频候选被认——否则 MSE 管线连默认音轨都喂不进去。
 */
export function mseSeekGate(pre: SoftMsePreflight, source: DecodeSourceLike): SoftMseGate {
  const streams = source.MediaStreams || []
  if (streams.length) return softMseGate(pre, source)
  if (!pre.mseApi) return 'no-mse-api'
  const parameterized = (mime: string) => (mime.split('"')[1] || '').includes('.')
  const videoOk = pre.entries.some(
    (e) => e.kind === 'video' && e.supported === true && parameterized(e.mime),
  )
  if (!videoOk) return 'video-unsupported'
  const audioOk = pre.entries.some((e) => e.kind === 'audio' && e.supported === true)
  return audioOk ? 'pass' : 'audio-unsupported'
}

/** MSE-seek 预检一站式：候选串（真实或合成）+ 闸门结论。 */
export function probeMseSeekSupport(
  source: DecodeSourceLike,
  isTypeSupported?: (mime: string) => boolean | null,
): { supported: boolean; gate: SoftMseGate } {
  const gate = mseSeekGate(
    runSoftMsePreflight(source, isTypeSupported, mseSeekCandidates(source)),
    source,
  )
  return { supported: gate === 'pass', gate }
}

/**
 * MSE-seek 路由决策（纯函数）：只认 mkv 容器；有流信息时只对
 * HEVC 视频走新路（H.264 原生能跳，不必换管线）；流信息为空时
 * 按 HEVC 猜——AVPlayer load 时再认，认不出回落原生。
 * mseSupported 来自 probeMseSeekSupport 的放行结论。
 */
export function shouldMseSeekRoute(source: DecodeSourceLike, mseSupported: boolean): boolean {
  if (!mseSupported) return false
  if ((source.Container || '').toLowerCase() !== 'mkv') return false
  const streams = source.MediaStreams || []
  if (!streams.length) return true
  return normalizeVideoCodec(streams.find((s) => s.Type === 'Video')?.Codec) === 'hevc'
}

// ---------- 开关读取（公开端点，只读一个布尔值） ----------

let enabledPromise: Promise<boolean> | null = null

/** 读一次开关并缓存（页面级）：取不到/超时一律按关处理，不拖垮起播。 */
export function fetchSoftDecodeEnabled(): Promise<boolean> {
  if (enabledPromise) return enabledPromise
  enabledPromise = (async () => {
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 5000)
      try {
        const res = await fetch('/api/site/web-softdecode', { signal: controller.signal })
        if (!res.ok) return false
        const data = (await res.json()) as { enabled?: unknown }
        return data?.enabled === true
      } finally {
        clearTimeout(timer)
      }
    } catch {
      return false
    }
  })()
  return enabledPromise
}

/** 仅测试用：清掉开关缓存，让下一轮重新读。 */
export function __resetSoftDecodeEnabledCache(): void {
  enabledPromise = null
}

// ---------- 软解取流地址 ----------

/**
 * 直链变体 → 中继变体（与 backend/emby_server/play_variants.py 的变体
 * 枚举一一对应）。直链变体（drive_direct 等）的取流是 302 跳网盘直链
 * （跨源、无 CORS 头），AVPlayer 的 IO 在 Worker 里用 fetch 取流，
 * 永远读不到：2026-11-15 生产实证——load() 在 IO 层反复重试约
 * 20 秒后才失败、期间零 wasm 请求，最终被 45 秒守卫吞掉回落。
 * 中继变体是同一个流端点改由服务器出字节，fetch 可读；软解只换
 * 自己用的这一条地址，原生 <video> 路径（能跟 302）逐字不变。
 */
export const DIRECT_TO_RELAY_VARIANT: Record<string, string> = {
  drive_direct: 'drive_relay',
  byos_drive_direct: 'byos_drive_relay',
  byos115_direct: 'mount_relay',
}

/** 无变体标记时探测中继变体的顺序：本人盘中继是灰度直链的设计兜底，先探它；转存副本中继次之（挂载原件+本人副本条目只有它能出字节） */
const RELAY_PROBE_ORDER = ['byos_drive_relay', 'usercopy_relay', 'drive_relay']

/**
 * 从播放地址里读变体名：variant 参数优先，其次 MediaSourceId 的
 * {guid}:{variant} 后缀——与服务端 parse_requested_variant 同口径。
 * 读不到返回空串。
 */
export function variantOfStreamUrl(rawUrl: string): string {
  try {
    const u = new URL(rawUrl, 'http://softdecode.invalid/')
    const v = (u.searchParams.get('variant') || '').trim()
    if (v) return v
    const msid = u.searchParams.get('MediaSourceId') || ''
    if (msid.includes(':')) return msid.slice(msid.lastIndexOf(':') + 1).trim()
  } catch {
    /* 地址解析不了就没有变体 */
  }
  return ''
}

/** 给地址打上变体参数（与服务端 variantize_url 同形态；MediaSourceId 后缀由调用方按需同步） */
function withVariantParam(url: string, variant: string): string {
  const u = new URL(url)
  u.searchParams.set('variant', variant)
  return u.toString()
}

/**
 * 地址若标的是直链变体，换成对应中继变体（variant 参数与
 * MediaSourceId 后缀双保险位一起换，与服务端 variantize_url 的
 * 打标形态一致；验签不看这两个参数）。不是直链变体时原样返回。
 */
export function relayVariantUrl(rawUrl: string): string {
  const variant = variantOfStreamUrl(rawUrl)
  const relay = DIRECT_TO_RELAY_VARIANT[variant]
  if (!relay) return rawUrl
  try {
    const u = new URL(rawUrl)
    u.searchParams.set('variant', relay)
    const msid = u.searchParams.get('MediaSourceId') || ''
    if (msid.endsWith(`:${variant}`)) {
      u.searchParams.set(
        'MediaSourceId',
        `${msid.slice(0, msid.length - variant.length)}${relay}`,
      )
    }
    return u.toString()
  } catch {
    return rawUrl
  }
}

export type SoftUrlProbeResult = 'bytes' | 'redirect' | 'fail'
export type SoftUrlProbe = (
  url: string,
  redirect: 'manual' | 'follow',
) => Promise<SoftUrlProbeResult>

/** 默认探测：1 字节 Range 读，只看「出字节 / 会重定向 / 其余失败」，不读完 body */
async function fetchProbe(
  url: string,
  redirect: 'manual' | 'follow',
): Promise<SoftUrlProbeResult> {
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 6000)
    try {
      const res = await fetch(url, {
        redirect,
        headers: { Range: 'bytes=0-0' },
        signal: controller.signal,
      })
      const result: SoftUrlProbeResult =
        (res as { type?: string }).type === 'opaqueredirect'
          ? 'redirect'
          : res.status === 200 || res.status === 206
            ? 'bytes'
            : 'fail'
      // 探测只看响应头，结论一出立刻掐断正文：被探的地址若回 200
      // 整流（上游偶发不认 Range），不掐断就是一份整文件下载在后台
      // 继续跑——2026-11-16 生产实证：一发探测在后台漏下 90MB+，
      // 与播放器自己的取流抢同一条中继和带宽。掐断失败不影响结论。
      try {
        await res.body?.cancel()
      } catch {
        /* 正文掐断失败不掩盖探测结论 */
      }
      controller.abort()
      return result
    } finally {
      clearTimeout(timer)
    }
  } catch {
    return 'fail'
  }
}

/**
 * 算软解真正要喂给 AVPlayer 的地址：
 * 1. 相对地址先绝对化——AVPlayer 的 IO 在 Worker 里 fetch，
 *    相对地址解析不了（复现台实证：Failed to parse URL）。
 * 2. 地址带直链变体标记 → 直接换中继变体（确定性，不探测）。
 * 3. 无标记（生产默认口径：PlaybackInfo 单源下发、取流分支由服务端
 *    开关现场决定）→ 探原地址：出字节就用原地址；会 302 才逐个探
 *    中继变体，谁出字节用谁；都探不通回原地址，让播放器把真实
 *    失败抛出来（不再静默）。探测有任何不确定都回原地址，
 *    绝不比现状更差。
 */
export async function resolveSoftDecodeUrl(
  rawUrl: string,
  deps: { probe?: SoftUrlProbe; base?: string } = {},
): Promise<string> {
  if (!rawUrl) return rawUrl
  const probe = deps.probe ?? fetchProbe
  let url = rawUrl
  try {
    const base =
      deps.base ?? (typeof location !== 'undefined' ? location.href : undefined)
    if (base) url = new URL(rawUrl, base).toString()
  } catch {
    /* 绝对化失败就用原串 */
  }
  if (variantOfStreamUrl(url)) return relayVariantUrl(url)
  const first = await probe(url, 'manual')
  if (first !== 'redirect') return url
  for (const relay of RELAY_PROBE_ORDER) {
    const candidate = withVariantParam(url, relay)
    if ((await probe(candidate, 'follow')) === 'bytes') return candidate
  }
  return url
}

/** 软解能直接取字节的中继变体（优先级：本人转存副本 > 本人盘 > 共享 Drive 中继 > 挂载中继） */
const SOFT_RELAY_VARIANT_ORDER = [
  'usercopy_relay',
  'byos_drive_relay',
  'drive_relay',
  'mount_relay',
] as const

/**
 * 从 PlaybackInfo 的 MediaSources 里为软解挑可取流的中继源 URL。
 *
 * #232 的确定性换路只改选中源 URL 上的变体标记（drive_direct 机械
 * 换 drive_relay）——但「挂载原件 + 本人 Drive 转存副本」这类条目，
 * drive_direct 实际 302 到副本的无 CORS 直链、drive_relay 又被
 * 生产开关关掉不在清单里，换过去只会 409（2026-11-16 生产实证）。
 * 清单里真能出字节的是同条目的中继兄弟源，必须按源挑：选中源
 * 本身已是中继变体 → 用它自己；否则按优先级找兄弟中继源。
 * 找不到返回 ''，调用方回落既有 resolveSoftDecodeUrl 流程。
 */
export function pickSoftRelayUrl(
  sources:
    | Array<{ Id?: string; DirectStreamUrl?: string }>
    | undefined
    | null,
  selectedId: string | undefined | null,
): string {
  if (!sources?.length || !selectedId) return ''
  const variantOfSource = (s: { Id?: string; DirectStreamUrl?: string }) => {
    const id = String(s?.Id || '')
    const idx = id.indexOf(':')
    if (idx > 0 && idx < id.length - 1) return id.slice(idx + 1)
    return s?.DirectStreamUrl ? variantOfStreamUrl(s.DirectStreamUrl) : ''
  }
  const selected = sources.find((s) => s?.Id === selectedId)
  if (!selected) return ''
  if (
    (SOFT_RELAY_VARIANT_ORDER as readonly string[]).includes(
      variantOfSource(selected),
    )
  ) {
    return String(selected.DirectStreamUrl || '')
  }
  for (const name of SOFT_RELAY_VARIANT_ORDER) {
    const hit = sources.find((s) => variantOfSource(s) === name)
    if (hit?.DirectStreamUrl) return String(hit.DirectStreamUrl)
  }
  return ''
}

/** 把软解的原始失败串翻成一句人话（回落提示卡小字用；原始串仍走 console.error 留痕） */
export function humanizeSoftError(message: string): string {
  const raw = (message || '').trim()
  if (!raw) return '未知错误'
  if (/timeout/i.test(raw)) return '启动超时，网络或片源太慢'
  if (/open stream failed|failed to fetch|fetch|network/i.test(raw)) {
    return '视频流打不开，没能从服务器取到数据'
  }
  if (/wasm|webassembly/i.test(raw)) return '解码器组件加载失败'
  if (/mse/i.test(raw)) return '浏览器没有接住原生播放管线'
  if (/decode|decoder|codec/i.test(raw)) return '解码器启动失败'
  // libmedia 的错误串带 [packages/...][line N] 前缀与 taskId，去掉再截断
  const cleaned = raw
    .replace(/\[[^\]]*\]/g, '')
    .replace(/taskId:\s*\S+/i, '')
    .replace(/\s+/g, ' ')
    .replace(/^[\s:：\-—]+/, '')
    .trim()
  return (cleaned || '未知错误').slice(0, 60)
}

// ---------- seek 看门狗（2026-11-25 生产实测事故） ----------

/**
 * 软解 seek 单次看门狗时长（毫秒）。冷 seek 要经中继对大文件做远
 * 距离 Range 取流：服务端 rcd 开流实测可达数秒，demux 定位关键帧
 * 再叠 wasm 解码重启，15 秒是「慢但还活着」的上界；超时先重试
 * 一次（见 SOFT_SEEK_MAX_RETRIES），仍无结果才判失败。
 */
export const SOFT_SEEK_TIMEOUT_MS = 15000

/** seek 超时后自动重试的次数（重试 = 同一目标再下发一次 seek） */
export const SOFT_SEEK_MAX_RETRIES = 1

/** seek 到达容差（秒）：关键帧落点可略早于目标，读数到目标前此值以内算到达 */
export const SOFT_SEEK_ARRIVE_TOLERANCE_S = 2

/**
 * seek 到达后确认恢复的窗口（毫秒）与最少推进量（秒）。2026-11-25
 * 第二轮：复测里 seek 把定位器推到了目标 56:59、时间却永远停在
 * 那里——旧看门狗只看「位置到了没有」，轮询第一次就判到达撤防，
 * 「正在定位」一闪而过、重试与错误页永不触发。到达只是「定位器
 * 就位」，不是「播起来了」：转入确认阶段后，播放态下时间必须在
 * 窗口内再前进至少这么多秒才算真正恢复；否则按卡住走重试→判失败。
 * 暂停态不适用（时间本来就不走，见 SeekProgressTracker）。
 * 取值：冷段开流 + 解码重启实测约 2–3 秒，5 秒窗口留有余量；
 * 0.5 秒推进量约等于一次 time 事件步进的两倍，防单次读数抖动误判。
 */
export const SOFT_SEEK_RESUME_MS = 5000
export const SOFT_SEEK_RESUME_ADVANCE_S = 0.5

/**
 * 确认恢复要求时钟「持续前进」的跨度（毫秒），缺省 1000。
 * 第四轮（2026-11-25 复测，三次同症）：seek 落点后解码器先播出
 * 已缓冲的头几帧、时钟 tick 约 0.5 秒，随后因下一段是冷段而停
 * 住；旧规则「前进 ≥0.5 秒即落定」把这一个 tick 当成恢复，几秒
 * 内撤防、提示一闪而过，之后 2 分钟静默冻结没人管。单次 tick 不
 * 算恢复：时钟必须在一段跨度里一直在前进（见 SeekProgressTracker），
 * 即前进被至少两次相隔此跨度的采样同时看到，才落定。取值：正常
 * 播放 1 秒走 1 秒，1 秒跨度只让健康 seek 多亮约 1 秒提示；缓冲
 * 头再长也只是一次 tick 的量级，不会被误认成持续播放。
 */
export const SOFT_SEEK_RESUME_SUSTAIN_MS = 1000

/**
 * 进度读数是否已到达 seek 目标（elapsedSeconds = 距本次 seek 下发
 * 的秒数）：读数落在 [目标-容差, 目标+容差+elapsed] 窗口内才算。
 * 下界防「还停在旧位置」误判（卡死形态）；上界防回退 seek 的误
 * 判——回退时 seek 落定前的读数还停在旧位置（远在目标之后），
 * 不设上界会被当成「已越过目标」把看门狗提前撤掉，seek 真卡住
 * 时就又没人管了。落定后播放头以 1 倍速前进，窗口随 elapsed
 * 同步放宽，不会把正常播放误判成未到达。读数无效（NaN）不算。
 */
export function softSeekArrived(
  targetSeconds: number,
  currentSeconds: number,
  elapsedSeconds = 0,
): boolean {
  if (!Number.isFinite(currentSeconds)) return false
  if (currentSeconds < targetSeconds - SOFT_SEEK_ARRIVE_TOLERANCE_S) return false
  return (
    currentSeconds <=
    targetSeconds + SOFT_SEEK_ARRIVE_TOLERANCE_S + Math.max(0, elapsedSeconds)
  )
}

export type SoftSeekPhase = 'seeking' | 'confirming'

export interface SoftSeekGuardDeps {
  /** 单次看门狗时长（毫秒） */
  timeoutMs: number
  /** 到达后确认恢复的窗口（毫秒），缺省 SOFT_SEEK_RESUME_MS */
  confirmTimeoutMs?: number
  /** 超时/拒绝后自动重试次数，缺省 SOFT_SEEK_MAX_RETRIES */
  maxRetries?: number
  /** 该重试了：调用方把同一目标的 seek 再下发一次 */
  onRetry: (attempt: number) => void
  /** 重试用尽仍无结果：判失败，只调一次；phase 说明死在哪一阶段 */
  onTimeout: (phase: SoftSeekPhase) => void
  /** 计时器注入（测试用），缺省走全局 setTimeout/clearTimeout */
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
}

/**
 * seek 看门狗状态机（纯逻辑，可单测）：管「一次 seek 从下发到落
 * 定」的生死，不碰播放器本身。
 *
 * 来历（2026-11-25 实测）：《黑洞表面》1080p mkv 软解拖到 52%
 * 后永久冻结——旧接线把 seek 打出去就不管了：视图层
 * .catch(()=>{}) 吞掉 promise 拒绝、包装层没订阅 libmedia 的
 * seeked 事件、没有任何超时。AVPlayer 内部 seek 一旦卡住（demux
 * 定位要经中继对 2.4GB 文件做远距离 Range 取流），'time' 事件
 * 不再来、error 也不来，UI 就永远停在「播放态＋进度冻结＋无提
 * 示」。看门狗把这条路堵死：begin 武装 → seeked/进度到达
 * complete 撤防；计时到期或底层 promise 拒绝（fail）→ 先
 * onRetry 重发一次，再无结果才 onTimeout 判失败（只报一次）。
 * 新 seek 顶替在飞的 seek 时重试计数清零。
 *
 * 两阶段（2026-11-25 第二轮）：'seeking'（找目标，计时 timeoutMs）
 * → arrive() → 'confirming'（已到目标，确认时间真的往前走，计时
 * confirmTimeoutMs）。只看位置会把「到了目标但播不起来」误判成
 * 成功——复测里时间永远停在目标值，旧代码半秒内就撤防了。确认
 * 阶段里时间不推进同样走重试→判失败；暂停态由调用方按
 * SeekProgressTracker.pausedSettled 判定后 complete（时间本来就不走），未落定的短暂
 * 暂停先 arrive() 进确认阶段等着，不直接落定（第三轮）。
 *
 * 第四轮（2026-11-25 复测，三次同症）：确认阶段「前进 ≥0.5 秒
 * 即落定」太弱——seek 落点后缓冲头先让时钟 tick 约 0.5 秒，随后
 * 冷段断粮停住，看门狗把这一个 tick 当恢复、几秒内撤防。改成
 * 「持续前进」（见 SeekProgressTracker）：前进必须被两次相隔
 * SOFT_SEEK_RESUME_SUSTAIN_MS 的采样同时看到才落定。另：每次变
 * 迁（begin/arrive/complete/escalate）都打 console 日志，下一轮
 * 复测可凭控制台日志定案看门狗走到了哪一步，不再盲修。
 */
export class SoftSeekGuard {
  private deps: SoftSeekGuardDeps
  private timer: unknown = null
  private targetSeconds: number | null = null
  private phase: SoftSeekPhase = 'seeking'
  private retriesUsed = 0
  private closed = false

  constructor(deps: SoftSeekGuardDeps) {
    this.deps = deps
  }

  /** 是否有 seek 在飞 */
  get pending(): boolean {
    return this.targetSeconds !== null
  }

  /** 是否在确认恢复阶段（已到目标，等时间往前走） */
  get confirming(): boolean {
    return this.targetSeconds !== null && this.phase === 'confirming'
  }

  /** 在飞 seek 的目标秒（无在飞时为 null） */
  get target(): number | null {
    return this.targetSeconds
  }

  /** 一次 seek 下发时调用：武装计时，重试计数清零（顶替旧 seek） */
  begin(targetSeconds: number): void {
    this.targetSeconds = targetSeconds
    this.phase = 'seeking'
    this.retriesUsed = 0
    this.closed = false
    this.arm()
    console.info('[softDecode] seek guard begin: target', targetSeconds, 's')
  }

  /**
   * 位置已到目标：转入确认恢复阶段，改按确认窗口重新武装。
   * 未在飞 / 已在确认阶段时是空操作（轮询与 seeked 事件可能重复调）。
   * 落定的暂停调用方不应调它——直接 complete；未落定的短暂暂停
   * 调用方调它进确认阶段等着（见 SeekProgressTracker.pausedSettled）。
   */
  arrive(): void {
    if (this.targetSeconds === null || this.closed) return
    if (this.phase === 'confirming') return
    this.phase = 'confirming'
    this.arm()
    console.info('[softDecode] seek guard arrive: target', this.targetSeconds, 's')
  }

  /** seek 已落定（确认恢复阶段里时间真的往前走了）：撤防 */
  complete(): void {
    if (this.targetSeconds === null) return
    const target = this.targetSeconds
    this.targetSeconds = null
    this.phase = 'seeking'
    this.disarm()
    console.info('[softDecode] seek guard complete: target', target, 's')
  }

  /** 底层 seek promise 拒绝：不等计时到期，立刻走重试/判失败 */
  fail(): void {
    if (this.targetSeconds === null || this.closed) return
    this.disarm()
    this.escalate()
  }

  /** 实例销毁：撤防且此后不再有任何回调 */
  dispose(): void {
    this.targetSeconds = null
    this.phase = 'seeking'
    this.closed = true
    this.disarm()
  }

  private arm(): void {
    this.disarm()
    const setTimer =
      this.deps.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
    // 找目标用 seek 超时，确认恢复用确认窗口：两阶段各有各的表。
    const ms =
      this.phase === 'confirming'
        ? (this.deps.confirmTimeoutMs ?? SOFT_SEEK_RESUME_MS)
        : this.deps.timeoutMs
    this.timer = setTimer(() => {
      this.timer = null
      if (this.targetSeconds === null || this.closed) return
      this.escalate()
    }, ms)
  }

  private disarm(): void {
    if (this.timer === null) return
    const clearTimer =
      this.deps.clearTimer ??
      ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>))
    clearTimer(this.timer)
    this.timer = null
  }

  private escalate(): void {
    const maxRetries = this.deps.maxRetries ?? SOFT_SEEK_MAX_RETRIES
    if (this.retriesUsed < maxRetries) {
      this.retriesUsed += 1
      this.deps.onRetry(this.retriesUsed)
      // onRetry 里调用方可能已 complete/dispose（同步判死）：仍在飞才重新武装
      if (this.targetSeconds !== null && !this.closed) {
        // 重试是重新下发一次 seek：回到「找目标」阶段，按 seek 超时重武装。
        this.phase = 'seeking'
        this.arm()
      }
      console.info(
        '[softDecode] seek guard retry',
        this.retriesUsed,
        'target',
        this.targetSeconds,
        's',
      )
    } else {
      const phase = this.phase
      const target = this.targetSeconds
      this.targetSeconds = null
      this.phase = 'seeking'
      this.closed = true
      console.info(
        '[softDecode] seek guard timeout: phase',
        phase,
        'target',
        target,
        's',
      )
      this.deps.onTimeout(phase)
    }
  }
}

// ---------- seek 到达＋确认恢复记账（软/原生共用，第五轮抽出） ----------

/** 一次进度采样（驱动层喂给 tracker 的原料） */
export interface SeekProgressSample {
  /** 本次采样看到的播放头秒数 */
  seconds: number
  /** 播放器当前是否处于播放态（暂停态下时间不走是正常的） */
  playing: boolean
  /** 最近一次暂停开始的时刻（毫秒，与 note 的 now 同一时钟）；正在播/从未暂停为 null */
  pausedAt: number | null
}

export type SeekProgressVerdict =
  | { kind: 'arrive' }
  | {
      kind: 'complete'
      reason: 'sustained' | 'paused'
      /** 本轮确认里时钟累计前进的秒数（sustained 时有意义） */
      advancedSeconds: number
      /** 判定发生在确认阶段（false = 找目标阶段到达即落定） */
      fromConfirming: boolean
    }

/**
 * seek 到达＋确认恢复的记账（纯逻辑，可单测）：原先是
 * createSoftPlayer 闭包里的局部逻辑（noteSeekProgress），第五轮
 * 抽成共享 helper——原生 <video> 路径的看门狗驱动（utils/
 * nativeSeek.ts）与软解共用同一套到达判定（softSeekArrived）与
 * 「持续前进」确认口径，不许复制一份。行为与抽出前逐字一致，
 * 现有 softSeekResume 测试钉住。
 *
 * 用法：seek 下发时 begin(目标) + issued()（每次实际下发含重试
 * 都刷新 issued）；进度采样（事件/轮询）喂 note(采样, 是否确认
 * 阶段)，按返回判定调 SoftSeekGuard 的 arrive()/complete()。
 */
export class SeekProgressTracker {
  private readonly opts: { resumeMs?: number; sustainMs?: number }
  private targetSeconds = 0
  private issuedAt = 0
  private arrivedAt = 0
  private hasArrived = false
  private confirmFirstMoveAt: number | null = null
  private confirmFirstMoveSeconds = 0
  private confirmLastSeenSeconds = 0

  constructor(opts: { resumeMs?: number; sustainMs?: number } = {}) {
    this.opts = opts
  }

  /** 新 seek 下发（用户/调用方发起）：记目标。确认记账在到达时清零（与旧口径一致） */
  begin(targetSeconds: number): void {
    this.targetSeconds = Math.max(0, targetSeconds)
    this.hasArrived = false
  }

  /** 每次实际下发（含看门狗重试）刷新下发时刻：到达判定窗口上界与暂停落定判定都按它算 */
  issued(now: number = Date.now()): void {
    this.issuedAt = now
  }

  /**
   * 失败后观察的起手式（第九轮 late-recovery）：把确认记账的基线
   * 钉在判失败时刻的真实位置上，之后 note(..., confirming=true)
   * 按同一套「持续前进」口径判定（累计 ≥0.5s、两次相隔 sustainMs
   * 的采样都看到、第二次读数大于第一次）。不另写一套确认逻辑。
   * issuedAt 同样钉在失败时刻，暂停落定判定才有意义。
   */
  beginLateRecovery(baselineSeconds: number, now: number = Date.now()): void {
    const base = Number.isFinite(baselineSeconds) ? baselineSeconds : 0
    this.arrivedAt = base
    this.hasArrived = true
    this.issuedAt = now
    this.confirmFirstMoveAt = null
    this.confirmFirstMoveSeconds = base
    this.confirmLastSeenSeconds = base
  }

  /** 在飞 seek 的目标秒 */
  get target(): number {
    return this.targetSeconds
  }

  /** 确认阶段已前进量（秒）：最近一次看到前进的读数 − 到达读数；未到达为 0。只供状态串显示，不参与判定 */
  get advancedSeconds(): number {
    if (!this.hasArrived) return 0
    return Math.max(0, this.confirmLastSeenSeconds - this.arrivedAt)
  }

  /**
   * 当前暂停是否算「落定」：只有两种暂停可以让看门狗撤防——
   * ① 暂停开始于本次 seek 下发之前（用户本来就停着，seek 是暂停
   *    态下发的；重试会刷新下发时刻，所以暂停发生在 seek 下发之
   *    后时，最多因此多发一次同目标重 seek，随后即落定）；
   * ② 暂停已持续满确认窗口（仍无人恢复：要么用户真停住了，要么
   *    解码器停顿超过窗口——后者与用户暂停已无法区分，按暂停
   *    处理）。
   * seek 后才开始的短暂暂停两种都不满足：不等它，也不撤防，进
   * 确认阶段继续盯着。复核窗口直接复用确认窗口，不再新开可调
   * 参数（单测里确认窗口取小值，复核窗口同步取小值）。
   */
  pausedSettled(sample: SeekProgressSample, now: number = Date.now()): boolean {
    if (sample.playing || sample.pausedAt === null) return false
    if (sample.pausedAt <= this.issuedAt) return true
    return now - sample.pausedAt >= (this.opts.resumeMs ?? SOFT_SEEK_RESUME_MS)
  }

  /**
   * 喂一次进度采样，返回判定（null = 还没结论，继续盯）：
   * - confirming=false（找目标）：读数到达目标 → { kind:'arrive' }
   *   （到达记账已清零，调用方随后调 guard.arrive()）；到达且暂停
   *   已落定 → complete/paused；
   * - confirming=true（确认恢复）：暂停已落定 → complete/paused；
   *   时钟持续前进达标 → complete/sustained（口径见
   *   SOFT_SEEK_RESUME_SUSTAIN_MS：单次 tick 不算，前进须被两次
   *   相隔 sustainMs 的采样同时看到、且累计 ≥
   *   SOFT_SEEK_RESUME_ADVANCE_S）。
   */
  note(
    sample: SeekProgressSample,
    confirming: boolean,
    now: number = Date.now(),
  ): SeekProgressVerdict | null {
    const { seconds } = sample
    if (confirming) {
      // 确认恢复阶段：落定的暂停（见 pausedSettled）时间不走是
      // 正常的，直接算落定；还没落定的短暂暂停先等着，不撤防。
      if (!sample.playing && this.pausedSettled(sample, now)) {
        return {
          kind: 'complete',
          reason: 'paused',
          advancedSeconds: this.advancedSeconds,
          fromConfirming: true,
        }
      }
      // 持续前进（第四轮）：只认「时钟在一段跨度里一直在走」。单
      // 次 tick（缓冲头播几帧就断粮）不落定：前进必须被两次相隔
      // sustainMs 的采样同时看到，且第二次的读数大于第一次（时
      // 钟还在走，不只是时间过去了），累计前进 ≥0.5 秒，才算真
      // 的恢复。
      if (Number.isFinite(seconds) && seconds > this.confirmLastSeenSeconds) {
        if (this.confirmFirstMoveAt === null) {
          this.confirmFirstMoveAt = now
          this.confirmFirstMoveSeconds = seconds
        }
        this.confirmLastSeenSeconds = seconds
      }
      if (
        this.confirmFirstMoveAt !== null &&
        Number.isFinite(seconds) &&
        seconds > this.confirmFirstMoveSeconds &&
        now - this.confirmFirstMoveAt >=
          (this.opts.sustainMs ?? SOFT_SEEK_RESUME_SUSTAIN_MS) &&
        seconds >= this.arrivedAt + SOFT_SEEK_RESUME_ADVANCE_S
      ) {
        return {
          kind: 'complete',
          reason: 'sustained',
          advancedSeconds: seconds - this.arrivedAt,
          fromConfirming: true,
        }
      }
      return null
    }
    const elapsed = this.issuedAt ? (now - this.issuedAt) / 1000 : 0
    if (!softSeekArrived(this.targetSeconds, seconds, elapsed)) return null
    // 位置已到目标：落定的暂停直接落定（时间本来就不走）；还没
    // 落定的短暂暂停先按到达进确认阶段等着；播放态转入确认恢复
    // 阶段，「正在定位」继续留着，直到时间真的往前走。
    if (!sample.playing && this.pausedSettled(sample, now)) {
      return {
        kind: 'complete',
        reason: 'paused',
        advancedSeconds: 0,
        fromConfirming: false,
      }
    }
    this.arrivedAt = seconds
    this.hasArrived = true
    // 新一轮确认的记账清零：上一轮的前进不算进这一轮。
    this.confirmFirstMoveAt = null
    this.confirmFirstMoveSeconds = seconds
    this.confirmLastSeenSeconds = seconds
    return { kind: 'arrive' }
  }
}

/** 看门狗状态变迁名（DOM 可观测状态串用，软/原生共用同一格式） */
export type SeekGuardTransition = 'seeking' | 'confirming' | 'complete' | 'failed'

/**
 * 看门狗状态串（写到播放器根元素 data-seek-guard 上，复测不打开
 * 控制台、直接读 DOM 即可定案看门狗走到了哪一步）：
 * `<变迁> target=<目标>s advanced=<已前进>s retries=<已重试数>`，
 * 秒数保留两位小数。无 seek 发生过时调用方用字面量 'idle'。
 */
export function seekGuardStateString(
  transition: SeekGuardTransition,
  targetSeconds: number,
  advancedSeconds: number,
  retriesUsed: number,
): string {
  return `${transition} target=${targetSeconds.toFixed(2)}s advanced=${advancedSeconds.toFixed(2)}s retries=${retriesUsed}`
}

// ---------- AVPlayer 包装 ----------

/**
 * AVPlayer 原始构建的站内托管地址（版本目录防缓存串版）。
 * 只能按 URL 加载：包导入经 rollup 重新打包后，其内部 cheap
 * Worker 启动代码会因标识符改名报 `W is not defined`，load()
 * 永远不返回（2026-11-15 实证，tests/softDecode.test.mjs 有
 * 结构断言钉住这两条）。
 */
export const SOFT_PLAYER_SCRIPT_URL = '/libmedia/1.3.1/avplayer.js'

export interface SoftPlayerHooks {
  onFirstVideo: () => void
  onFirstAudio: () => void
  onPlaying: () => void
  onPaused: () => void
  /** ms → 秒，播放进度上报与进度条共用 */
  onTime: (seconds: number) => void
  onEnded: () => void
  /** 任何阶段的软解失败：只回调一次，调用方负责回落提示卡，不重试 */
  onError: (message: string) => void
  /** seek 状态变化：true = 有 seek 在飞（UI 显示「正在定位」），false = 已落定或已判失败 */
  onSeekingChange?: (seeking: boolean) => void
  /**
   * seek 看门狗判失败（自动重试后仍无结果）：与 onError 分开——
   * 这不是解码失败，调用方给「跳转没能继续」的可见报错与重开
   * 出路，不许回落「浏览器解不了」的解码提示卡混淆原因。
   */
  onSeekFailed?: (message: string) => void
  /**
   * 起播后音频仍被浏览器拦住（AudioContext suspended/interrupted，
   * iOS 无手势起播的常态）：调用方给用户可见提示（如「点一下画面
   * 恢复声音」），点按手势里再调 handle.unlockAudio() 解锁。
   */
  onAudioBlocked?: () => void
  /**
   * seek 看门狗状态变迁上报（可选）：begin/arrive/complete/retry/
   * timeout 时以状态串回调（格式见 seekGuardStateString），调用
   * 方写进播放器根元素的 data-seek-guard 属性——复测不打开控制
   * 台、直接读 DOM 即可定案看门狗走到了哪一步。
   */
  onGuardState?: (state: string) => void
}

export interface SoftPlayerHandle {
  play(): Promise<void>
  pause(): Promise<void>
  seek(seconds: number): Promise<void>
  setRate(rate: number): void
  currentTimeSeconds(): number
  durationSeconds(): number
  hasVideo(): boolean
  hasAudio(): boolean
  /**
   * 播放器真实已接收字节（libmedia getStats().bufferReceiveBytes，
   * IO 层每读一段就累加的实读字节）；统计不可用时返回 null。
   * 只许拿它显示「已接收 X.XMB」，不许拿它推算其他量。
   */
  receivedBytes(): number | null
  /**
   * 软解字幕选轨（主/副槽位，Emby 流 Index；null=该槽关闭）：实际
   * 渲染轨 = 主 ?? 副（AVPlayer 同时只渲染一条），都关则停渲染。
   */
  setSubtitleSelection(primary: number | null, secondary: number | null): Promise<void>
  /** 软解字幕延时（毫秒，正值=字幕延后，与原生 shiftVtt 同号） */
  setSubtitleDelayMs(ms: number): void
  /**
   * 音频解锁：在用户手势里调用——让 libmedia 的 AudioContext
   * 建好并 resume，返回解锁后的上下文状态（见 unlockAudioContextOf）。
   */
  unlockAudio(): Promise<string | null>
  /** 当前 AudioContext 状态（running/suspended/…；未建时 null） */
  audioContextState(): string | null
  destroy(): Promise<void>
}

/**
 * 软解起播守卫默认时长（毫秒）。2026-11-16 生产实测校准：一部
 * 4.4GB MKV（Drive 转存副本中继）的冷起播全程——IO 打开、demux
 * 分析多 GB 文件、播放器懒加载分片、h264+ac3+resample+stretchpitch
 * 四件 wasm 下载——走完约 45 秒，旧的 45 秒守卫恰好在解码器刚
 * 加载完、首帧未出时把播放器销毁（失败文案「启动超时」）。软解是
 * 最后一道兜底，守卫按慢源冷起播的真实量级放宽到 90 秒；真死的源
 * 仍由 IO 层自己的错误先行暴露，不靠守卫兜底判死。
 */
export const SOFT_START_TIMEOUT_MS = 90000

/** 软解外挂字幕轨：与原生 <track> 同源的文本字幕（DeliveryUrl） */
export interface SoftSubtitleTrack {
  /** Emby 字幕流 Index（视图选轨口径） */
  index: number
  /** 字幕文件 URL（服务端 DeliveryUrl，通常以 Stream.vtt 结尾） */
  url: string
  label?: string
  language?: string
}

/** AVPlayer loadExternalSubtitle 的载荷（ExternalSubtitle） */
export interface SoftExternalSubtitle {
  source: string
  lang?: string
  title?: string
}

/** Emby 字幕轨 → AVPlayer 外挂字幕载荷（纯映射，单测覆盖） */
export function softExternalSubtitleOf(track: SoftSubtitleTrack): SoftExternalSubtitle {
  const out: SoftExternalSubtitle = { source: track.url }
  if (track.language) out.lang = track.language
  if (track.label) out.title = track.label
  return out
}

/**
 * 软解实际渲染的字幕轨 Index：AVPlayer 的 SubtitleRender 同时只
 * 渲染一条字幕流，主轨优先、副轨在主轨关闭时接管（原生 <track>
 * 路可双轨同显，软解单轨是 libmedia 的真实形态，不是降级摆设）。
 */
export function effectiveSoftSubtitleIndex(
  primary: number | null,
  secondary: number | null,
): number | null {
  return primary ?? secondary
}

/** 按主/副选择在字幕轨列表里挑出实际渲染的那条（纯映射，单测覆盖） */
export function pickSoftSubtitleTrack(
  tracks: SoftSubtitleTrack[],
  primary: number | null,
  secondary: number | null,
): SoftSubtitleTrack | null {
  const index = effectiveSoftSubtitleIndex(primary, secondary)
  if (index == null) return null
  return tracks.find((t) => t.index === index) ?? null
}

export interface CreateSoftPlayerOptions {
  /** 容器扩展名（如 mkv/mp4）：DirectStreamUrl 常无扩展名，靠它选 demuxer */
  ext?: string
  /** 首帧/起播超时（毫秒）：wasm 下载 + 分析超了就判失败回落，不干等 */
  startTimeoutMs?: number
  /** seek 看门狗单次超时（毫秒），缺省 SOFT_SEEK_TIMEOUT_MS；超时先自动重试一次，仍无结果才判失败 */
  seekTimeoutMs?: number
  /** seek 到达后确认恢复的窗口（毫秒），缺省 SOFT_SEEK_RESUME_MS */
  seekResumeMs?: number
  /** 确认恢复要求时钟持续前进的跨度（毫秒），缺省 SOFT_SEEK_RESUME_SUSTAIN_MS；只为单测取小值 */
  seekResumeSustainMs?: number
  /** seek 在飞时轮询 currentTime 的间隔（毫秒），缺省 500；只为单测取小值 */
  seekPollMs?: number
  /**
   * 以 MSE 原生管线构造（checkUseMSE 回调改判 true）。只许 4K 分流
   * 决策判 'mse' 时传 true（见 softDecodePipelineDecision）；缺省/
   * false 与 wasm 软解旧行为逐字一致。解码开关恒关：MSE 模式下
   * libmedia 把音视频转封装成一条 fMP4 流喂 <video> 元素、由系统
   * 媒体管线解码，解码管线（wasm/WebCodecs）不参与。
   *
   * 音频路径（2026-11-17 真机探针查证结论）：MSE 模式下音频随流
   * 由系统播，libmedia 自己的音频渲染管线不参与，因此
   * firstAudioRendered 事件不会触发——探针实测该事件未发、
   * hasAudio=true、播放头正常推进，且用户亲耳确认声音正常。
   * 「事件不发」是 MSE 模式的正常形态，不是缺陷；调用方的
   * onFirstAudio 在 MSE 模式下不会被调用，不许拿它当出声判据。
   * （非 MSE 的 wasm 模式音频仍走 wasm 解码管线，事件照常。）
   */
  mse?: boolean
  /**
   * 已接收字节回调：构造后每 500ms 轮询 getStats().bufferReceiveBytes，
   * 数值变化时回调（load() 未返回、handle 还不存在的起播期也靠它拿数）。
   * 统计不可用时不回调。实例销毁后自动停轮询。
   */
  onReceivedBytes?: (bytes: number) => void
  /**
   * 软解字幕：当前源的文本字幕轨（与原生 <track> 同源的
   * DeliveryUrl 列表）。起播时把初始选中轨以 loadExternalSubtitle
   * 注册为外挂字幕；运行中选轨/延时经 handle 的 setSubtitleSelection
   * / setSubtitleDelayMs 同步。内嵌字幕流不经这里（见 createSoftPlayer
   * 内 applySubtitleSelection 的注释）。
   */
  subtitles?: SoftSubtitleTrack[]
  /** 初始主字幕（Emby 流 Index，null/缺省=关闭） */
  subtitlePrimary?: number | null
  /** 初始副字幕（Emby 流 Index，null/缺省=关闭） */
  subtitleSecondary?: number | null
  /** 初始字幕延时（毫秒，正值=延后，与原生 shiftVtt 同号） */
  subtitleDelayMs?: number
}

type AVPlayerInstance = {
  load(source: string, options?: { ext?: string }): Promise<void>
  play(options?: { subtitle?: boolean }): Promise<void>
  pause(): Promise<void>
  seek(timestamp: bigint): Promise<void>
  setPlaybackRate(rate: number): void
  getDuration(): bigint
  getVolume(): number
  hasAudio(): boolean
  hasVideo(): boolean
  destroy(): Promise<void>
  on(event: string, handler: (arg?: unknown) => void): void
  readonly currentTime: bigint
  /** play() 内管线判定结果（产物实证的实例属性）：true = 真走了 MSE */
  useMSE?: boolean
  /** libmedia 统计（产物实证）：bufferReceiveBytes 是 IO 层实读字节累计（int64） */
  getStats?: () => { bufferReceiveBytes?: bigint | number } | null | undefined
  /** 外挂字幕（1.3.1 产物实证）：注册一条外挂字幕流，返回状态码（0=成功），不是流 id */
  loadExternalSubtitle(ext: SoftExternalSubtitle): Promise<number>
  /** 切换当前渲染的字幕流（id 取自 getStreams）；无已选流时 libmedia 内部记错不抛 */
  selectSubtitle(id: number): Promise<void>
  getSelectedSubtitleStreamId(): number
  getStreams(): Array<{ id: number }>
  /** 字幕延时（毫秒，正值=延后）；渲染层未建时为无操作 */
  setSubtitleDelay(delay: number): void
  /** 字幕渲染启停；渲染层未建时为无操作 */
  setSubtitleEnable(enable: boolean): void
}

// ---------- 音频解锁（iOS 有画面没声音，2026-11-26 实报） ----------

/** 音频解锁所需的 AVPlayer 静态面（类未加载时为 null） */
export interface SoftAudioContextHost {
  startAudioContext?: () => Promise<void>
  audioContext?: { state: string; resume: () => Promise<void> }
}

/** AVPlayer 类的静态面持有（createSoftPlayer 动态 import 后填） */
let softAVPlayerClass: SoftAudioContextHost | null = null

/**
 * libmedia 的 AudioContext 是否处于「被浏览器拦住」状态：suspended /
 * interrupted 时音频渲染往没启动的上下文里送样本——画面照常、声音
 * 全无。libmedia 1.3.1 产物实证：上下文在选流时才建（起播异步链路
 * 里、用户手势窗口之外），内部只抢救一次 resume（与 0.1 秒竞速），
 * 抢不到只留日志 "the audioContext was not allowed to start. It must
 * be resumed after a user gesture"，此后无人再 resume 即恒无声。
 * running / closed / 未知状态都不需要解锁。
 */
export function audioContextNeedsUnlock(state: string | null | undefined): boolean {
  return state === 'suspended' || state === 'interrupted'
}

/**
 * 对给定的 AVPlayer 静态面做一次音频解锁：上下文已在且未被拦住时
 * 直接回状态（不重复 startAudioContext，避免重复注册状态监听）；
 * 否则先 startAudioContext 建好上下文，被拦住再 resume()。返回解
 * 锁后的上下文状态（类未加载/无此 API 时返回 null）。resume 在无
 * 手势时被拒是常态——吞掉错误、回真实状态，靠调用方在手势里重试。
 */
export async function unlockAudioContextOf(
  host: SoftAudioContextHost | null,
): Promise<string | null> {
  if (!host?.startAudioContext) return null
  const existing = host.audioContext
  if (existing && !audioContextNeedsUnlock(existing.state)) {
    return existing.state
  }
  try {
    await host.startAudioContext()
  } catch {
    return host.audioContext?.state ?? null
  }
  const ctx = host.audioContext
  if (ctx && audioContextNeedsUnlock(ctx.state)) {
    try {
      await ctx.resume()
    } catch {
      /* 无手势的 resume 被拒：保持原状态，等手势内重试 */
    }
  }
  return host.audioContext?.state ?? null
}

/** 当前软解 AudioContext 状态（类未加载/上下文未建时 null） */
export function softAudioContextState(): string | null {
  return softAVPlayerClass?.audioContext?.state ?? null
}

/** 用已加载的 AVPlayer 类做一次音频解锁（见 unlockAudioContextOf） */
export async function unlockSoftAudioContext(): Promise<string | null> {
  return unlockAudioContextOf(softAVPlayerClass)
}

/** AVPlayer 类：构造器 + 音频上下文静态面 */
type AVPlayerClass = (new (options: Record<string, unknown>) => unknown) &
  SoftAudioContextHost

/**
 * 创建软解播放器：AVPlayer 动态 import（不进首屏包），默认强制
 * wasm 解码（enableHardware/enableWebCodecs 都关——<video> 已经探
 * 明解不了）。唯一例外：options.mse=true（仅 4K 源经
 * softDecodePipelineDecision 分流且 MSE 预检通过时）挂 checkUseMSE
 * 回调把管线改判到 libmedia 自带 MSE 原生管线，并挂 useMSE 看门
 * 狗：play 后一旦发现实例没走 MSE（被 libmedia 内部否回解码
 * 管线），立刻销毁判失败——解码管线就是 4K 崩页的那条路，帧
 * 积压以百毫秒计，抢在它前面收场（打法移植探针页 #279）。
 * wasm 一律走站内自托管路径。取流地址先过 resolveSoftDecodeUrl：
 * 直链变体（302 跳跨源）换中继变体，AVPlayer 的 fetch 取流才
 * 读得到字节。
 */
export async function createSoftPlayer(
  container: HTMLDivElement,
  url: string,
  hooks: SoftPlayerHooks,
  options: CreateSoftPlayerOptions = {},
): Promise<SoftPlayerHandle> {
  const { default: AVPlayer } = (await import(
    /* @vite-ignore */ SOFT_PLAYER_SCRIPT_URL
  )) as { default: AVPlayerClass }
  softAVPlayerClass = AVPlayer
  // 取流地址先过软解口径：直链（302 跳跨源）换中继，fetch 才读得到
  const playUrl = await resolveSoftDecodeUrl(url)
  let errorReported = false
  const failOnce = (message: string) => {
    if (errorReported) return
    errorReported = true
    // 失败必须留痕：原始错误进控制台，调用方再翻成人话上提示卡
    console.error('[softDecode] 软解失败:', message)
    hooks.onError(message)
  }
  const player = new AVPlayer({
    container,
    // 解码开关恒关：wasm 模式靠 getWasm 给的自托管解码器干活；MSE
    // 模式根本不走解码管线。WebCodecs 解码路径已随 #261 退役。
    enableHardware: false,
    enableWebCodecs: false,
    // MSE 模式：构造选项里的官方改判口子，恒回 true 请求走 MSE
    // （libmedia 内部先过 isTypeSupported 关、回调才有话语权；
    // 起播前预检已同口径问过）。是否真走 MSE 由下方看门狗验明。
    ...(options.mse === true ? { checkUseMSE: () => true } : {}),
    // 预读深度（libmedia 1.3.1 构造选项 preLoadTime，单位秒，按各流
    // 码率换算成解封装预读包数）：默认 4 秒在中继取流下太浅——软解
    // 只能走服务器中继，上游抖动一来缓冲就见底（2026-11-17 网页
    // 播放卡顿诊断）。设 10 秒：以本次个案 6.5Mbps 计约 8MB 预读，
    // 25Mbps 高码率片约 31MB，内存代价可控；起播侧的服务端已同步
    // 做续段快速通道＋中继预读，10 秒存货足够跨过秒级抖动。
    // jitterBufferMin/Max 保持默认（1/4 秒）：那是渲染侧同步缓冲，
    // 与取流存货不是一回事，本次不动。
    preLoadTime: 10,
    getWasm: (type: string, codecId?: number) => {
      if (type === 'decoder') return wasmPathForDecoder(codecId)
      if (type === 'resampler') return '/wasm/resample/resample-simd.wasm'
      if (type === 'stretchpitcher') return '/wasm/stretchpitch/stretchpitch-simd.wasm'
      return undefined
    },
  }) as unknown as AVPlayerInstance

  // ---- seek 看门狗接线（2026-11-25 事故，状态机见 SoftSeekGuard）----
  // 两阶段：位置到达（seeked 事件 / time 事件 / 轮询读数三路并行，
  // 哪路先到哪路转入确认）→ 确认恢复（播放态下时间持续往前走才
  // 落定，单次 tick 不算——见 SeekProgressTracker 的确认口径（第四轮））。旧代码位置
  // 一到就撤防，复测里「到了目标但播不起来」
  // 的卡死半秒内就没人管了。超时/底层拒绝由状态机先自动重试一次，
  // 仍无结果经 hooks.onSeekFailed 判失败（没接该钩子的调用方回落
  // failOnce/onError)——绝不静默冻结。
  // 暂停态（第三轮，2026-11-25 复测）：libmedia 在 seek 前后会短暂
  // 发出 'paused'（seek 引发重缓冲时暂停渲染），第二轮「暂停态到达
  // 即落定」把这当成用户暂停了——复测里看门狗在到达瞬间撤防，「正
  // 在定位」一次都没出现、冻结 2–4 分钟没人管。所以暂停只有「落定」
  // 时才算（见 SeekProgressTracker.pausedSettled）：暂停开始于本次 seek 下发之前（用户
  // 本来就停着），或暂停已持续满确认窗口（仍无人恢复：要么用户真停
  // 住了，要么解码器停顿超过窗口——后者与用户暂停已无法区分，按暂
  // 停处理）。seek 后才开始的短暂暂停先按到达进确认阶段等着：窗口
  // 内恢复播放即按正常确认走（时间必须往前走）；持续到窗口满仍无
  // 人恢复才按暂停落定。残余：暂停发生在 seek 下发之后时，看门狗最
  // 多因此多发一次同目标重 seek（重发刷新下发时刻，暂停随即早于下
  // 发时刻而落定），不判失败。
  let seekTargetSeconds = 0
  // 到达＋确认恢复的记账走共享 tracker（SeekProgressTracker）：
  // 软解与原生 <video> 两条路径共用同一套到达判定与「持续前进」
  // 口径，不许各写一份（第五轮抽出，原闭包局部逻辑见 tracker）。
  const seekTracker = new SeekProgressTracker({
    resumeMs: options.seekResumeMs,
    sustainMs: options.seekResumeSustainMs,
  })
  // 看门狗已用重试数（状态串上报用，onRetry 时更新）
  let guardRetriesUsed = 0
  let softPlaying = false
  // 最近一次 'paused' 事件的时刻（毫秒）；恢复播放时清掉。看门狗据
  // 此判断暂停是「用户本来就停着」还是「seek 后解码器的短暂
  // 停顿」（见 SeekProgressTracker.pausedSettled）。
  let pausedAt: number | null = null
  let seekingNotified = false
  let seekPollTimer: ReturnType<typeof setInterval> | undefined
  const seekGuard = new SoftSeekGuard({
    timeoutMs: options.seekTimeoutMs ?? SOFT_SEEK_TIMEOUT_MS,
    confirmTimeoutMs: options.seekResumeMs ?? SOFT_SEEK_RESUME_MS,
    onRetry: (attempt) => {
      guardRetriesUsed = attempt
      // 重试 = 同一目标再下发一次（libmedia 以最新 seek 请求为准）
      void issuePlayerSeek(seekTargetSeconds)
      reportGuardState('seeking')
    },
    onTimeout: (phase) => {
      setSeekingNotified(false)
      const message =
        phase === 'confirming'
          ? `seek stalled after arrival: time frozen at ${seekTargetSeconds}s after retry`
          : `seek timeout: no seeked/progress toward ${seekTargetSeconds}s after retry`
      // 判失败必须留痕：原始原因进控制台，调用方翻成人话上错误页
      console.error('[softDecode] seek 看门狗判失败:', message)
      reportGuardState('failed')
      if (hooks.onSeekFailed) hooks.onSeekFailed(message)
      else failOnce(message)
    },
  })
  function setSeekingNotified(v: boolean): void {
    if (seekingNotified === v) return
    seekingNotified = v
    if (v) startSeekPoll()
    else stopSeekPoll()
    hooks.onSeekingChange?.(v)
  }
  function startSeekPoll(): void {
    if (seekPollTimer) return
    seekPollTimer = setInterval(() => {
      if (!seekGuard.pending) {
        setSeekingNotified(false)
        return
      }
      noteSeekProgress(Number(player.currentTime) / 1000)
    }, options.seekPollMs ?? 500)
  }
  function stopSeekPoll(): void {
    if (seekPollTimer) clearInterval(seekPollTimer)
    seekPollTimer = undefined
  }
  /** 看门狗状态串上报（DOM 可观测，见 SoftPlayerHooks.onGuardState） */
  function reportGuardState(transition: SeekGuardTransition): void {
    hooks.onGuardState?.(
      seekGuardStateString(
        transition,
        seekTargetSeconds,
        seekTracker.advancedSeconds,
        guardRetriesUsed,
      ),
    )
  }
  function noteSeekProgress(seconds: number): void {
    if (!seekGuard.pending) return
    const verdict = seekTracker.note(
      { seconds, playing: softPlaying, pausedAt },
      seekGuard.confirming,
    )
    if (!verdict) return
    if (verdict.kind === 'arrive') {
      seekGuard.arrive()
      reportGuardState('confirming')
      return
    }
    // 落定：明细日志口径与抽出前一致——确认阶段的暂停落定打
    // 「paused settled」、持续前进打「sustained advance」（状态机
    // 自己的 complete 日志在这之前）；找目标阶段到达即落定不打
    // 明细。
    seekGuard.complete()
    if (verdict.reason === 'paused' && verdict.fromConfirming) {
      console.info(
        '[softDecode] seek guard complete: paused settled at',
        seconds,
        's target',
        seekTargetSeconds,
        's',
      )
    } else if (verdict.reason === 'sustained') {
      console.info(
        '[softDecode] seek guard complete: sustained advance',
        `${verdict.advancedSeconds.toFixed(2)}s`,
        'at',
        `${seconds.toFixed(2)}s`,
        'target',
        `${seekTargetSeconds}s`,
      )
    }
    setSeekingNotified(false)
    reportGuardState('complete')
  }
  async function issuePlayerSeek(seconds: number): Promise<void> {
    // 每次下发（含看门狗重试）都刷新下发时刻：到达判定的窗口上界按它算
    seekTracker.issued()
    try {
      await player.seek(BigInt(Math.max(0, Math.round(seconds * 1000))))
    } catch {
      // 底层 seek 拒绝：交给看门狗立刻走重试/判失败，不等计时到期
      seekGuard.fail()
    }
  }

  player.on('firstVideoRendered', () => hooks.onFirstVideo())
  player.on('firstAudioRendered', () => hooks.onFirstAudio())
  player.on('playing', () => {
    softPlaying = true
    pausedAt = null
    hooks.onPlaying()
  })
  player.on('played', () => {
    softPlaying = true
    pausedAt = null
    hooks.onPlaying()
  })
  player.on('paused', () => {
    softPlaying = false
    pausedAt = Date.now()
    hooks.onPaused()
  })
  player.on('time', (pts) => {
    const ms = typeof pts === 'bigint' ? Number(pts) : Number(pts ?? 0)
    if (Number.isFinite(ms)) hooks.onTime(ms / 1000)
    if (Number.isFinite(ms)) noteSeekProgress(ms / 1000)
  })
  player.on('seeked', () => {
    // libmedia 明告 seek 已处理完（定位器已到目标）：走与进度到达
    // 同一条路——落定的暂停落定、未落定的短暂暂停进确认阶段等着、
    // 播放态转入确认恢复阶段。绝不能像旧代码那样直接撤防：到了目
    // 标但播不起来时就又没人管了。
    noteSeekProgress(Number(player.currentTime) / 1000)
  })
  player.on('ended', () => hooks.onEnded())
  player.on('error', (err) => {
    const msg = err instanceof Error ? err.message : String(err ?? 'soft decode error')
    failOnce(msg)
  })
  player.on('timeout', () => failOnce('soft decode timeout'))

  // 已接收字节口径：getStats().bufferReceiveBytes（libmedia IO 层每读
  // 一段就累加的实读字节，软解/MSE 两条管线都过这个 IO 层）。构造完
  // 就开始轮询——load() 未返回时 handle 还不存在，准备卡靠回调拿数；
  // 统计读不到（旧产物/异常）就保持 null，调用方不显示数值，不编数。
  let lastReceivedBytes: number | null = null
  const readReceivedBytes = (): number | null => {
    try {
      const raw = player.getStats?.()?.bufferReceiveBytes
      if (typeof raw === 'bigint') return Number(raw)
      if (typeof raw === 'number' && Number.isFinite(raw)) return raw
    } catch {
      /* 统计不可用：保持 null */
    }
    return null
  }
  let statsTimer: ReturnType<typeof setInterval> | undefined
  if (options.onReceivedBytes) {
    const onReceivedBytes = options.onReceivedBytes
    statsTimer = setInterval(() => {
      const bytes = readReceivedBytes()
      if (bytes !== null && bytes !== lastReceivedBytes) {
        lastReceivedBytes = bytes
        onReceivedBytes(bytes)
      }
    }, 500)
  }
  const stopStatsPolling = () => {
    if (statsTimer) clearInterval(statsTimer)
    statsTimer = undefined
  }

  // ---- 软解字幕（外挂文本字幕 → AVPlayer SubtitleRender）----
  // 轨来源与原生路同源（视图传入当前源文本字幕的 DeliveryUrl）。
  // AVPlayer 同时只渲染一条：主轨优先、副轨接管（见
  // effectiveSoftSubtitleIndex）。内嵌字幕不走这里：壳层选轨口径是
  // Emby 流 Index，而实际播放文件可能是同条目的中继副本，其内嵌
  // 流序与 Emby Index 无法可靠对齐，硬凑会选错轨——宁可只做外挂。
  const subtitleTracksByIndex = new Map<number, SoftSubtitleTrack>()
  for (const t of options.subtitles ?? []) subtitleTracksByIndex.set(t.index, t)
  const initialSubtitleTrack = pickSoftSubtitleTrack(
    options.subtitles ?? [],
    options.subtitlePrimary ?? null,
    options.subtitleSecondary ?? null,
  )
  let desiredSubtitleDelayMs = options.subtitleDelayMs ?? 0
  let subtitleDestroyed = false
  let playerDestroyed = false
  const subtitleLoads = new Map<number, Promise<number | null>>()
  function loadSoftSubtitleTrack(track: SoftSubtitleTrack): Promise<number | null> {
    let p = subtitleLoads.get(track.index)
    if (!p) {
      p = (async () => {
        try {
          // loadExternalSubtitle 的返回值是状态码、不是流 id：靠注册
          // 前后 getStreams() 的差集认出新流的 id（一次只注册一条）
          const before = new Set(player.getStreams().map((s) => s.id))
          await player.loadExternalSubtitle(softExternalSubtitleOf(track))
          const added = player.getStreams().filter((s) => !before.has(s.id))
          return added.length ? added[0].id : null
        } catch (e) {
          // 字幕加载失败不拖垮播放：按无此轨处理（与原生 <track>
          // 加载失败同样无字幕，但控制台留痕、不静默）
          console.error('[softDecode] 外挂字幕加载失败:', track.index, e)
          return null
        }
      })()
      subtitleLoads.set(track.index, p)
    }
    return p
  }
  async function applySubtitleSelection(
    primary: number | null,
    secondary: number | null,
  ): Promise<void> {
    if (subtitleDestroyed) return
    const track = pickSoftSubtitleTrack(
      [...subtitleTracksByIndex.values()],
      primary,
      secondary,
    )
    if (!track) {
      try {
        player.setSubtitleEnable(false)
      } catch {
        /* 渲染层未建：本来就没字幕 */
      }
      return
    }
    const streamId = await loadSoftSubtitleTrack(track)
    if (subtitleDestroyed) return
    if (streamId == null) {
      try {
        player.setSubtitleEnable(false)
      } catch {
        /* 同上 */
      }
      return
    }
    const selected = player.getSelectedSubtitleStreamId()
    if (selected !== streamId && selected !== -1) {
      // 已有流在渲染（play 时 findBestStream 的默认选择，可能是
      // 内嵌轨）：切到用户选的轨
      try {
        await player.selectSubtitle(streamId)
      } catch (e) {
        console.error('[softDecode] 字幕切轨失败:', streamId, e)
      }
    }
    // selected === streamId：已是目标轨。selected === -1：渲染层
    // 还没建——起播前注册的轨由 play({ subtitle: true }) 建层时
    // 选中，播中注册的轨由 loadExternalSubtitle 在播放稳态自建
    // 渲染层并绑定该流（均为 1.3.1 产物实现口径），都不必再切。
    // 最后统一补开渲染、补上期望延时（setSubtitleDelay 在渲染层
    // 未建时是无操作，靠 desired 值在每次 apply 时重下，不丢）。
    try {
      player.setSubtitleEnable(true)
    } catch {
      /* 渲染层未建：无操作 */
    }
    try {
      player.setSubtitleDelay(desiredSubtitleDelayMs)
    } catch {
      /* 同上 */
    }
  }

  const startTimeoutMs = options.startTimeoutMs ?? SOFT_START_TIMEOUT_MS
  let startTimer: ReturnType<typeof setTimeout> | undefined
  const startGuard = new Promise<never>((_resolve, reject) => {
    startTimer = setTimeout(
      () => reject(new Error('soft decode start timeout')),
      startTimeoutMs,
    )
  })
  // MSE 看门狗（移植探针页 #279 的已验证打法）：起播期间 120ms
  // 轮询实例 useMSE 标志，发现不是 MSE 就判失败——外层 catch 会
  // 销毁实例回落提示卡，不让解码管线在 4K 上真跑起来（解码帧
  // 积压以百毫秒计，抢在它前面收场）。非 MSE 模式此承诺永不落定。
  let vetoTimer: ReturnType<typeof setInterval> | undefined
  const mseVeto = new Promise<never>((_resolve, reject) => {
    if (options.mse !== true) return
    vetoTimer = setInterval(() => {
      if (player.useMSE === false) {
        reject(new Error('mse pipeline vetoed: platform fell back to decoder pipeline'))
      }
    }, 120)
  })
  try {
    await Promise.race([
      (async () => {
        await player.load(playUrl, options.ext ? { ext: options.ext } : undefined)
        if (initialSubtitleTrack) {
          // 起播前注册初始外挂字幕，play 的 findBestStream 才选得到
          // 它；注册慢/失败不挡起播（5 秒未回先起播，play 后由
          // applySubtitleSelection 等同一个加载承诺兜底纠正）
          await Promise.race([
            loadSoftSubtitleTrack(initialSubtitleTrack),
            new Promise<void>((resolve) => setTimeout(resolve, 5000)),
          ])
        }
        await player.play(initialSubtitleTrack ? { subtitle: true } : undefined)
      })(),
      startGuard,
      mseVeto,
    ])
    // 起播落定后再核一次：轮询间隙里刚翻成 false 的同样算被否回
    if (options.mse === true && player.useMSE === false) {
      throw new Error('mse pipeline vetoed: platform fell back to decoder pipeline')
    }
  } catch (e) {
    failOnce(e instanceof Error ? e.message : String(e))
    stopStatsPolling()
    // 起播失败：把已建的实例销毁，不留后台继续拉 wasm/拉流的残留
    try {
      await player.destroy()
    } catch {
      /* 销毁失败不掩盖原始错误 */
    }
    throw e
  } finally {
    if (startTimer) clearTimeout(startTimer)
    if (vetoTimer) clearInterval(vetoTimer)
  }

  // 音频解锁首试（仅 wasm 管线；MSE 管线音频走 <video> 元素、与
  // AudioContext 无关）：先补一次（恰在手势窗口内就直接解开）；
  // 仍被拦住则经 onAudioBlocked 让调用方提示用户点画面——点按
  // 处理里会再调 unlockAudio 在手势内解。销毁后不再回调。
  if (options.mse !== true) {
    void unlockSoftAudioContext().then((state) => {
      if (!playerDestroyed && audioContextNeedsUnlock(state)) {
        hooks.onAudioBlocked?.()
      }
    })
  }

  // 起播落定后把初始字幕选择纠正到位（play 的默认选流可能选了
  // 内嵌轨）：不等它完成，字幕晚几百毫秒到位好过挡住起播返回
  if (initialSubtitleTrack) {
    void applySubtitleSelection(
      options.subtitlePrimary ?? null,
      options.subtitleSecondary ?? null,
    ).catch((e) => console.error('[softDecode] 初始字幕应用失败:', e))
  }

  return {
    play: () => player.play(),
    pause: () => player.pause(),
    seek: (seconds: number) => {
      // seek 从这里开始归看门狗管（位置到达转确认恢复、时间真的往前
      // 走才落定；超时/卡住先自动重试一次、仍无结果判失败），不许再
      // 像旧行为那样打出去就不管、静默冻结
      seekTargetSeconds = Math.max(0, seconds)
      seekTracker.begin(seekTargetSeconds)
      guardRetriesUsed = 0
      seekGuard.begin(seekTargetSeconds)
      setSeekingNotified(true)
      reportGuardState('seeking')
      return issuePlayerSeek(seekTargetSeconds)
    },
    setRate: (rate: number) => player.setPlaybackRate(rate),
    currentTimeSeconds: () => Number(player.currentTime) / 1000,
    durationSeconds: () => Number(player.getDuration()) / 1000,
    hasVideo: () => player.hasVideo(),
    hasAudio: () => player.hasAudio(),
    receivedBytes: () => readReceivedBytes() ?? lastReceivedBytes,
    unlockAudio: () => unlockSoftAudioContext(),
    audioContextState: () => softAudioContextState(),
    setSubtitleSelection: (primary: number | null, secondary: number | null) =>
      applySubtitleSelection(primary, secondary),
    setSubtitleDelayMs: (ms: number) => {
      desiredSubtitleDelayMs = ms
      try {
        player.setSubtitleDelay(ms)
      } catch {
        /* 渲染层未建：desired 已记下，下次 apply 时补 */
      }
    },
    destroy: async () => {
      playerDestroyed = true
      subtitleDestroyed = true
      stopStatsPolling()
      seekGuard.dispose()
      stopSeekPoll()
      await player.destroy()
    },
  }
}
