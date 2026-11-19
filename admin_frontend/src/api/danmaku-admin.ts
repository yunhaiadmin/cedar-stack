/** 弹幕内容治理 API（/api/admin/danmaku/comments|mutes，见 backend/api/admin_danmaku.py）
 * 与弹幕设置（api/admin.ts 的 fetchDanmakuConfig，走 /danmaku/config）分工：
 * 这里是治理面——弹幕列表/删除、用户禁言/解禁；设置面一字不动。 */
import { del, get, post } from '@/utils/request'

export interface DanmakuCommentRow {
  id: number
  user_id: number
  username: string
  item_id: number
  item_name: string
  item_guid: string
  position_seconds: number
  text: string
  created_at: string | null
}

export interface DanmakuMuteRow {
  id: number
  user_id: number
  username: string
  /** 到期时间；null = 永久禁言 */
  muted_until: string | null
  permanent: boolean
  /** 是否生效中（已自然到期的行为 false） */
  active: boolean
  reason: string
  created_by: number | null
  created_at: string | null
  updated_at: string | null
}

export const fetchDanmakuComments = (params: {
  limit?: number
  offset?: number
  item_id?: number
  user_id?: number
  keyword?: string
}) => get<{ total: number; items: DanmakuCommentRow[] }>('/danmaku/comments', params)

export const deleteDanmakuComment = (commentId: number) =>
  del<{ success: boolean; comment_id: number }>(`/danmaku/comments/${commentId}`)

export const fetchDanmakuMutes = (params: { limit?: number; offset?: number }) =>
  get<{ total: number; items: DanmakuMuteRow[] }>('/danmaku/mutes', params)

export const createDanmakuMute = (data: {
  user_id: number
  permanent: boolean
  duration_hours?: number | null
  reason?: string
}) => post<DanmakuMuteRow>('/danmaku/mutes', data)

export const deleteDanmakuMute = (userId: number) =>
  del<{ success: boolean; user_id: number }>(`/danmaku/mutes/${userId}`)
