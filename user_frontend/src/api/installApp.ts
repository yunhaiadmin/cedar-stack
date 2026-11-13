/**
 * 安装应用页数据（门户后端 /api/user/install-app）。
 *
 * 下载链接是系统配置项（管理端可配），页面只读后端下发的生效值渲染，
 * 不在前端写死任何下载链接；后端只下发已配链接的平台，未配的整行
 * 不显示。服务器地址由后端按对外唯一口径下发（CDN 地址）。
 */
import api from '@/api'

export interface InstallAppPlatform {
  key: string
  label: string
  devices: string
  store: string
  url: string
}

export interface InstallAppPayload {
  server_address: string
  platforms: InstallAppPlatform[]
}

export async function getInstallApp(): Promise<InstallAppPayload> {
  const res = await api.get<never, InstallAppPayload>('/api/user/install-app')
  return {
    server_address: String(res?.server_address ?? ''),
    platforms: Array.isArray(res?.platforms) ? res.platforms : [],
  }
}
