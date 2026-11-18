/**
 * 准备播放中央卡的状态文案决策（2026-11-18，差异清单 G-168/170/172/
 * 175/176）：形态与序列照 UHDNow 逐帧盘点——卡内只有首行「准备播放」、
 * 一行状态文案、末行恒定解码器提示，没有副行、没有进度条。
 *
 * 状态序列（与播放管线的真实阶段对齐，不许编造阶段）：
 *   step 0（取 PlaybackInfo/选版本中）→ 无状态行（G-170）
 *   step 1（源已选定、打开中）→「正在打开源」
 *   step 2（已向管线交源）→ 软解/MSE 的 load() 未返回前仍是「正在打开源」，
 *     load() 返回（容器已分析、等首帧）或原生管线进入 step 2 后是
 *     「正在分析容器（{容器小写}）」
 *   step 3（原生 loadedmetadata 已到、等 canplay）→ 仍是「正在分析容器」
 *   慢态（任一阶段等待过久）→ 主行换「网速较慢，仍在读取（已接收 X.XMB）」
 *
 * 已接收字节只许来自播放器真实计数：libmedia 路径是
 * getStats().bufferReceiveBytes（IO 层实读字节），原生路径是
 * Chromium 的 webkit 解码字节计数；两者都没有时 receivedBytes=null，
 * 慢文案不带数值，不许拿缓冲时长×码率之类的估算冒充（G-172）。
 */

export interface PrepStatusInput {
  /** 准备阶段序号（WebPlayerView 的 prepStepIndex，0–3） */
  step: number
  /** 慢态标记（等待超时、数据未到） */
  slow: boolean
  /** 播放器真实已接收字节；无真实数据源时为 null */
  receivedBytes: number | null
  /** 容器名（如 mkv/mp4），大小写不限，渲染时转小写 */
  container?: string | null
  /** 当前走 libmedia 管线（软解/MSE） */
  softActive: boolean
  /** libmedia load() 已返回（容器分析完成、等首帧） */
  softLoadDone: boolean
}

/** 已接收字节 → MB 文案数值：一位小数、十进制 MB、无空格（友站格式） */
export function formatReceivedMb(bytes: number): string {
  return (bytes / 1_000_000).toFixed(1)
}

/**
 * 末行恒定解码器提示（G-176）。MB 数是我方自托管解码器套件实测：
 * public/wasm 下 getWasm 实际下发的 8 件 wasm（decode 六件
 * 5,597,469 B + resample 264,049 B + stretchpitch 46,124 B）合计
 * 5,907,642 B ≈ 5.9 MB——不许照抄友站的 2–4 MB。
 */
export const PREP_DECODER_HINT = '首次播放需要下载解码器（约 5.9 MB），缓存后下次秒开。'

export function prepStatusText(input: PrepStatusInput): string {
  if (input.slow) {
    return input.receivedBytes !== null
      ? `网速较慢，仍在读取（已接收 ${formatReceivedMb(input.receivedBytes)}MB）`
      : '网速较慢，仍在读取'
  }
  if (input.step <= 0) return ''
  if (input.step === 1) return '正在打开源'
  // step >= 2：软解/MSE 在 load() 返回前，管线还停在打开/读取源
  if (input.softActive && !input.softLoadDone) return '正在打开源'
  const container = (input.container || '').trim().toLowerCase()
  return container ? `正在分析容器（${container}）` : '正在分析容器'
}
