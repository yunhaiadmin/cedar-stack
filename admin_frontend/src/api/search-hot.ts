/** 热搜管理 API（/api/admin/search*，见 backend/api/admin_search.py） */
import { del, get, post } from '@/utils/request'

export interface HotRankingItem {
  term: string
  count: number
  term_key: string
  pinned: boolean
}

export interface SearchTermRow {
  term_key: string
  term: string
  count: number
  last_seen_at: string | null
  override: 'pin' | 'block' | null
  pin_order: number | null
}

export interface SearchOverrideRow {
  id: number
  term_key: string
  term: string
  mode: 'pin' | 'block'
  pin_order: number
  created_at: string | null
  updated_at: string | null
}

export const fetchSearchRanking = (days: number, limit = 50) =>
  get<{ days: number; items: HotRankingItem[] }>('/search/ranking', { days, limit })

export const fetchSearchTerms = (days: number, limit = 100) =>
  get<{ days: number; total: number; items: SearchTermRow[] }>('/search/terms', { days, limit })

export const fetchSearchOverrides = () =>
  get<{ items: SearchOverrideRow[] }>('/search/overrides')

export const setSearchOverride = (term: string, mode: 'pin' | 'block') =>
  post<{ success: boolean; item: SearchOverrideRow }>('/search/overrides', { term, mode })

export const reorderSearchOverrides = (termKeys: string[]) =>
  post<{ success: boolean; items: SearchOverrideRow[] }>(
    '/search/overrides/reorder', { term_keys: termKeys })

export const removeSearchOverride = (term: string) =>
  del<{ success: boolean; term_key: string; mode: string }>('/search/overrides', { term })

export const deleteSearchTermStats = (term: string) =>
  del<{ success: boolean; deleted_rows: number; removed_pin: boolean }>(
    '/search/terms', { term })
