/**
 * 安装应用页数据（门户后端 /api/user/install-app）。
 *
 * 下载链接是系统配置项（管理端可配），页面只读后端下发的生效值渲染，
 * 不在前端写死任何下载链接；后端只下发已配链接的平台，未配的整行
 * 不显示。服务器地址由后端按播放资格下发（2026-11-27 资格门，与
 * 新人指南 #542 同口径）：无资格时响应整字段不带 server_address，
 * 前端归一化为 null，页面只显示占位提示、不渲染地址本体。
 */
import api from '@/api'

// === installAppNormalize 开始 ===
export interface InstallAppPlatform {
  key: string
  label: string
  devices: string
  store: string
  url: string
}

export interface InstallAppPayload {
  server_address: string | null
  platforms: InstallAppPlatform[]
}

export function normalizeInstallAppPayload(
  raw: { server_address?: unknown; platforms?: unknown } | null | undefined,
): InstallAppPayload {
  const address = typeof raw?.server_address === 'string' ? raw.server_address.trim() : ''
  return {
    server_address: address || null,
    platforms: Array.isArray(raw?.platforms) ? (raw.platforms as InstallAppPlatform[]) : [],
  }
}
// === installAppNormalize 结束 ===

export async function getInstallApp(): Promise<InstallAppPayload> {
  const res = await api.get<never, { server_address?: unknown; platforms?: unknown }>('/api/user/install-app')
  return normalizeInstallAppPayload(res)
}
