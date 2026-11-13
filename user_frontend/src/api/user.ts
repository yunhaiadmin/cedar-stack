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

/** 我的专属线路（GET /api/user/exclusive-lines）：只有名称与入口
 * 地址，取流基址与入口密钥是内部配置、永不下发用户端。无资格时
 * 后端回空列表，页面据此不渲染该分组；请求失败同样按空处理。 */
export interface MyExclusiveLine {
  name: string
  entry_url: string
}

export async function getMyExclusiveLines(): Promise<MyExclusiveLine[]> {
  try {
    const res = await api.get<never, { lines: MyExclusiveLine[] }>('/api/user/exclusive-lines')
    return Array.isArray(res?.lines) ? res.lines : []
  } catch {
    return []
  }
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
  registration_mode: 'open' | 'code' | 'closed'
  /** 凭码模式：注册前必须先验注册码（两步流程第一步） */
  registration_code_required: boolean
  /** 「没有注册码去哪领」提示（文案与链接均由管理端配置下发） */
  registration_code_hint: { text: string; url: string }
  invitation_enabled: boolean
}

/** 获取注册页开关：注册模式 + 邀请码开关 */
export async function getRegisterConfig(): Promise<RegisterConfig> {
  try {
    const res = await api.get<never, {
      registration_mode: string
      registration_code_required?: boolean
      registration_code_hint?: { text?: string; url?: string }
      invitation_enabled: boolean
    }>('/api/user/auth/register-config')
    const mode = res.registration_mode === 'closed'
      ? 'closed'
      : res.registration_mode === 'code'
        ? 'code'
        : 'open'
    return {
      registration_mode: mode,
      registration_code_required: res.registration_code_required === true,
      registration_code_hint: {
        text: res.registration_code_hint?.text || '',
        url: res.registration_code_hint?.url || '',
      },
      invitation_enabled: res.invitation_enabled !== false,
    }
  } catch {
    // 拿不到时按最宽松处理：开放注册 + 邀请码显示（不把用户锁在门外）
    return {
      registration_mode: 'open',
      registration_code_required: false,
      registration_code_hint: { text: '', url: '' },
      invitation_enabled: true,
    }
  }
}

/** 凭码注册第一步验码结果（只校验不消耗） */
export interface RegisterCodeVerifyResult {
  valid: boolean
  message: string
  reward_type?: string
  days_text?: string
  realm_name?: string
  is_named?: boolean
}

/** 两步注册第一步：校验注册码（公开接口，未登录可调；校验不消耗次数） */
export async function verifyRegisterCode(code: string): Promise<RegisterCodeVerifyResult> {
  return api.post<never, RegisterCodeVerifyResult>('/api/user/auth/register-code/verify', { code })
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
 * 人机验证与登录共用同一道闸（action=login），令牌随请求带上。
 */
export async function requestPasswordReset(
  identifier: string,
  captchaToken: string,
): Promise<PasswordResetResult> {
  return api.post<never, PasswordResetResult>('/api/auth/password-reset/request', {
    identifier,
    captcha_token: captchaToken || null,
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
