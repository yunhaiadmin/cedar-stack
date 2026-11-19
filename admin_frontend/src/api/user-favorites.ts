/** 用户收藏查看 API（/api/admin/users/{id}/favorites，见 backend/api/admin_user_favorites.py） */
import { get } from '@/utils/request'

export interface FavoriteItemRow {
  item_id: number
  guid: string | null
  name: string
  type: string
  year: number | null
  rating: number | null
  /** 条目已下架/不存在：收藏标记还在，只是条目查不到 */
  item_deleted: boolean
  favorited_at: string | null
}

export interface FavoritePersonRow {
  person_key: string
  name: string
  image: string | null
  person_tmdb_id: string | null
  created_at: string | null
}

export interface UserFavoritesResult {
  user: { id: number; username: string }
  items: FavoriteItemRow[]
  persons: FavoritePersonRow[]
  counts: { items: number; persons: number }
}

export const fetchUserFavorites = (userId: number) =>
  get<UserFavoritesResult>(`/users/${userId}/favorites`)
