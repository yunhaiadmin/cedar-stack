/**
 * 安装应用页下载链接配置（/api/admin/install-app/config，2026-11-13）。
 * 6 个平台的官方客户端下载链接：管理端可配，用户端页面只读已配的
 * 生效值渲染。没有代码级默认链接：url 为空 = 未配，用户端整行不显示。
 * 保存时传空串 = 清空取消配置。
 */
import { get, put } from '@/utils/request'

export interface InstallAppPlatformConfig {
  key: string
  label: string
  devices: string
  url: string
  configured: boolean
}

export interface InstallAppConfigResponse {
  platforms: InstallAppPlatformConfig[]
}

export function fetchInstallAppConfig() {
  return get<InstallAppConfigResponse>('/install-app/config')
}

export function updateInstallAppConfig(urls: Record<string, string>) {
  return put<InstallAppConfigResponse>('/install-app/config', { urls })
}
