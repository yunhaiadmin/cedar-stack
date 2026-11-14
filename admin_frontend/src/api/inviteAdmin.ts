/** 邀请总览 API（全部走 /api/admin/invite/*，2026-11-14 邀请全套单新增）
 * 返利比例/模式/分档阈值等钱包键在「系统设置 → 钱包」里改（钱包批 1），
 * 本文件只管总览、返利明细、补入账与福利邀请（送试用）策略。 */
import { get, post, put } from '@/utils/request'

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

export const fetchInviteOverview = () => get<InviteOverview>(`${E}/overview`)

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
