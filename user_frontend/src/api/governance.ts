/**
 * 活动治理·用户端 API（Step 2 举报）。
 * 端点与后端逐字对应：backend/api/governance_report.py（/api/user/governance）。
 * 停权判定一律以服务端为准，前端只展示与明示，不在本地算权限。
 * （Step 3 活动提案/直接发起已随公益服删除于 2026-11-19 下线，客户端一并摘除。）
 */
import api from './index'

/** 举报原因：与后端 REPORT_REASONS 逐字一致 */
export type GovernanceReportReason = 'spam' | 'duplicate' | 'violation'

export interface GovernanceReport {
  id: number
  reporter_id: number
  target_type: string
  target_id: number
  reason: string
  detail: string | null
  status: string
}

export const governanceApi = {
  /** 举报一条求片（同一用户对同一条目终身只能举报 1 次，重复报 409） */
  createReport: (targetId: number, reason: GovernanceReportReason, detail?: string) =>
    api.post<never, GovernanceReport>('/api/user/governance/reports', {
      target_id: targetId,
      reason,
      ...(detail ? { detail } : {}),
    }),
}
