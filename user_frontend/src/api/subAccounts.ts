/**
 * 家庭子账号 API（门户后端 /api/user/sub-accounts，2026-11-15）。
 *
 * 子账号是主账号在用户中心创建的独立登录账号：订阅、月流量与播放
 * 门禁一律随主账号（后端有效账号解析），子账号自己没有订阅与钱包，
 * 购买类接口后端一律 403、前端入口同步隐藏。管理口只在主账号当前
 * 套餐声明了 sub_account_limit（家庭版）时才有意义：limit=0 且没有
 * 已建子账号时概览整段不渲染。
 */
import api from '@/api'

export interface SubAccountItem {
  id: number
  username: string
  is_active: boolean
  created_at: string | null
}

export interface SubAccountManagement {
  /** 当前登录账号本身是子账号 */
  is_sub_account: boolean
  /** 主账号用户名（子账号视角才有值） */
  parent_username: string | null
  /** 主账号当前套餐可建上限（未声明子账号权益为 0） */
  limit: number
  /** 在用（未删除）子账号数 */
  used: number
  /** 订阅有效且未满额时可新建 */
  can_create: boolean
  items: SubAccountItem[]
}

export const subAccountsApi = {
  get: () => api.get<never, SubAccountManagement>('/api/user/sub-accounts'),

  create: (data: { username: string; password: string }) =>
    api.post<never, { ok: boolean; item: SubAccountItem }>(
      '/api/user/sub-accounts', data),

  remove: (id: number) =>
    api.delete<never, { ok: boolean; id: number; is_active: boolean }>(
      `/api/user/sub-accounts/${id}`),
}
