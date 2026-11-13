/**
 * 流量额度 API（门户后端 /api/user/traffic*，流量额度一期）。
 *
 * 额度未启用（套餐未声明月额度、全局默认也没配）时后端回
 * enabled=false，页面整段不展示，绝不拿 0 额度吓人。
 * 余额购买依赖钱包（未建）：后端只回 balance_purchase_available
 * 状态，**没有**余额支付端点，前端页签置灰，不许假接。
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

export interface TrafficExchangeOption {
  days: number
  bytes: number
}

export interface TrafficOverview extends TrafficSummary {
  exchange_options: TrafficExchangeOption[]
  balance_purchase_available: boolean
}

export interface TrafficExchangeResult {
  ok: boolean
  replayed: boolean
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

/** 时间换流量成交：idempotencyKey 由弹窗在打开时生成一次，同一次
 * 确认的重试/双击只成交一次（后端幂等）。失败抛错，文案走后端 detail。 */
export async function exchangeTimeForTraffic(
  days: number,
  idempotencyKey: string,
): Promise<TrafficExchangeResult> {
  return api.post<never, TrafficExchangeResult>('/api/user/traffic/exchange', {
    days,
    idempotency_key: idempotencyKey,
  })
}

const GB = 1024 ** 3

/** 大数字用：剩余量按 GB 显示（整数不带小数，否则一位小数） */
export function formatGbValue(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  const gb = bytes / GB
  if (gb >= 100) return String(Math.round(gb))
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
