/** 人物条目管理 API（/api/admin/people*，见 backend/api/admin_people.py） */
import { get, post } from '@/utils/request'

export interface PersonRow {
  key: string
  name: string
  image: string | null
  tmdb_id: string | null
  credits_count: number
  works_count: number
}

export interface MergePreview {
  source: PersonRow
  target: PersonRow
  will_migrate: number
  will_drop_duplicates: number
  favorites_to_migrate: number
  result: { key: string; credits_count: number; works_count: number }
}

export interface MergeResult {
  success: boolean
  key: string
  name: string
  image: string | null
  tmdb_id: string | null
  migrated: number
  duplicates_dropped: number
  favorites_migrated: number
  credits_count: number
  works_count: number
}

export const fetchPeople = (params: { q?: string; limit?: number; offset?: number }) =>
  get<{ total: number; items: PersonRow[] }>('/people', params)

export const renamePerson = (key: string, newName: string) =>
  post<{ success: boolean; renamed: boolean } & PersonRow & { favorites_migrated?: number }>(
    '/people/rename', { key, new_name: newName })

export const fetchMergePreview = (sourceKey: string, targetKey: string) =>
  get<MergePreview>('/people/merge-preview', { source_key: sourceKey, target_key: targetKey })

export const mergePerson = (sourceKey: string, targetKey: string) =>
  post<MergeResult>('/people/merge', { source_key: sourceKey, target_key: targetKey })
