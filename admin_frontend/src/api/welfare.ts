/** 机器人配置管理后台 API（全部走 /api/admin/welfare/*，历史路径名）
 *
 * 群抽奖 API 已随群抽奖整族于 2026-11-23 下线删除（端点已删，
 * 保留会触发 check_admin_api_contract 契约门）。
 */
import { get, put } from '@/utils/request'

const E = '/welfare'

// 公益用户操作（grant/revoke/bulk-extend）已随公益资格整族于 2026-11-14 删除。

// 注：求片审核已并入「求片管理」（MediaSeek.vue），welfare.ts 不再保留求片 API。

// ==================== 配置 ====================

export const fetchWelfareConfig = () =>
  get<Record<string, string>>(`${E}/config`)

export const saveWelfareConfig = (data: Record<string, string>) =>
  put<{ success: boolean; updated: string[] }>(`${E}/config`, data)
