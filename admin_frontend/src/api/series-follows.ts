/** 追剧订阅管理 API（/api/admin/series-follows*，见 backend/api/admin_series_follows.py） */
import { get, post } from '@/utils/request'

export interface SeriesFollowRow {
  id: number
  user_id: number
  username: string
  series_item_id: number
  series_name: string
  series_year: number | null
  status: 'active' | 'cancelled'
  created_at: string | null
  updated_at: string | null
}

export const fetchSeriesFollows = (params: {
  limit?: number
  offset?: number
  status?: string
  user_id?: number
  q?: string
}) => get<{ total: number; items: SeriesFollowRow[] }>('/series-follows', params)

export const cancelSeriesFollow = (followId: number) =>
  post<{ success: boolean; reason: string; follow_id: number; status: string }>(
    `/series-follows/${followId}/cancel`)
