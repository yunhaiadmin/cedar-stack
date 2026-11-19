/** 4K 探针结果查看 API（/api/admin/probe-4k/*，见 backend/api/admin_probe_4k.py） */
import { get } from '@/utils/request'

export interface Probe4kRow {
  id: number
  user_id: number
  username: string
  item_guid: string
  attempt: string
  outcome: string
  user_agent: string
  /** payload 字符数（列表不带原文，详情才给） */
  payload_chars: number
  created_at: string | null
}

export interface Probe4kDetail extends Probe4kRow {
  payload: string
}

export interface Probe4kListResponse {
  total: number
  items: Probe4kRow[]
  /** 表里出现过的全部结果短码（筛选下拉用） */
  outcome_options: string[]
}

export const fetchProbe4kResults = (params: {
  limit?: number
  offset?: number
  item_guid?: string
  outcome?: string
}) => get<Probe4kListResponse>('/probe-4k/results', params)

export const fetchProbe4kResult = (id: number) =>
  get<Probe4kDetail>(`/probe-4k/results/${id}`)
