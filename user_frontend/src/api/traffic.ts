/**
 * 流量额度 API（门户后端 /api/user/traffic*，流量额度一期）。
 *
 * 额度未启用（套餐未声明月额度、全局默认也没配）时后端回
 * enabled=false，页面整段不展示，绝不拿 0 额度吓人。
 * 余额购买（钱包批 2）：后端回 balance_purchase_available +
 * 单价 + 钱包余额，前端页签据此放开，成交走 buyBalanceTraffic。
 */
import api from '@/api'

export interface TrafficSummary {
  enabled: boolean
  quota_bytes: number | null
  used_bytes: number
  gross_used_bytes: number
  relay_bytes: number
  local_bytes: number
  /** 302 直链只有起播次数、没有字节（字节不过本机，计不了） */
  direct_plays: number
  credit_bytes: number
  credit_applied_bytes: number
  remaining_bytes: number | null
  used_pct: number
  exhausted: boolean
  period_start: string
  period_end: string
  reset_date: string
}

/** 时间换流量包（2026-11-15 照友站录屏包型）：包参数全部由
 * 后端配置下发，前端不写死天数/比例——credit ＝ 当月配额 ×
 * quota_percent，只冲已用、不扩总配额，固定扣 days 天时长。 */
export interface TrafficExchangePackage {
  key: string
  name: string
  days: number
  quota_percent: number
}

/** 时间换流量规则视图：包表 + 该用户额度与本周期已抵扣 */
export interface TrafficExchangeRule {
  packages: TrafficExchangePackage[]
  quota_bytes: number | null
  credited_bytes: number
}

export interface TrafficOverview extends TrafficSummary {
  exchange_rule: TrafficExchangeRule
  balance_purchase_available: boolean
  /** 余额购流量单价（分/100GB，钱包批 2 下发；不可用时为 0） */
  balance_price_per_100gb_fen: number
  /** 当前钱包余额（元字符串，不可用时为 '0.00'） */
  wallet_balance: string
}

export interface TrafficExchangeResult {
  ok: boolean
  replayed: boolean
  log: {
    days_deducted: number
    bytes_credited: number
  }
  traffic: TrafficSummary
}

/** 取流量概览：失败返回 null（调用方据此整段隐藏，不报错吓人） */
export async function getTraffic(): Promise<TrafficOverview | null> {
  try {
    return await api.get<never, TrafficOverview>('/api/user/traffic')
  } catch {
    return null
  }
}

/** 时间换流量成交（按包）：packageKey 是规则下发的包 key；
 * idempotencyKey 由弹窗在打开时生成一次，同一次确认的重试/双击
 * 只成交一次（后端幂等）。失败抛错，文案走后端 detail。 */
export async function exchangeTimeForTraffic(
  packageKey: string,
  idempotencyKey: string,
): Promise<TrafficExchangeResult> {
  return api.post<never, TrafficExchangeResult>('/api/user/traffic/exchange', {
    package: packageKey,
    idempotency_key: idempotencyKey,
  })
}

export interface BalancePurchaseResult {
  ok: boolean
  replayed: boolean
  log: {
    id: number
    kind: string
    bytes_credited: number
    period_start: string | null
    period_end: string | null
  }
  traffic: TrafficSummary
  paid_amount: string
  balance_after: string
  gb: number
}

/** 余额购流量成交：gb 为任意 ≥100 的整数（后端再校验下限并按
 * 单价比例计价），idempotencyKey 由弹窗在打开时生成一次，
 * 同一次确认重试/双击只成交一次（后端幂等）。失败抛错，文案走后端 detail。 */
export async function buyBalanceTraffic(
  gb: number,
  idempotencyKey: string,
): Promise<BalancePurchaseResult> {
  return api.post<never, BalancePurchaseResult>('/api/user/traffic/buy-balance', {
    gb,
    idempotency_key: idempotencyKey,
  })
}

const GB = 1024 ** 3

/** 购流量弹窗流量卡口径：GB 两位小数（照友站帧「已用 0.27 GB / 共 500.00 GB」） */
export function formatGbFixed(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  return (bytes / GB).toFixed(2)
}

/** 大数字用：剩余量按 GB 显示（≥100 取整向下取，照友站帧
 * 499.73 →「499」；否则一位小数）。概览 meta 行已用/共同口径。 */
export function formatGbValue(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  const gb = bytes / GB
  if (gb >= 100) return String(Math.floor(gb))
  const s = gb.toFixed(1)
  return s.endsWith('.0') ? s.slice(0, -2) : s
}

/** 通用字节格式：B / KB / MB / GB / TB（1024 进制） */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = Math.max(0, bytes)
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  if (i === 0) return `${Math.round(v)} ${units[i]}`
  const s = v.toFixed(1)
  return `${s.endsWith('.0') ? s.slice(0, -2) : s} ${units[i]}`
}

/** '2026-12-13' → '12 月 13 日' */
export function formatResetDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '')
  if (!m) return iso
  return `${Number(m[2])} 月 ${Number(m[3])} 日重置`
}
