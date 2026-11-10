/** 缺集清单 API（/api/admin/missing-episodes*，见 backend/api/admin_missing_episodes.py） */
import { get } from '@/utils/request'

export interface MissingEpisodeRow {
  series_item_id: number
  series_name: string
  tmdb_id: string
  library_id: number | null
  library_name: string
  /** {季号: [缺的集号]} */
  missing_detail: Record<string, number[]>
  missing_count: number
  /** 大白话文案，如「第 1 季缺第 2、5 集」 */
  missing_text: string
  first_detected_at: string | null
  last_checked_at: string | null
  /** 求补人数（发起人＋附议人，与用户端同一数据源） */
  requesters: number
}

export interface MissingEpisodesSummary {
  open_series: number
  total_missing: number
  skipped_no_tmdb: number
  libraries: { id: number; name: string; open_series: number }[]
  worker: {
    running?: boolean
    enabled?: boolean
    last_round?: Record<string, unknown> | null
  } | null
}

export const fetchMissingEpisodes = (params: {
  limit?: number
  offset?: number
  sort?: 'missing_count' | 'first_detected_at'
  q?: string
  library_id?: number
}) => get<{ total: number; items: MissingEpisodeRow[] }>('/missing-episodes', params)

export const fetchMissingEpisodesSummary = () =>
  get<MissingEpisodesSummary>('/missing-episodes/summary')
