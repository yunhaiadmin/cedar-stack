/** 转存变体 API（/api/admin/playback/transfer-variants*，见 backend/api/playback_admin.py）
 *
 * 转存变体 = 把条目文件转存出一份「服主副本」，播放优先走副本。
 * 本组端点是登记/任务观测 + 条目级动作（手动转存、解绑），没有配置项。
 */
import { get, post } from '@/utils/request'

export interface TransferVariantRow {
  id: number
  item_id: number
  source_file_id: string | null
  variant_file_id: string | null
  variant_drive_id: string | null
  owner_scope: string | null
  owner_ref: string | null
  status: string
  size_bytes: number | null
  created_at: string | null
  last_played_at: string | null
  last_checked_at: string | null
  stale_count: number
}

export interface TransferJobRow {
  id: number
  item_id: number
  trigger: string | null
  drive_id: string | null
  status: string
  attempts: number
  error_kind: string | null
  error_detail: string | null
  next_retry_at: string | null
  created_at: string | null
  updated_at: string | null
}

export const fetchTransferVariants = (limit = 100) =>
  get<{ items: TransferVariantRow[] }>('/playback/transfer-variants', { limit })

export const fetchTransferJobs = (params: { status?: string; limit?: number } = {}) =>
  get<{ items: TransferJobRow[] }>('/playback/transfer-variants/jobs', params)

/** 手动触发转存（只建队，工人按轮次执行）；force 越过 24h 节流 */
export const transferVariantItem = (itemId: number, force = false) =>
  post<{ success: boolean; job_id: number; item_id: number; status: string }>(
    `/playback/transfer-variants/${itemId}/transfer`, { force })

/** 条目级解绑：全部 ready 副本变 released，下次播放回原件（副本文件保留） */
export const unbindTransferVariant = (itemId: number) =>
  post<{ success: boolean; item_id: number; released: number }>(
    `/playback/transfer-variants/${itemId}/unbind`)
