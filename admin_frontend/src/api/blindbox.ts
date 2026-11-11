/** 盲盒 API（/api/admin/blindbox*，见 backend/api/admin_blindbox.py）
 *
 * 双服通用：档位/奖池/上限/预算全部按 realm 分开；奖品六类里
 * vitality / whitelist 是公益专属奖，非公益服选用会被后端拒绝
 * 保存（前端同步给提示）。期望返还率由后端按估值表复核，
 * >100% 拒绝保存；前端的同口径试算只作即时提示。
 */
import { get, post, put } from '@/utils/request'

export type BlindboxPrizeKind =
  | 'points' | 'vitality' | 'access_days'
  | 'reg_code' | 'sub_code' | 'whitelist'

export interface BlindboxPrize {
  kind: BlindboxPrizeKind
  weight: number
  params: { amount?: number; days?: number }
}

export interface BlindboxTier {
  key: string
  name: string
  price: number
  enabled: boolean
  sort: number
  fallback_prize: BlindboxPrize
  prizes: BlindboxPrize[]
  /** 后端保存时按估值表算出的期望返还率（0–1），未保存过的新档位没有 */
  expected_return_rate?: number
}

export interface BlindboxRealmConfig {
  enabled: boolean
  starts_at: string
  ends_at: string
  daily_spend_cap: number
  access_days_daily_budget: number
  whitelist_stock_total: number
  tiers: BlindboxTier[]
}

export interface BlindboxPrizeValues {
  vitality_per_point: number
  access_day_rate: number
  whitelist: number
  access_day_values: Record<string, number>
}

export interface BlindboxConfigResponse {
  success: boolean
  master_enabled: boolean
  realm_id: number
  config: BlindboxRealmConfig
  prize_values: BlindboxPrizeValues
  whitelist_stock_remaining: number | null
}

export interface BlindboxDrawRow {
  id: number
  draw_key: string
  realm_id: number
  user_id: number
  tier_key: string
  tier_name: string
  price: number
  prize: {
    kind: BlindboxPrizeKind
    params: { amount?: number; days?: number }
    value: number
    code?: string
    fallback_from?: string
  }
  status: 'delivered' | 'delivery_failed' | 'failed' | string
  charged: boolean
  created_at: string | null
  delivered_at: string | null
  delivery: Record<string, unknown>
  error: string | null
}

export interface BlindboxStats {
  realm_id: number | null
  draws: number
  revenue: number
  payout_value: number
  actual_return_rate: number
  by_kind: Record<string, { draws: number; value: number }>
  failed_draws: number
}

export const fetchBlindboxConfig = (realmId: number) =>
  get<BlindboxConfigResponse>('/blindbox/config', { realm_id: realmId })

export const saveBlindboxConfig = (
  realmId: number,
  config: BlindboxRealmConfig,
) =>
  put<{ success: boolean; config: BlindboxRealmConfig }>('/blindbox/config', {
    realm_id: realmId,
    config,
  })

export const saveBlindboxMaster = (enabled: boolean) =>
  put<{ success: boolean; master_enabled: boolean }>('/blindbox/master', {
    enabled,
  })

export const saveBlindboxPrizeValues = (values: BlindboxPrizeValues) =>
  put<{ success: boolean; prize_values: BlindboxPrizeValues }>(
    '/blindbox/prize-values',
    { values },
  )

export const fetchBlindboxDraws = (params: {
  realm_id?: number
  user_id?: number
  status?: string
  limit?: number
  offset?: number
}) => get<{ success: boolean; total: number; items: BlindboxDrawRow[] }>(
  '/blindbox/draws',
  params,
)

export const fetchBlindboxStats = (realmId?: number) =>
  get<{ success: boolean } & BlindboxStats>(
    '/blindbox/stats',
    realmId === undefined ? undefined : { realm_id: realmId },
  )

export const redeliverBlindboxDraw = (drawId: number) =>
  post<{ success: boolean; draw_id: number; status: string }>(
    `/blindbox/draws/${drawId}/redeliver`,
  )
