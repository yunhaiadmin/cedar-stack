/** 软件授权管理 API（/api/admin/license/*，见 backend/api/admin_license.py） */
import { get, post, put } from '@/utils/request'

export interface LicenseStatus {
  enforced: boolean
  /** disabled=未启用校验 / ok=生效中 / grace=宽限中 / blocked=已拦截播放 */
  state: 'disabled' | 'ok' | 'grace' | 'blocked'
  allowed: boolean
  /** 拦截原因（state=blocked 时）：no_code / disabled / expired / binding_mismatch 等 */
  reason: string
  in_grace: boolean
  tier: string | null
  expires_at: string | null
  days_left: number | null
  /** 到期提醒级别：30 / 7 / 1 天内，或 grace=已到期处于宽限期 */
  reminder: string | null
  server: string
  /** 授权码掩码：已配置时为 ******，未配置为空串；明文永不下发 */
  code: string
  license_code_set: boolean
  domain: string
  last_checked_at: string
  last_result: string
}

export interface LicenseConfigPayload {
  code?: string
  domain?: string
}

export const fetchLicenseStatus = () => get<LicenseStatus>('/license/status')

export const saveLicenseConfig = (payload: LicenseConfigPayload) =>
  put<LicenseStatus>('/license/config', payload)

export const verifyLicenseNow = () => post<LicenseStatus>('/license/verify')
