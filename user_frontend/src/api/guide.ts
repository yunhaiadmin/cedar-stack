/**
 * 新人指南页服务器地址（门户后端 /api/user/guide/server-address）。
 *
 * 地址不在前端写死：后端只给有播放资格（有效订阅或团队播放资格）
 * 的登录用户下发地址本体，无资格时响应不带地址字段。前端拿不到
 * 或无资格一律按不显示处理（fail-closed），页面只显示占位文案。
 */
import api from '@/api'

export interface GuideServerAddressPayload {
  visible: boolean
  server_address: string
}

export async function getGuideServerAddress(): Promise<GuideServerAddressPayload> {
  const res = await api.get<never, { visible?: unknown; server_address?: unknown }>(
    '/api/user/guide/server-address',
  )
  return {
    visible: res?.visible === true,
    server_address: typeof res?.server_address === 'string' ? res.server_address : '',
  }
}
