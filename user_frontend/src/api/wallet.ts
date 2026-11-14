/**
 * 钱包余额 API（用户端 /api/user/wallet*，钱包批 1 建、批 2 接扣款）。
 *
 * 金额一律字符串（两位小数），不在前端做 float 运算；余额不足等
 * 业务拒绝走后端 detail 文案直接展示。
 */
import api from '@/api'

export interface WalletLedgerRow {
  id: number
  user_id: number
  kind: string
  amount: string
  balance_after: string
  ref_id: string | null
  note: string | null
  operator_id: number | null
  created_at: string | null
}

export interface WalletInfo {
  balance: string
  wallet_enabled: boolean
  ledger: WalletLedgerRow[]
  ledger_total: number
}

export interface WalletLedgerPage {
  items: WalletLedgerRow[]
  total: number
  page: number
  page_size: number
}

export interface WalletPaySubscriptionResult {
  ok: boolean
  replayed: boolean
  order_id: string
  plan_name: string
  paid_amount: string
  list_price: string
  discount_amount: string
  balance_after: string
  subscription_id: number | null
  end_date: string | null
}

/** 流水 kind 的人话文案（未知 kind 原样显示，不造假文案） */
export function walletKindText(kind: string): string {
  const map: Record<string, string> = {
    recharge: '在线充值',
    admin_adjust: '管理员调整',
    gift_code: '兑换码入账',
    rebate: '邀请返利',
    subscription_purchase: '购买订阅',
    traffic_purchase: '购买流量',
    refund: '订单退款',
    rebate_reversal: '返利撤回',
  }
  return map[kind] ?? kind
}

export const walletApi = {
  /** 余额 + 最近流水（第一页）：失败抛错，由调用方决定是否隐藏入口 */
  get: () => api.get<never, WalletInfo>('/api/user/wallet'),
  /** 流水分页（可按 kind 筛选） */
  ledger: (params?: { kind?: string; page?: number; page_size?: number }) =>
    api.get<never, WalletLedgerPage>('/api/user/wallet/ledger', { params }),
  /** 余额买订阅：idempotencyKey 由调用方每次确认生成一个新键 */
  paySubscription: (data: {
    plan_id: number
    coupon_code?: string
    idempotency_key: string
  }) =>
    api.post<never, WalletPaySubscriptionResult>(
      '/api/user/wallet/pay-subscription',
      data,
    ),
  /** 余额兑换码入账 */
  redeem: (code: string) =>
    api.post<never, { ok: boolean; message?: string; balance_value?: string; balance?: string }>(
      '/api/user/wallet/redeem',
      { code },
    ),
}

/** 每次确认生成一个新幂等键（与 TrafficPurchaseModal 同一写法） */
export function newWalletKey(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `w-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  }
}
