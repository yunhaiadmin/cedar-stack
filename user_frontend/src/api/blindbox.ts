/**
 * 盲盒 API（门户后端 /api/user/blindbox/*，PR-2）。
 *
 * 双服通用：realm_id 不传时后端默认当前服。总开关关或该服未开启
 * 时 tiers 回 enabled=false——补给站入口据此整块不渲染（零感知）。
 * 概率购买前全公布（prizes[].probability 为 0–1 的占比）。
 */
import api from '@/api'

export type BlindboxPrizeKind =
  | 'points' | 'vitality' | 'access_days'
  | 'reg_code' | 'sub_code' | 'whitelist'

export interface BlindboxPrizeInfo {
  kind: BlindboxPrizeKind
  params: { amount?: number; days?: number }
  probability: number
  value: number
}

export interface BlindboxTierInfo {
  key: string
  name: string
  price: number
  expected_return_rate: number
  prizes: BlindboxPrizeInfo[]
  fallback_prize: { kind: BlindboxPrizeKind; params: { amount?: number; days?: number } }
}

export interface BlindboxTiersResponse {
  realm_id: number
  enabled: boolean
  starts_at: string
  ends_at: string
  daily_spend_cap: number
  whitelist_stock_remaining: number | null
  tiers: BlindboxTierInfo[]
}

export interface BlindboxPrizeResult {
  kind: BlindboxPrizeKind
  params: { amount?: number; days?: number }
  value: number
  code?: string
  fallback_from?: string
}

export interface BlindboxDrawResponse {
  draw_id: number
  draw_key: string
  tier_key: string
  price: number
  prize: BlindboxPrizeResult
  balance: number | null
}

export interface BlindboxDrawRecord {
  id: number
  draw_key: string
  realm_id: number
  tier_key: string
  tier_name: string
  price: number
  prize: BlindboxPrizeResult
  status: string
  charged: boolean
  created_at: string | null
  delivered_at: string | null
}

export interface BlindboxCodeItem {
  id: number
  code: string
  code_type: number
  days: number
  used: boolean
  is_active: boolean
  created_at: string | null
  expires_at: string | null
}

export const fetchBlindboxTiers = (realmId?: number) =>
  api.get<never, BlindboxTiersResponse>('/api/user/blindbox/tiers', {
    params: realmId === undefined ? undefined : { realm_id: realmId },
  })

export const drawBlindbox = (tierKey: string, realmId?: number) =>
  api.post<never, BlindboxDrawResponse>('/api/user/blindbox/draw', {
    tier_key: tierKey,
    ...(realmId === undefined ? {} : { realm_id: realmId }),
  })

export const fetchBlindboxMyDraws = (limit = 20) =>
  api.get<never, { total: number; items: BlindboxDrawRecord[] }>(
    '/api/user/blindbox/my-draws',
    { params: { limit } },
  )

export const fetchBlindboxMyCodes = () =>
  api.get<never, { items: BlindboxCodeItem[] }>('/api/user/blindbox/my-codes')
