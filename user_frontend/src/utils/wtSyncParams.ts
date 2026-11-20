/**
 * 一起看同步参数（G-192~G-199 定案）：同步引擎的机制数值，默认值照
 * 参考站 友站取（开始纠偏 1000 / 结束纠偏 200 / 直接对齐 3000 /
 * 倍速幅度 5% / 自动对齐间隔 3000）。
 *
 * 参数只决定本机播放行为：按用户本地持久化（localStorage），
 * 不广播给房间成员、不上后端。分段语义：
 *   差 ≤ 开始纠偏            → 不动
 *   开始纠偏 < 差 ≤ 直接对齐 → 倍速微调追赶/等待（纠偏中差收敛到
 *                              结束纠偏以内即停，与开始纠偏成迟滞对）
 *   差 > 直接对齐            → 直接 seek 硬对齐
 */

export interface WtSyncParams {
  /** 开始纠偏（ms）：与目标进度差超过它才启动纠偏 */
  startMs: number
  /** 结束纠偏（ms）：纠偏中差收敛到它以内即停止纠偏 */
  endMs: number
  /** 直接对齐（ms）：差超过它直接 seek 硬对齐 */
  directMs: number
  /** 倍速调整幅度（%）：纠偏区内以 ±此幅度调 playbackRate */
  ratePct: number
  /** 自动对齐间隔（ms）：成员侧周期对齐自查间隔 */
  intervalMs: number
}

export const WT_SYNC_DEFAULTS: WtSyncParams = {
  startMs: 1000,
  endMs: 200,
  directMs: 3000,
  ratePct: 5,
  intervalMs: 3000,
}

export const WT_SYNC_LOCAL_KEY = 'aetrix-wt-sync-params'

const RANGES: Record<keyof WtSyncParams, [number, number]> = {
  startMs: [50, 10000],
  endMs: [0, 5000],
  directMs: [200, 60000],
  ratePct: [1, 25],
  intervalMs: [500, 60000],
}

function clampField(
  key: keyof WtSyncParams,
  value: unknown,
  fallback: number,
): number {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return fallback
  const [min, max] = RANGES[key]
  return Math.min(max, Math.max(min, Math.round(n)))
}

/** 任意输入消毒成合法参数：逐字段回落默认/钳制，结束纠偏不大于开始纠偏 */
export function sanitizeWtSyncParams(raw: unknown): WtSyncParams {
  const src =
    raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const out: WtSyncParams = {
    startMs: clampField('startMs', src.startMs, WT_SYNC_DEFAULTS.startMs),
    endMs: clampField('endMs', src.endMs, WT_SYNC_DEFAULTS.endMs),
    directMs: clampField('directMs', src.directMs, WT_SYNC_DEFAULTS.directMs),
    ratePct: clampField('ratePct', src.ratePct, WT_SYNC_DEFAULTS.ratePct),
    intervalMs: clampField(
      'intervalMs',
      src.intervalMs,
      WT_SYNC_DEFAULTS.intervalMs,
    ),
  }
  // 迟滞对前提：退出阈值不得高于进入阈值，否则纠偏刚启即停
  if (out.endMs > out.startMs) out.endMs = out.startMs
  return out
}

export function loadWtSyncParams(): WtSyncParams {
  try {
    const raw = globalThis.localStorage?.getItem(WT_SYNC_LOCAL_KEY)
    if (!raw) return { ...WT_SYNC_DEFAULTS }
    return sanitizeWtSyncParams(JSON.parse(raw))
  } catch {
    return { ...WT_SYNC_DEFAULTS }
  }
}

/** 保存并返回消毒后的参数（保存即由调用方即时生效，G-199） */
export function saveWtSyncParams(params: WtSyncParams): WtSyncParams {
  const clean = sanitizeWtSyncParams(params)
  try {
    globalThis.localStorage?.setItem(WT_SYNC_LOCAL_KEY, JSON.stringify(clean))
  } catch {
    /* 隐私模式等写不进时只在内存里生效 */
  }
  return clean
}

export interface WtDriftDecision {
  /**
   * seek=硬对齐到目标进度；rate=按 rate 字段设倍速纠偏；
   * restore=纠偏结束、倍速恢复基准；none=不动
   */
  action: 'none' | 'seek' | 'rate' | 'restore'
  /** action=rate/restore 时应设置的 playbackRate（基准＝锚点 rate） */
  rate: number
  /** 本次判定后是否处于纠偏中 */
  correcting: boolean
}

/**
 * 一次纠偏判定（纯函数）。diffMs = 目标进度 − 本地进度（正＝本地落后）。
 * baseRate 为锚点 rate（房主倍速），纠偏倍速在其上 ±ratePct 浮动，
 * 收敛后恢复到它（锚点 rate 字段在此消费，G-197）。
 */
export function decideWtDrift(args: {
  diffMs: number
  correcting: boolean
  params: WtSyncParams
  baseRate: number
}): WtDriftDecision {
  const { diffMs, correcting, params } = args
  const base =
    Number.isFinite(args.baseRate) && args.baseRate > 0 ? args.baseRate : 1
  const abs = Math.abs(diffMs)
  if (abs > params.directMs) {
    return { action: 'seek', rate: base, correcting: false }
  }
  if (abs > params.startMs || (correcting && abs > params.endMs)) {
    return {
      action: 'rate',
      rate: base * (1 + (Math.sign(diffMs) * params.ratePct) / 100),
      correcting: true,
    }
  }
  if (correcting) {
    return { action: 'restore', rate: base, correcting: false }
  }
  return { action: 'none', rate: base, correcting: false }
}

/**
 * 按锚点时间戳外推当前目标进度（成员自查用，G-198）：播放中按锚点
 * rate 推进，暂停停在锚点位置，本机时钟早于锚点时刻时不倒退。
 */
export function wtTargetPosition(
  state: { position: number; playing: boolean; rate: number; ts: number },
  nowMs: number,
): number {
  if (!state.playing) return state.position
  const elapsed = Math.max(0, nowMs / 1000 - state.ts)
  return state.position + elapsed * (state.rate || 1)
}
