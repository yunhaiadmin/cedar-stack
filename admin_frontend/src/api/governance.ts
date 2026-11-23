/**
 * 活动治理·管理端 API（Step 2：举报终审队列 + 治理停权）。
 *
 * 端点与后端逐字对应：backend/api/governance_report.py 的 admin_router
 * （前缀 /api/admin/governance；axios baseURL 已是 /api/admin，这里只写余下部分）。
 * 鉴权走 get_current_admin：只读审计（viewer）角色的写请求会被服务端 403，
 * 页面据 auth.admin.can_write 置灰，服务端仍自己判一次。
 *
 * 注意：这里的「停权」是 Step 2 的 GovernanceSuspension（恶意举报自动触发 / 手动），
 * 与 Step 4 的 GovernanceFreeze（/governance/freeze）是两套独立机制。
 */
import { get, post } from '@/utils/request'

const G = '/governance'

/** 举报状态：与 backend/governance_report.py 的 REPORT_STATUS_* 逐字一致 */
export type GovernanceReportStatus =
  | 'pending'
  | 'escalated'
  | 'accepted'
  | 'rejected'
  | 'review_rejected'

/** 举报原因：与后端 REPORT_REASONS 逐字一致 */
export type GovernanceReportReason = 'spam' | 'duplicate' | 'violation'

/** 终审决定：与后端 DecideIn.decision 一致 */
export type GovernanceDecision = 'accepted' | 'rejected'

/** 举报行（后端 _report_dict + _vote_stats） */
export interface GovernanceReportRow {
  id: number
  reporter_id: number
  /** v1 只有 media_seek（求片条目） */
  target_type: string
  target_id: number
  reason: GovernanceReportReason | string
  detail: string | null
  status: GovernanceReportStatus | string
  admin_note: string | null
  admin_id: number | null
  escalated_at: string | null
  decided_at: string | null
  created_at: string | null
  updated_at: string | null
  /** 复核票数（按人数） */
  ballots: number
  /** 同意侧权重合计 */
  agree_weight: number
  /** 不同意侧权重合计 */
  disagree_weight: number
}

/** 复核票（仅管理端详情返回） */
export interface GovernanceReviewVote {
  id: number
  reviewer_id: number
  decision: 'agree' | 'disagree' | string
  weight: number
  created_at: string | null
}

export interface GovernanceReportDetail extends GovernanceReportRow {
  votes: GovernanceReviewVote[]
}

/** 停权行（后端 _suspension_dict） */
export interface GovernanceSuspensionRow {
  id: number
  user_id: number
  /** auto_abuse（终审驳回计数自动触发）/ manual（管理员手动） */
  reason: 'auto_abuse' | 'manual' | string
  note: string | null
  /** 自动停权为 null */
  created_by_id: number | null
  starts_at: string | null
  ends_at: string | null
  revoked_at: string | null
  revoked_by_id: number | null
  created_at: string | null
}

/** 终审结果：驳回可能顺带触发举报人的自动停权 */
export interface GovernanceDecideResult extends GovernanceReportRow {
  auto_suspension?: GovernanceSuspensionRow
}

// ==================== 举报终审 ====================

/** 举报列表：不传 status 时后端只列终审队列（escalated，最早在前） */
export const fetchGovernanceReports = (params: { status?: string; limit?: number } = {}) =>
  get<GovernanceReportRow[]>(`${G}/reports/queue`, params)

/** 举报详情（含全部复核票） */
export const fetchGovernanceReport = (id: number) =>
  get<GovernanceReportDetail>(`${G}/reports/${id}`)

/** 终审：受理 accepted / 驳回 rejected（仅 escalated 可判，否则 409） */
export const decideGovernanceReport = (
  id: number,
  data: { decision: GovernanceDecision; note?: string },
) => post<GovernanceDecideResult>(`${G}/reports/${id}/decide`, data)

// ==================== 治理停权 ====================

/** 停权列表（最新在前） */
export const fetchGovernanceSuspensions = (
  params: { user_id?: number; active_only?: boolean; limit?: number } = {},
) => get<GovernanceSuspensionRow[]>(`${G}/suspensions`, params)

/** 手动停权：冻结治理特权（举报／复核），不扣经验、不降级；已有生效中停权 409 */
export const createGovernanceSuspension = (data: { user_id: number; days: number; note?: string }) =>
  post<GovernanceSuspensionRow>(`${G}/suspensions`, data)

/** 撤销停权（仅生效中的可撤销，否则 409） */
export const revokeGovernanceSuspension = (id: number) =>
  post<GovernanceSuspensionRow>(`${G}/suspensions/${id}/revoke`)

// ==================== 治理冻结（Step 4，O-02 补缺） ====================
/**
 * 端点：backend/api/governance_admin.py，挂 admin_router
 * （前缀 /api/admin，这里只写余下部分）。
 *
 * 与 Step 2「治理停权」（GovernanceSuspension）是两套独立机制：
 * 冻结（GovernanceFreeze）冻的是治理特权（举报 / 复核等治理参与权），
 * 不扣经验、不影响核心权益。写操作双写审计（管理审计 + 治理审计日志）。
 */
export interface GovernanceFreezeRow {
  id: number
  user_id: number
  username: string | null
  reason: string
  /** 空 = 不限期 */
  expires_at: string | null
  created_by: number | null
  created_by_name: string | null
  created_at: string | null
}

export interface GovernanceAuditLogRow {
  id: number
  action: string
  actor_user_id: number | null
  actor_name: string | null
  target_user_id: number | null
  target_name: string | null
  target_ref: string | null
  detail: Record<string, unknown>
  created_at: string | null
}

/** 当前生效中的冻结列表（未解封且未过期，最新在前） */
export const fetchGovernanceFreezes = () =>
  get<{ items: GovernanceFreezeRow[] }>(`${G}/freezes`)

/** 新建冻结：理由必填；已有生效中冻结 409；days 空 = 不限期 */
export const createGovernanceFreeze = (data: { user_id: number; reason: string; days?: number | null }) =>
  post<{ success: boolean; freeze_id: number; expires_at: string | null }>(`${G}/freeze`, data)

/** 解冻（按用户）：无生效中冻结 404 */
export const releaseGovernanceFreeze = (userId: number) =>
  post<{ success: boolean; freeze_id: number }>(`${G}/freeze/release`, { user_id: userId })

/** 治理审计日志只读查询（无写接口） */
export const fetchGovernanceAuditLogs = (params: { limit?: number; action?: string; actor_id?: number; target_id?: number } = {}) =>
  get<{ items: GovernanceAuditLogRow[] }>(`${G}/audit-logs`, params)

// ==================== 可见性治理复核（L7，O-02 补缺） ====================
/**
 * 端点：backend/api/admin_media_governance.py，挂 admin_emby_router
 * （前缀 /api/admin/emby，这里只写余下部分）。
 *
 * 硬语义：准入/驳回只追加决策行与事件、留处置痕迹，**不改变任何露出
 * 结果**——条目露不露永远由案 A 判定函数现算。页面文案必须如实说明。
 */
const VG = '/emby/visibility-governance'

export type VisibilityTaskStatus = 'pending' | 'approved' | 'rejected'

export interface VisibilityTaskRow {
  id: number
  item_id: number
  item_name: string
  /** shelf 货架 / search 搜索 / direct 直达 */
  surface: string
  status: VisibilityTaskStatus | string
  reason: string | null
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string | null
}

export interface VisibilityEventRow {
  id: number
  task_id: number | null
  item_id: number | null
  event_type: string
  actor: number | null
  detail: Record<string, unknown>
  created_at: string | null
}

export interface VisibilityPage<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** 复核队列分页列表（最新在前；status 可筛 pending/approved/rejected） */
export const fetchVisibilityTasks = (params: { status?: string; page?: number; page_size?: number } = {}) =>
  get<VisibilityPage<VisibilityTaskRow>>(`${VG}/tasks`, params)

/** 准入（仅 pending 可判，否则 409）；只留痕，不改变露出结果 */
export const approveVisibilityTask = (id: number, note = '') =>
  post<VisibilityTaskRow>(`${VG}/tasks/${id}/approve`, { note })

/** 驳回（仅 pending 可判，否则 409）；只留痕，不改变露出结果 */
export const rejectVisibilityTask = (id: number, note = '') =>
  post<VisibilityTaskRow>(`${VG}/tasks/${id}/reject`, { note })

/** 事件流水只读列表（最新在前；item_id 可筛某条目） */
export const fetchVisibilityEvents = (params: { item_id?: number; page?: number; page_size?: number } = {}) =>
  get<VisibilityPage<VisibilityEventRow>>(`${VG}/events`, params)
