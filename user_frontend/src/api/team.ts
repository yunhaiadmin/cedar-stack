/**
 * 团队 API（门户后端 /api/user/teams/*，2026-11-13 照 UHDNow 团队形态）。
 *
 * 一人只能在一个团队；套餐走申请意向单→管理端审核开通。后端不接支付、
 * 不做流量计量（本站没有流量额度模型），plan_* 只是套餐登记值。
 */
import api from '@/api'

export interface TeamConfig {
  /** 套餐起订总流量（字节，十进制） */
  team_plan_min_traffic_bytes: number
  /** 套餐子账号（成员）数上限 */
  team_plan_max_members: number
  /** 未开通套餐的团队成员上限 */
  team_default_member_limit: number
}

export interface TeamMemberRow {
  user_id: number
  username: string
  role: 'owner' | 'member'
  joined_at: string | null
}

export interface MyTeam {
  id: number
  name: string
  invite_code: string
  owner_id: number
  owner_username: string
  my_role: 'owner' | 'member'
  member_count: number
  member_limit: number
  plan_status: 'none' | 'pending' | 'active'
  plan_line: string | null
  plan_traffic_bytes: number | null
  plan_member_limit: number | null
  created_at: string | null
  members: TeamMemberRow[]
}

export interface TeamApplication {
  id: number
  team_id: number | null
  team_name: string | null
  line: string | null
  traffic_bytes: number
  member_limit: number
  note: string
  status: 'pending' | 'approved' | 'rejected'
  review_note: string
  created_at: string | null
  reviewed_at: string | null
}

export interface MyTeamsPayload {
  team: MyTeam | null
  config: TeamConfig
  applications: TeamApplication[]
}

export interface TeamApplicationPayload {
  team_name?: string | null
  line?: string | null
  traffic_bytes: number
  member_limit: number
  note?: string | null
}

export const teamApi = {
  config: () => api.get<never, TeamConfig>('/api/user/teams/config'),
  mine: () => api.get<never, MyTeamsPayload>('/api/user/teams/mine'),
  create: (name: string) =>
    api.post<never, { team: MyTeam }>('/api/user/teams/create', { name }),
  join: (inviteCode: string) =>
    api.post<never, { team: MyTeam }>('/api/user/teams/join', {
      invite_code: inviteCode,
    }),
  leave: () => api.post<never, { left: boolean }>('/api/user/teams/leave'),
  dissolve: () =>
    api.post<never, { dissolved: boolean }>('/api/user/teams/dissolve'),
  removeMember: (userId: number) =>
    api.post<never, { removed: boolean }>(
      `/api/user/teams/members/${userId}/remove`,
    ),
  apply: (payload: TeamApplicationPayload) =>
    api.post<never, { application: TeamApplication }>(
      '/api/user/teams/applications',
      payload,
    ),
}
