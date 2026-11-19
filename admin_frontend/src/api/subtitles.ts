/** 字幕管理只读盘点 API（/api/admin/subtitles/*，见 backend/api/admin_subtitles.py） */
import { get } from '@/utils/request'

export interface SubtitleItemRow {
  id: number
  guid: string
  name: string
  /** movie / episode */
  item_type: string
  /** 集所属剧名（电影为空串） */
  series_name: string
  season_number: number | null
  episode_number: number | null
  production_year: number | null
  /** 已入库字幕轨总数（内嵌 + 外挂） */
  subtitle_count: number
  external_count: number
  embedded_count: number
}

export interface SubtitleTrack {
  id: number
  stream_index: number
  /** embedded 内嵌轨 / external 外挂文件 */
  source: 'embedded' | 'external'
  codec: string
  language: string
  title: string
  is_default: boolean
  is_forced: boolean
  /** 仅外挂轨有值：本机路径或 mount:// 地址 */
  external_path: string
}

export interface SubtitleItemDetail extends SubtitleItemRow {
  tracks: SubtitleTrack[]
}

export interface SubtitleListResponse {
  total: number
  items: SubtitleItemRow[]
}

export const fetchSubtitleItems = (params: {
  limit?: number
  offset?: number
  q?: string
  /** '' 全部 / 'with' 有字幕 / 'without' 无字幕 */
  subtitle_filter?: string
}) => get<SubtitleListResponse>('/subtitles/items', params)

export const fetchSubtitleItem = (id: number) =>
  get<SubtitleItemDetail>(`/subtitles/items/${id}`)
