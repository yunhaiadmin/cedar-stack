/**
 * 团队套餐申请审核 API（管理端 /api/admin/teams/*，2026-11-13）。
 *
 * 用户提交团队套餐申请（意向单）→ 管理员在这里通过/驳回；通过即给团登记
 * 套餐（plan_status=active）。本站不接支付、不做流量计量，审核通过只是
 * 「人工审核流」的开通登记，付款与开通细节走客服线下确认。
 * 团队配置（起订流量/子账号上限/未开通套餐成员上限）同走本模块读写。
 */
import { get, post, put } from '@/utils/request'

export interface AdminTeamApplication {
  id: number
  user_id: number
  username: string
  team_id: number | null
  team_name: string | null
  line: string | null
  traffic_bytes: number
  member_limit: number
  note: string
  status: 'pending' | 'approved' | 'rejected'
  review_note: string
  reviewed_by: number | null
  reviewed_at: string | null
  created_at: string | null
}

export interface AdminTeamApplicationsPayload {
  total: number
  applications: AdminTeamApplication[]
}

export function listTeamApplications(params: {
  status?: '' | 'pending' | 'approved' | 'rejected'
  limit?: number
}) {
  return get<AdminTeamApplicationsPayload>('/teams/applications', {
    status: params.status || 'all',
    limit: params.limit ?? 100,
  })
}

export function approveTeamApplication(id: number, reviewNote?: string) {
  return post(`/teams/applications/${id}/approve`, {
    review_note: reviewNote || '',
  })
}

export function rejectTeamApplication(id: number, reviewNote?: string) {
  return post(`/teams/applications/${id}/reject`, {
    review_note: reviewNote || '',
  })
}

export interface TeamConfig {
  /** 套餐起订总流量（字节，十进制） */
  team_plan_min_traffic_bytes: number
  /** 套餐子账号（成员）数上限 */
  team_plan_max_members: number
  /** 未开通套餐的团队成员上限 */
  team_default_member_limit: number
}

export function getTeamConfig() {
  return get<TeamConfig>('/teams/config')
}

export function updateTeamConfig(values: Partial<TeamConfig>) {
  return put<TeamConfig>('/teams/config', values)
}
