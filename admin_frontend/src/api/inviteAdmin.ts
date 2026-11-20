/** 邀请总览 API（全部走 /api/admin/invite/*，2026-11-14 邀请全套单新增）
 * 返利比例/模式/分档阈值等钱包键在「系统设置 → 钱包」里改（钱包批 1），
 * 本文件只管总览、返利明细、补入账与福利邀请（送试用）策略。
 * 2026-11-20 补推广者（AFF）汇总/档案/导出（/promoters*）。 */
import { get, getBlob, post, put } from '@/utils/request'

const E = '/invite'

export interface InviteRebateConfig {
  mode: 'flat' | 'tiered'
  percent: number
  cap_fen: number
  tier_threshold_fen: number
  tier_percent: number
}

export interface InviteTrialPolicy {
  enabled: boolean
  days: number
  monthly_limit: number
  min_paid_fen: number
  active: boolean
}

export interface InviteOverview {
  relations: number
  inviters: number
  paid_invitees: number
  conversion_rate: number
  rebate: { credited: string; pending: string; pending_count: number }
  trial_grants: number
  rebate_config: InviteRebateConfig
  trial_policy: InviteTrialPolicy
  burst_alerts: { inviter_id: number; inviter_username: string; invited_7d: number }[]
  burst_rule: string
}

export interface InviteRebateRow {
  id: number
  inviter_id: number
  inviter_username: string
  invitee_id: number
  invitee_username: string
  source: string
  source_ref: string
  paid_amount: string
  rebate_amount: string
  percent: number
  status: 'pending' | 'credited'
  created_at: string | null
  credited_at: string | null
}

/** 福利试用码（2026-11-15 生成码机制）：管理端只读列表行 */
export interface InviteTrialCodeRow {
  id: number
  code: string
  creator_id: number
  creator_username: string | null
  plan_id: number | null
  days: number
  status: 'pending' | 'used' | 'expired'
  used_by: number | null
  used_by_username: string | null
  created_at: string | null
  used_at: string | null
  expires_at: string | null
}

export const fetchInviteOverview = () => get<InviteOverview>(`${E}/overview`)

export const fetchInviteTrialCodes = (params?: { limit?: number; status?: string }) =>
  get<{ total: number; codes: InviteTrialCodeRow[] }>(`${E}/trial-codes`, params)

export const fetchInviteRebates = (params?: { limit?: number; status?: string }) =>
  get<{ total: number; rebates: InviteRebateRow[] }>(`${E}/rebates`, params)

export const settleInviteRebates = () =>
  post<{ settled: number; pending_remaining: number }>(`${E}/rebates/settle`)

export const fetchTrialPolicy = () =>
  get<{ policy: InviteTrialPolicy }>(`${E}/trial-policy`)

export const updateTrialPolicy = (data: {
  invite_trial_enabled?: boolean
  invite_trial_days?: number
  invite_welfare_monthly_limit?: number
  invite_welfare_min_paid_fen?: number
}) => put<{ success: boolean; applied: Record<string, string>; policy: InviteTrialPolicy }>(`${E}/trial-policy`, data)

// ==================== 推广者（AFF） ====================

/** 推广者汇总行：金额为美元字符串（两位小数），口径见后端 invite_promoter */
export interface InvitePromoterRow {
  user_id: number
  username: string
  invited_count: number
  paid_invitees: number
  total_paid: string
  rebate_credited: string
  rebate_pending: string
  rebate_total: string
  /** 当前生效费率（%）：专属覆盖优先，否则全局基础比例 */
  rate_percent: number
  rate_source: 'promoter' | 'global'
  rate_mode: 'flat' | 'tiered'
  rebate_percent_override: number | null
  is_disabled: boolean
  note: string
  updated_at: string | null
}

export interface PromoterQuery {
  search?: string
  sort?: string
  order?: string
  page?: number
  page_size?: number
  [key: string]: unknown
}

export const fetchInvitePromoters = (params: PromoterQuery = {}) =>
  get<{ total: number; page: number; page_size: number; promoters: InvitePromoterRow[] }>(
    `${E}/promoters`, params)

export const updateInvitePromoter = (
  userId: number,
  data: { rebate_percent_override: number | null; is_disabled: boolean; note: string },
) => put<{ success: boolean; promoter: InvitePromoterRow }>(`${E}/promoters/${userId}`, data)

/** 汇总 CSV 导出：按当前筛选/排序导出全部（getBlob 不带 params，手拼查询串） */
export const exportInvitePromotersCsv = (params: PromoterQuery = {}) => {
  const qs = new URLSearchParams()
  if (params.search) qs.set('search', params.search)
  if (params.sort) qs.set('sort', params.sort)
  if (params.order) qs.set('order', params.order)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return getBlob(`${E}/promoters/export${suffix}`)
}
