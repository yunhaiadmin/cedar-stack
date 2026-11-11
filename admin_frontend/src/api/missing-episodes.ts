/** 缺集清单 API（/api/admin/missing-episodes*，见 backend/api/admin_missing_episodes.py） */
import { get, put } from '@/utils/request'

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

/** 补集通道配置（B 方案）：token 是密钥，出口只回「是否已设置」，不回原文。 */
export interface MpFillConfig {
  mp_download_base: string
  mp_download_token_set: boolean
  mp_episode_fill_enabled: boolean
  mp_episode_fill_daily_cap: number
  mp_episode_fill_cooldown_hours: number
}

export interface MpFillConfigSave {
  mp_download_base?: string
  /** 留空 = 不修改已配的 token */
  mp_download_token?: string
  mp_episode_fill_enabled?: boolean
  mp_episode_fill_daily_cap?: number
  mp_episode_fill_cooldown_hours?: number
}

export const fetchMpFillConfig = () =>
  get<MpFillConfig>('/missing-episodes/fill-config')

export const saveMpFillConfig = (payload: MpFillConfigSave) =>
  put<MpFillConfig>('/missing-episodes/fill-config', payload)
