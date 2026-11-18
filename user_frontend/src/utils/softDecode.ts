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

/** 4K（UHD）判定线：宽达此值或高达 SOFT_UHD_MIN_HEIGHT 任一命中即按 4K 算 */
export const SOFT_UHD_MIN_WIDTH = 3840
export const SOFT_UHD_MIN_HEIGHT = 2160

/** 护栏命中时提示卡的文案（人话，指出路：客户端 App 自带解码） */
export const SOFT_UHD_BLOCK_MESSAGE =
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
 * 实例化 AVPlayer，直接出提示卡（SOFT_UHD_BLOCK_MESSAGE）。
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
  if (width >= SOFT_UHD_MIN_WIDTH || height >= SOFT_UHD_MIN_HEIGHT) {
    return { blocked: true, width, height, reason: 'uhd-soft-decode' }
  }
  return { blocked: false, width, height, reason: 'below-uhd' }
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
  return { pipeline: 'mse', width: gate.width, height: gate.height, reason: 'uhd-mse' }
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

/** 跑一遍 MSE 预检：候选串逐个问平台 isTypeSupported（同步 API）。第二个参数仅供测试注入。 */
export function runSoftMsePreflight(
  source: DecodeSourceLike,
  isTypeSupported?: (mime: string) => boolean | null,
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
  const entries: SoftMsePreflightEntry[] = mseMimeCandidates(source).map((c) => {
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

export interface CreateSoftPlayerOptions {
  /** 容器扩展名（如 mkv/mp4）：DirectStreamUrl 常无扩展名，靠它选 demuxer */
  ext?: string
  /** 首帧/起播超时（毫秒）：wasm 下载 + 分析超了就判失败回落，不干等 */
  startTimeoutMs?: number
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
}

type AVPlayerInstance = {
  load(source: string, options?: { ext?: string }): Promise<void>
  play(): Promise<void>
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
}

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
  )) as { default: new (options: Record<string, unknown>) => unknown }
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

  player.on('firstVideoRendered', () => hooks.onFirstVideo())
  player.on('firstAudioRendered', () => hooks.onFirstAudio())
  player.on('playing', () => hooks.onPlaying())
  player.on('played', () => hooks.onPlaying())
  player.on('paused', () => hooks.onPaused())
  player.on('time', (pts) => {
    const ms = typeof pts === 'bigint' ? Number(pts) : Number(pts ?? 0)
    if (Number.isFinite(ms)) hooks.onTime(ms / 1000)
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
        await player.play()
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

  return {
    play: () => player.play(),
    pause: () => player.pause(),
    seek: (seconds: number) => player.seek(BigInt(Math.max(0, Math.round(seconds * 1000)))),
    setRate: (rate: number) => player.setPlaybackRate(rate),
    currentTimeSeconds: () => Number(player.currentTime) / 1000,
    durationSeconds: () => Number(player.getDuration()) / 1000,
    hasVideo: () => player.hasVideo(),
    hasAudio: () => player.hasAudio(),
    receivedBytes: () => readReceivedBytes() ?? lastReceivedBytes,
    destroy: async () => {
      stopStatsPolling()
      await player.destroy()
    },
  }
}
