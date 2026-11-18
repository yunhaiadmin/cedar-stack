/**
 * 团队 API（门户后端 /api/user/teams，2026-11-18 重建批 1 后端、批 2 前端）。
 *
 * 形态照友站：GET 详情（含创建配置）→ POST preview 结算预览 →
 * POST purchase 确认并支付（钱包余额扣款、幂等）→ 成员区
 * POST/DELETE members、PUT name。成员由团长直接创建（子账号形态，
 * 无邀请码）；购买成功后团队为 pending_activation，等管理端开通
 * 播放权限才转 active。
 *
 * 价格一律以后端 preview 返回为准，前端不自算价；金额单位为美分
 * （*_fen），展示层统一换算。业务错误体是结构化 detail
 * { code, message }，message 是后端人话（含余额差额等具体数字），
 * 用 teamErrorMessage 直接取，不走 toUserMessage 的错误码路径
 * （小写 code 会被它原样回出）。
 */
import api from '@/api'

export type TeamLine = 'global' | 'premium'

export interface TeamConfig {
  team_purchase_enabled: boolean
  team_global_min_traffic_gb: number
  team_premium_min_traffic_gb: number
  team_max_traffic_gb: number
  team_premium_price_per_1000gb_fen: number
  team_global_price_anchors: Array<{ gb: number; per_1000gb_fen: number }>
  team_sub_account_divisor: number
}

export interface TeamInfo {
  id: number
  name: string
  owner_id: number
  owner_username: string | null
  /** pending_activation 待开通播放权限 / active 已开通 */
  status: string
  plan_line: TeamLine | string | null
  plan_traffic_gb: number | null
  plan_traffic_bytes: number | null
  plan_member_limit: number | null
  plan_streams: number | null
  plan_price_fen: number | null
  plan_started_at: string | null
  plan_expires_at: string | null
  member_count: number
  created_at: string | null
}

export interface TeamDetail {
  config: TeamConfig
  team: TeamInfo | null
  my_role: 'owner' | 'member' | null
}

export type TeamMemberRow = {
  id: number
  user_id: number
  username: string | null
  public_id: string | null
  role: string
  is_active: boolean
  joined_at: string | null
  expires_at: string | null
  traffic_quota_bytes: number
  /** 本周期已用字节：团队行由后端计量；家庭版成员行无分账用量，恒为 null（页面显示 '—'） */
  traffic_used_bytes: number | null
  traffic_used_pct: number | null
  period_start: string
  period_end: string
  reset_date: string
}

export interface TeamMembersPage {
  items: TeamMemberRow[]
  total: number
  page: number
  page_size: number
}

export interface TeamPreview {
  line: TeamLine
  traffic_gb: number
  traffic_bytes: number
  months: number
  unit_price_fen_per_1000gb: number
  monthly_price_fen: number
  list_price_fen: number
  switch_credit_fen: number
  switch_type: string
  total_fen: number
  sub_account_limit: number
  streams: number
  purchase_enabled: boolean
  balance_fen: number
  balance_sufficient: boolean
}

export interface TeamOrder {
  order_id: string
  team_id: number | null
  line: TeamLine
  traffic_gb: number
  months: number
  streams: number
  list_price_fen: number
  credit_fen: number
  amount_fen: number
  status: string
  created_at: string | null
}

export interface TeamPurchaseResult {
  order: TeamOrder
  team: TeamInfo | null
  balance_after_fen: number
}

export const teamsApi = {
  /** 团队详情＋创建配置（无团队时 team=null） */
  detail: () => api.get<never, TeamDetail>('/api/user/teams'),

  /** 成员列表：按名称搜索、状态筛选（all/active/disabled）、分页 */
  members: (params?: { q?: string; status?: string; page?: number; page_size?: number }) =>
    api.get<never, TeamMembersPage>('/api/user/teams/members', { params }),

  /** 结算预览：套餐费/旧套餐抵扣/总计/子账户数（不落库） */
  preview: (data: { line: TeamLine; traffic_gb: number; months: number; streams: number }) =>
    api.post<never, TeamPreview>('/api/user/teams/preview', data),

  /** 确认并支付：钱包余额扣款；idempotency_key 同一次确认只生成一个 */
  purchase: (data: {
    line: TeamLine
    traffic_gb: number
    months: number
    streams: number
    name?: string
    idempotency_key: string
  }) => api.post<never, TeamPurchaseResult>('/api/user/teams/purchase', data),

  /** 团长创建成员子账号（用户名 3–32 位字母/数字/下划线，密码至少 6 位） */
  createMember: (data: { username: string; password: string }) =>
    api.post<never, TeamMemberRow>('/api/user/teams/members', data),

  /** 移除成员：删归属行＋软禁用账号 */
  removeMember: (userId: number) =>
    api.delete<never, { removed: boolean; user_id: number }>(
      `/api/user/teams/members/${userId}`),

  /** 修改团队名（仅团长） */
  rename: (name: string) =>
    api.put<never, { team: TeamInfo }>('/api/user/teams/name', { name }),
}

/** 线路展示名（照友站创建弹窗的两钮文案） */
export const TEAM_LINE_NAMES: Record<TeamLine, string> = {
  global: 'Global 全球线路',
  premium: 'Premium 高级线路',
}

/** 美分 → `$ 30.00`（与订阅页 moneyText 同形） */
export function formatFen(fen: number | null | undefined): string {
  if (fen == null || !Number.isFinite(fen)) return '–'
  const v = fen / 100
  return `$ ${v.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** 十进制 GB → 展示（10000 →「10 TB」，500 →「500 GB」；与后端计价的
 * 十进制口径一致，友站输入框旁换算同为 20000 →「20 TB」） */
export function formatGb(gb: number | null | undefined): string {
  if (gb == null || !Number.isFinite(gb)) return '–'
  if (gb >= 1000) {
    const tb = gb / 1000
    return `${Number.isInteger(tb) ? tb : tb.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')} TB`
  }
  return `${gb} GB`
}

/** ISO 日期 → YYYY.MM.DD（友站会员卡日期形）；空值回 '—' */
export function formatTeamDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = iso.slice(0, 10)
  return d.length === 10 ? d.replace(/-/g, '.') : iso
}

/**
 * 团队业务错误的人话：优先后端结构化 detail.message（含余额差额、
 * 起订下限等具体数字，必须原样到达用户），其次 detail 字符串，
 * 都没有才用调用方兜底。
 */
export function teamErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message.trim()
  }
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  const userMessage = (err as { userMessage?: string })?.userMessage
  if (userMessage && userMessage.trim()) return userMessage.trim()
  return fallback
}
