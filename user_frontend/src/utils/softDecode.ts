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
 * 约束（批 1 实证）：只编了 simd 变体；FFmpeg 钉 7.1；不承诺 4K/HDR。
 * 软解只覆盖 HEVC/H.264 画面 + AAC/AC3/EAC3/DTS 声音；AV1/Opus 等
 * 不在 wasm 集里，照旧走解码提示卡，不许硬上。
 */
import {
  normalizeAudioCodec,
  normalizeVideoCodec,
  type DecodeSourceLike,
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
}

/**
 * 创建软解播放器：AVPlayer 动态 import（不进首屏包），强制 wasm 解码
 *（enableHardware/enableWebCodecs 都关——浏览器已经探明解不了，再试
 * 硬解只会重蹈覆辙）。wasm 一律走站内自托管路径。取流地址先过
 * resolveSoftDecodeUrl：直链变体（302 跳跨源）换中继变体，
 * AVPlayer 的 fetch 取流才读得到字节。
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
    enableHardware: false,
    enableWebCodecs: false,
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

  const startTimeoutMs = options.startTimeoutMs ?? SOFT_START_TIMEOUT_MS
  let startTimer: ReturnType<typeof setTimeout> | undefined
  const startGuard = new Promise<never>((_resolve, reject) => {
    startTimer = setTimeout(
      () => reject(new Error('soft decode start timeout')),
      startTimeoutMs,
    )
  })
  try {
    await Promise.race([
      (async () => {
        await player.load(playUrl, options.ext ? { ext: options.ext } : undefined)
        await player.play()
      })(),
      startGuard,
    ])
  } catch (e) {
    failOnce(e instanceof Error ? e.message : String(e))
    // 起播失败：把已建的实例销毁，不留后台继续拉 wasm/拉流的残留
    try {
      await player.destroy()
    } catch {
      /* 销毁失败不掩盖原始错误 */
    }
    throw e
  } finally {
    if (startTimer) clearTimeout(startTimer)
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
    destroy: () => player.destroy(),
  }
}
