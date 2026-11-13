/**
 * 账号安全三件套 API（backend/api/account_security.py + emby_portal 登录侧端点）
 * - 动态验证码（TOTP）：setup → enable（输码确认）→ disable（验密码）
 * - Passkey（WebAuthn）：register options/verify、列表、删除；登录走 authApi 的 passkey 两步
 * - 平台 API Key：创建（后端硬前置＝已绑 TOTP 或 Passkey）、列表、撤销；明文只在创建响应出现一次
 */
import api from './index'
import type { AuthResponse } from './index'

export interface PasskeyItem {
  id: number
  device_name: string
  created_at: string | null
  last_used_at: string | null
}

export interface PlatformApiKeyItem {
  id: number
  name: string
  created_at: string | null
  expires_at: string | null
  last_used_at: string | null
}

export interface SecurityOverview {
  totp: { enabled: boolean }
  passkeys: { max: number; items: PasskeyItem[] }
  api_keys: {
    can_create: boolean
    validity_days: number
    items: PlatformApiKeyItem[]
  }
}

export interface LoginFactors {
  totp_required: boolean
  passkey_available: boolean
}

export const securityApi = {
  overview: () => api.get<never, SecurityOverview>('/api/user/security/overview'),

  totpSetup: () =>
    api.post<never, { secret: string; otpauth_uri: string }>('/api/user/security/totp/setup'),

  totpEnable: (code: string) =>
    api.post<never, { success: boolean }>('/api/user/security/totp/enable', { code }),

  totpDisable: (password: string) =>
    api.post<never, { success: boolean }>('/api/user/security/totp/disable', { password }),

  passkeyRegisterOptions: () =>
    api.post<never, { options: Record<string, any>; challenge_token: string }>(
      '/api/user/security/passkeys/register/options',
    ),

  passkeyRegisterVerify: (data: {
    credential: Record<string, any>
    challenge_token: string
    device_name?: string
  }) => api.post<never, PasskeyItem>('/api/user/security/passkeys/register/verify', data),

  passkeyDelete: (id: number) => api.delete<never, { success: boolean }>(`/api/user/security/passkeys/${id}`),

  apiKeyCreate: (name: string) =>
    api.post<never, PlatformApiKeyItem & { plaintext: string }>(
      '/api/user/security/api-keys', { name },
    ),

  apiKeyDelete: (id: number) =>
    api.delete<never, { success: boolean }>(`/api/user/security/api-keys/${id}`),
}

/** 登录页因素探测与 Passkey 登录（公开端点，挂在 auth_router 下） */
export const loginSecurityApi = {
  factors: (username: string) =>
    api.get<never, LoginFactors>('/api/user/auth/login-factors', { params: { username } }),

  passkeyOptions: (username: string) =>
    api.post<never, { options: Record<string, any>; challenge_token: string }>(
      '/api/user/auth/passkey/login/options', { username },
    ),

  passkeyVerify: (data: {
    username: string
    credential: Record<string, any>
    challenge_token: string
  }) => api.post<never, AuthResponse>('/api/user/auth/passkey/login/verify', data),
}
