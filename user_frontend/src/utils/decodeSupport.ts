/**
 * 解码能力探测：判断一个版本（MediaSource）的视频/音频编码，
 * 当前浏览器能不能用 <video> 原生解出来。
 *
 * 形态对齐 UHDNow 播放页：起播前先做能力探测（它用
 * VideoDecoder.isConfigSupported 决定要不要下载 wasm 解码器），
 * 探到解不了就当场给用户明确提示和出路，不等播放失败了再猜。
 * 我方不自备 wasm 解码器（友站那套是 FFmpeg GPL 构建，不分发），
 * 提示的出路落在现行真实路径：换同片能播的版本，或回详情页用
 * 第三方播放器 App 打开（App 自带解码）。
 *
 * 信号口径：
 * - 主信号 canPlayType —— <video> 元素自己的口径，最贴近真实结果；
 * - 复核信号 WebCodecs isConfigSupported —— 只有它明确说「能解」时
 *   才推翻主信号的「解不了」，避免误拦本来能播的版本。
 * 信号不全（编码参数不在表里、容器认不出）一律判 unknown 放行先播，
 * 失败后再用同步探测结果生成对症文案。
 */

export interface DecodeStreamLike {
  Type: string
  Codec?: string
  Width?: number | null
  Height?: number | null
}

export interface DecodeSourceLike {
  Id: string
  Container?: string
  MediaStreams?: DecodeStreamLike[]
}

export type DecodeStatus = 'supported' | 'unsupported' | 'unknown'

export interface DecodeVerdict {
  status: DecodeStatus
  badVideo: boolean
  badAudio: boolean
  /** 解不了的视频编码展示名（如 HEVC），没有为空串 */
  videoName: string
  /** 解不了的音频编码展示名（如 AC3），没有为空串 */
  audioName: string
  /**
   * 起播前可直接拦：容器是浏览器原生可播面（mp4/mov/webm），
   * 且信号确凿（canPlayType 明确不支持、WebCodecs 不反对）。
   * 其余容器（mkv 等）保持「先试着播」，失败时用 verdict 写文案。
   */
  hardBlock: boolean
}

const CONTAINER_MIME: Record<string, string> = {
  mp4: 'video/mp4',
  mov: 'video/mp4',
  m4v: 'video/mp4',
  webm: 'video/webm',
  mkv: 'video/x-matroska',
  ts: 'video/mp2t',
  m2ts: 'video/mp2t',
  avi: 'video/x-msvideo',
}

const NATIVE_CONTAINERS = new Set(['mp4', 'mov', 'm4v', 'webm'])

/** 归一化编码 → canPlayType 的 codecs 参数（RFC 6381） */
const VIDEO_PARAM: Record<string, string> = {
  h264: 'avc1.640028',
  hevc: 'hvc1.1.6.L93.B0',
  av1: 'av01.0.08M.08',
  vp9: 'vp09.00.10.08',
  vp8: 'vp8',
  mpeg4: 'mp4v.20.8',
  vc1: 'vc-1',
}

const AUDIO_PARAM: Record<string, string> = {
  aac: 'mp4a.40.2',
  mp3: 'mp3',
  ac3: 'ac-3',
  eac3: 'ec-3',
  opus: 'opus',
  vorbis: 'vorbis',
  flac: 'flac',
  dts: 'dts',
  truehd: 'truehd',
}

/** 归一化编码 → WebCodecs codec 串（仅视频复核用） */
const WEBCODECS_PARAM: Record<string, string> = {
  h264: 'avc1.640028',
  hevc: 'hev1.1.6.L93.B0',
  av1: 'av01.0.08M.08',
  vp9: 'vp09.00.10.08',
}

const DISPLAY_NAME: Record<string, string> = {
  h264: 'H.264',
  hevc: 'HEVC',
  av1: 'AV1',
  vp9: 'VP9',
  vp8: 'VP8',
  mpeg4: 'MPEG-4',
  vc1: 'VC-1',
  aac: 'AAC',
  mp3: 'MP3',
  ac3: 'AC3',
  eac3: 'EAC3',
  opus: 'Opus',
  vorbis: 'Vorbis',
  flac: 'FLAC',
  dts: 'DTS',
  truehd: 'TrueHD',
}

export function normalizeVideoCodec(raw?: string): string {
  const c = (raw || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  if (c === 'h265' || c === 'x265' || c === 'hvc1' || c === 'hev1') return 'hevc'
  if (c === 'avc' || c === 'avc1' || c === 'x264') return 'h264'
  if (c === 'vp09') return 'vp9'
  return c
}

export function normalizeAudioCodec(raw?: string): string {
  const c = (raw || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  if (c === 'dca' || c === 'dtshd' || c === 'dtsma') return 'dts'
  if (c === 'ddp' || c === 'ac3plus') return 'eac3'
  if (c === 'mp4a') return 'aac'
  return c
}

interface ProbeSide {
  probed: boolean
  bad: boolean
  name: string
}

function probeSide(
  el: HTMLVideoElement,
  mime: string,
  param: string | undefined,
  codec: string,
): ProbeSide {
  if (!param) return { probed: false, bad: false, name: '' }
  const answer = el.canPlayType(`${mime}; codecs="${param}"`)
  const bad = answer === ''
  return { probed: true, bad, name: bad ? DISPLAY_NAME[codec] || codec.toUpperCase() : '' }
}

/** 同步探测（只要 canPlayType）：起播前拦截与失败后写文案共用 */
export function probeDecodeSync(source: DecodeSourceLike): DecodeVerdict {
  const empty: DecodeVerdict = {
    status: 'unknown',
    badVideo: false,
    badAudio: false,
    videoName: '',
    audioName: '',
    hardBlock: false,
  }
  const mime = CONTAINER_MIME[(source.Container || '').toLowerCase()]
  const streams = source.MediaStreams || []
  if (!mime || !streams.length) return empty
  const el = document.createElement('video')
  const vs = streams.find((s) => s.Type === 'Video')
  const as = streams.find((s) => s.Type === 'Audio')
  const vCodec = normalizeVideoCodec(vs?.Codec)
  const aCodec = normalizeAudioCodec(as?.Codec)
  const v: ProbeSide = vs
    ? probeSide(el, mime, VIDEO_PARAM[vCodec], vCodec)
    : { probed: false, bad: false, name: '' }
  const a: ProbeSide = as
    ? probeSide(el, mime, AUDIO_PARAM[aCodec], aCodec)
    : { probed: false, bad: false, name: '' }
  return {
    status: v.bad || a.bad ? 'unsupported' : v.probed || a.probed ? 'supported' : 'unknown',
    badVideo: v.bad,
    badAudio: a.bad,
    videoName: v.name,
    audioName: a.name,
    hardBlock: false,
  }
}

/** 完整探测：同步结果之上再用 WebCodecs 复核，并算出能否起播前直拦 */
export async function probeDecode(source: DecodeSourceLike): Promise<DecodeVerdict> {
  const verdict = probeDecodeSync(source)
  if (verdict.status !== 'unsupported') return verdict
  // WebCodecs 复核：视频硬解其实可用时不要起播前拦死，降为先试着播，
  // 真失败了还有失败路径的对症文案兜底。
  const webcodecsOk = verdict.badVideo ? await webcodecsVideoSupported(source) : false
  const container = (source.Container || '').toLowerCase()
  return { ...verdict, hardBlock: NATIVE_CONTAINERS.has(container) && !webcodecsOk }
}

interface WebCodecsWindow {
  VideoDecoder?: {
    isConfigSupported(cfg: {
      codec: string
      codedWidth: number
      codedHeight: number
    }): Promise<{ supported?: boolean }>
  }
}

async function webcodecsVideoSupported(source: DecodeSourceLike): Promise<boolean> {
  try {
    const vd = (window as unknown as WebCodecsWindow).VideoDecoder
    if (!vd) return false
    const vs = (source.MediaStreams || []).find((s) => s.Type === 'Video')
    const param = WEBCODECS_PARAM[normalizeVideoCodec(vs?.Codec)]
    if (!param) return false
    const res = await vd.isConfigSupported({
      codec: param,
      codedWidth: vs?.Width || 1280,
      codedHeight: vs?.Height || 720,
    })
    return !!res?.supported
  } catch {
    return false
  }
}
