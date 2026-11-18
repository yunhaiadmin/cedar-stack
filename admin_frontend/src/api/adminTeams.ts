/**
 * 团队管理 API（管理端 /api/admin/teams/*，2026-11-18 重建批 3）。
 *
 * 注意：本模块只对接批 1 落地的购买制后端——团队是钱包购买产生的
 * TeamOrder，购买后 pending_activation，管理员在这里开通播放权限。
 * #120 的意向单（team_applications）模型批 1 已不再恢复：没有申请
 * 表、没有通过/驳回端点，管理面＝团队列表＋详情＋开通＋配置。
 */
import { get, post, put } from '@/utils/request'

export type TeamStatus = 'pending_activation' | 'active' | 'dissolved'

export interface AdminTeam {
  id: number
  name: string
  owner_id: number
  owner_username: string | null
  status: TeamStatus
  plan_line: string | null
  plan_traffic_gb: number | null
  plan_traffic_bytes: number | null
  plan_member_limit: number | null
  plan_streams: number
  plan_price_fen: number | null
  plan_started_at: string | null
  plan_expires_at: string | null
  member_count: number
  created_at: string | null
}

export interface AdminTeamMember {
  id: number
  user_id: number
  username: string | null
  public_id: string | null
  role: string
  is_active: boolean
  joined_at: string | null
  expires_at: string | null
  traffic_quota_bytes: number
  traffic_used_bytes: number
  traffic_used_pct: number | null
  period_start: string
  period_end: string
  reset_date: string
}

export interface AdminTeamOrder {
  order_id: string
  team_id: number | null
  line: string
  traffic_gb: number
  months: number
  streams: number
  list_price_fen: number
  credit_fen: number
  amount_fen: number
  status: string
  created_at: string | null
}

export interface AdminTeamDetailPayload {
  team: AdminTeam
  members: AdminTeamMember[]
  orders: AdminTeamOrder[]
}

export interface AdminTeamListPayload {
  items: AdminTeam[]
  total: number
  limit: number
  offset: number
}

export function listTeams(params: {
  status?: '' | TeamStatus
  q?: string
  limit?: number
  offset?: number
}) {
  return get<AdminTeamListPayload>('/teams', {
    status: params.status || '',
    q: params.q || '',
    limit: params.limit ?? 20,
    offset: params.offset ?? 0,
  })
}

export function getTeamDetail(id: number) {
  return get<AdminTeamDetailPayload>(`/teams/${id}`)
}

export function activateTeam(id: number) {
  return post<{ team: AdminTeam }>(`/teams/${id}/activate`, {})
}

export interface TeamConfig {
  team_purchase_enabled: boolean
  /** Premium 线是否开售：false＝暂停服务（存量团队续费不受影响） */
  team_premium_enabled: boolean
  team_global_min_traffic_gb: number
  team_premium_min_traffic_gb: number
  team_max_traffic_gb: number
  team_premium_price_per_1000gb_fen: number
  /** Global 单价锚点表：[{gb, per_1000gb_fen}, …] */
  team_global_price_anchors: { gb: number; per_1000gb_fen: number }[]
  team_sub_account_divisor: number
}

export function getTeamConfig() {
  return get<{ config: TeamConfig }>('/teams/config')
}

export function updateTeamConfig(values: Partial<TeamConfig>) {
  return put<{ config: TeamConfig; saved: string[] }>('/teams/config', values)
}
