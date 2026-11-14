/** 钱包管理 API（/api/admin/economy/wallet/*，后端见 backend/api/admin_wallet.py） */
import { get, post } from '@/utils/request'

export interface WalletLedgerRow {
  id: number
  user_id: number
  username?: string | null
  kind: string
  /** 带符号金额（元，字符串保精度）：正入账 / 负出账 */
  amount: string
  balance_after: string
  ref_id: string | null
  note: string | null
  operator_id: number | null
  operator_name?: string | null
  created_at: string | null
}

export interface WalletBalanceResponse {
  user_id: number
  username: string
  balance: string
  ledger: WalletLedgerRow[]
  ledger_total: number
}

export interface WalletLedgerPage {
  items: WalletLedgerRow[]
  total: number
  page: number
  page_size: number
}

export const walletApi = {
  balance: (userId: number) =>
    get<WalletBalanceResponse>('/economy/wallet/balance', { user_id: userId }),
  adjust: (data: { user_id: number; amount: string; reason: string }) =>
    post<{ ok: boolean; balance: string; ledger_id: number }>('/economy/wallet/adjust', data),
  ledger: (params: { user_id?: number; kind?: string; page?: number; page_size?: number }) =>
    get<WalletLedgerPage>('/economy/wallet/ledger', params as Record<string, unknown>),
  getSettings: () =>
    get<{ settings: Record<string, string> }>('/economy/wallet/settings'),
  saveSettings: (settings: Record<string, string>) =>
    post<{ ok: boolean; saved: string[] }>('/economy/wallet/settings', { settings }),
}
