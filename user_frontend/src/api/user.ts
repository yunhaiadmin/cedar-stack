/**
 * 用户端个人设置 API（门户后端 /api/user/*）。
 *
 * 2026-10 简化：播放只有中转一条路径，以下接口保留仅为兼容，
 * 新代码不要再调用。
 */
import api from '@/api'

/** 播放路径（已废弃）：只有 'relay' */
export type PlayLine = 'relay'

/** 设置播放路径（已废弃）：空操作，永远返回 relay */
export async function setPlayLine(_line: PlayLine): Promise<PlayLine> {
  return 'relay'
}

/** 测速页网络信息（GET /api/user/network-info）：IP 已在服务端打码，
 * 地区/运营商推导不到时为 null（页面显示 --）。失败同样按 null 处理。 */
export interface MyNetworkInfo {
  ip_masked: string | null
  region: string | null
  operator: string | null
}

export async function getMyNetworkInfo(): Promise<MyNetworkInfo | null> {
  try {
    return await api.get<never, MyNetworkInfo>('/api/user/network-info')
  } catch {
    return null
  }
}

/** 注册页开关状态（公开接口，未登录可调） */
export interface RegisterConfig {
  registration_mode: 'open' | 'closed'
  invitation_enabled: boolean
}

/** 获取注册页开关：注册模式 + 邀请码开关 */
export async function getRegisterConfig(): Promise<RegisterConfig> {
  try {
    const res = await api.get<never, {
      registration_mode: string
      invitation_enabled: boolean
    }>('/api/user/auth/register-config')
    const mode = res.registration_mode === 'closed' ? 'closed' : 'open'
    return {
      registration_mode: mode,
      invitation_enabled: res.invitation_enabled !== false,
    }
  } catch {
    // 拿不到时按最宽松处理：开放注册 + 邀请码显示（不把用户锁在门外）
    return {
      registration_mode: 'open',
      invitation_enabled: true,
    }
  }
}

/** 找回密码两端点结果（后端 /api/auth/password-reset/*，#94 已上线） */
export interface PasswordResetResult {
  success: boolean
  message: string
}

/**
 * 申请重置邮件：identifier 可填用户名或邮箱。
 * 后端防枚举——账号不存在 / 未绑邮箱 / 已发送三种情况返回完全一致的
 * success + message，调用方必须原样展示后端 message，不许自行改写成
 * 「邮件已发送到你的邮箱」之类能区分账号是否存在的文案。
 */
export async function requestPasswordReset(
  identifier: string,
): Promise<PasswordResetResult> {
  return api.post<never, PasswordResetResult>('/api/auth/password-reset/request', {
    identifier,
  })
}

/** 凭邮件链接令牌（/login?reset=<token>）设置新密码，后端校验 6–64 位 */
export async function confirmPasswordReset(
  token: string,
  newPassword: string,
): Promise<PasswordResetResult> {
  return api.post<never, PasswordResetResult>('/api/auth/password-reset/confirm', {
    token,
    new_password: newPassword,
  })
}
