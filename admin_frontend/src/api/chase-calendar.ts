/** 追新日历 API（/api/admin/chase-calendar*，见 backend/api/admin_chase_calendar.py） */
import { get } from '@/utils/request'

export interface ChaseCalendarItemRow {
  item_id: number
  name: string
  item_type: 'movie' | 'series' | 'episode'
  series_id: number | null
  series_name: string | null
  season_number: number | null
  episode_number: number | null
  library_id: number
  library_name: string | null
  date_added: string | null
  premiere_date: string | null
  production_year: number | null
  /** 所属剧的 active 追剧人数；电影为 null（无追剧概念） */
  followers: number | null
}

export interface ChaseCalendarDay {
  date: string
  count: number
  truncated: boolean
  items: ChaseCalendarItemRow[]
}

export interface ChaseCalendarResponse {
  start: string
  end: string
  types: Record<string, number>
  total: number
  days: ChaseCalendarDay[]
}

export interface ChaseCalendarRecentFollower {
  user_id: number
  username: string
  status: string
  created_at: string | null
}

export interface ChaseCalendarDetail {
  item_id: number
  name: string
  original_title: string | null
  item_type: 'movie' | 'series' | 'episode'
  library_id: number
  library_name: string | null
  production_year: number | null
  premiere_date: string | null
  date_added: string | null
  season_number: number | null
  episode_number: number | null
  tmdb_id: string | null
  imdb_id: string | null
  file_path: string | null
  media_source: string | null
  container: string | null
  size: number
  is_hidden: boolean
  series: { series_id: number; series_name: string } | null
  follow: {
    series_id: number
    series_name: string
    followers_active: number
    followers_total: number
    recent_followers: ChaseCalendarRecentFollower[]
    episode_count: number
    latest_episode_added: string | null
  } | null
  mp_subscription: unknown
  mp_note: string
}

export const fetchChaseCalendar = (params: {
  start?: string
  end?: string
  item_type?: string
  library_id?: number
}) => get<ChaseCalendarResponse>('/chase-calendar', params)

export const fetchChaseCalendarItem = (itemId: number) =>
  get<ChaseCalendarDetail>(`/chase-calendar/items/${itemId}`)
