/**
 * 网页软解（2026-11-14 批 2）：浏览器硬解失败时，用站内自编 LGPL wasm
 * 解码器（libmedia AVPlayer + /wasm/decode/*-simd.wasm）兜底。
 *
 * 决策与播放器构造分开，决策是纯函数、可单测：
 * - shouldSoftDecode：探测结论 + 编解码子集 + 开关，三者齐了才走软解；
 * - createSoftPlayer：AVPlayer 只在这里被动态 import（懒加载 chunk，
 *   开关关着/原生能播时不下载这 600KB+ 的播放器代码）。
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

// ---------- AVPlayer 包装 ----------

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
 * 硬解只会重蹈覆辙）。wasm 一律走站内自托管路径。
 */
export async function createSoftPlayer(
  container: HTMLDivElement,
  url: string,
  hooks: SoftPlayerHooks,
  options: CreateSoftPlayerOptions = {},
): Promise<SoftPlayerHandle> {
  const { default: AVPlayer } = await import('@libmedia/avplayer')
  let errorReported = false
  const failOnce = (message: string) => {
    if (errorReported) return
    errorReported = true
    hooks.onError(message)
  }
  const player = new AVPlayer({
    container,
    enableHardware: false,
    enableWebCodecs: false,
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

  const startTimeoutMs = options.startTimeoutMs ?? 45000
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
        await player.load(url, options.ext ? { ext: options.ext } : undefined)
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
