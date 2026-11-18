/** 群抽奖/机器人配置管理后台 API（全部走 /api/admin/welfare/*，历史路径名） */
import { del, get, post, put } from '@/utils/request'

const E = '/welfare'

// 公益用户操作（grant/revoke/bulk-extend）已随公益资格整族于 2026-11-14 删除。

// ==================== 抽奖 ====================
// 注：求片审核已并入「求片管理」（MediaSeek.vue），welfare.ts 不再保留求片 API。

// ==================== 配置 ====================

export const fetchWelfareConfig = () =>
  get<Record<string, string>>(`${E}/config`)

export const saveWelfareConfig = (data: Record<string, string>) =>
  put<{ success: boolean; updated: string[] }>(`${E}/config`, data)

// ==================== 群抽奖活动（G2） ====================
export type LotteryRoundStatus = 'open' | 'drawing' | 'done' | 'cancelled'

export interface LotteryRoundRow {
  id: number
  title: string
  chat_id: string
  status: LotteryRoundStatus
  participant_count: number
  prize_count: number
  draw_at: string | null
  created_at: string | null
  // 公告落库状态（2026-10-31）：sent=已发 / failed=发送失败（带原因，可补发）/ null=未发送
  announce_status?: string | null
  announce_message_id?: number | null
  announce_error?: string | null
}

export interface LotteryRoundPrizeRow {
  id: number
  round_id: number
  name: string
  type: 'days' | 'whitelist'
  value: number
  quantity: number
  sort: number
}

export interface LotteryRoundEntryRow {
  id: number
  user_id: number | null
  telegram_id: string | null
  joined_at: string | null
}

export interface LotteryRoundWinnerRow {
  id: number
  prize_id: number
  prize_name: string
  user_id: number | null
  telegram_id: string | null
  distributed: boolean
  distributed_at: string | null
}

export interface LotteryRoundDetail {
  round: Record<string, unknown>
  prizes: LotteryRoundPrizeRow[]
  entries: LotteryRoundEntryRow[]
  winners: LotteryRoundWinnerRow[]
  verify: Record<string, unknown> | null
}

export interface CreateRoundPayload {
  title: string
  chat_id: number
  prizes: Array<{
    name: string
    type: 'days' | 'whitelist'
    value: number
    quantity: number
  }>
  draw_at?: string | null
  max_participants?: number | null
}

export const fetchLotteryRounds = (params: { page?: number; page_size?: number; status?: LotteryRoundStatus } = {}) =>
  get<{ total: number; items: LotteryRoundRow[] }>(`${E}/lottery/rounds`, params)

export const createLotteryRound = (data: CreateRoundPayload) =>
  post<Record<string, unknown>>(`${E}/lottery/rounds`, data)

export const fetchLotteryRound = (id: number) =>
  get<LotteryRoundDetail>(`${E}/lottery/rounds/${id}`)

export const drawLotteryRound = (id: number) =>
  post<{ success: boolean; winners: unknown[]; distribute: unknown }>(`${E}/lottery/rounds/${id}/draw`, {})

export const cancelLotteryRound = (id: number) =>
  post<{ success: boolean }>(`${E}/lottery/rounds/${id}/cancel`, {})

export const resendLotteryAnnounce = (id: number) =>
  post<{ success: boolean; announce_status: string | null; announce_error: string | null }>(`${E}/lottery/rounds/${id}/announce/resend`, {})

// ==================== 抽奖黑名单（三重门·资格门） ====================
// 被拉黑的用户无法参加任何群抽奖（lottery.is_blacklisted 在参加时拦截）。
// 增删都以站内用户 ID（web_users.id）为准；移除按 user_id，不是黑名单行 id。
export interface LotteryBlacklistRow {
  id: number
  user_id: number
  username: string | null
  reason: string | null
  created_at: string | null
}

export const fetchLotteryBlacklist = (params: { page?: number; page_size?: number } = {}) =>
  get<{ total: number; page: number; page_size: number; items: LotteryBlacklistRow[] }>(`${E}/lottery/blacklist`, params)

export const addLotteryBlacklist = (data: { user_id: number; reason?: string }) =>
  post<{ success: boolean; id: number }>(`${E}/lottery/blacklist`, { user_id: data.user_id, reason: data.reason ?? '' })

export const removeLotteryBlacklist = (userId: number) =>
  del<{ success: boolean }>(`${E}/lottery/blacklist/${userId}`)
