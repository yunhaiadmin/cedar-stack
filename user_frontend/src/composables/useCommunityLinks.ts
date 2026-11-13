/**
 * 官网社区渠道（能力：官网社区渠道）
 *
 * 管理后台「系统设置 → 官网社区渠道」里填的文档外链与社区渠道卡片，
 * 驱动官网首页菜单面板：未配的文档行不渲染、渠道格只渲染下发的，
 * 四格全空时整个「社区渠道」小节不出现。
 *
 * 拉取失败（后端没起、老版本后端没有这个端点）时保持全空：菜单面板
 * 只显示固定导航（首页 / 定价 / 进入后台），不该把页面拖住。
 */
import { reactive } from 'vue'
import { communityApi, type CommunityLinks } from '@/api'

export const communityLinks = reactive<CommunityLinks>({
  docs_url: '',
  channels: [],
})

let initialized = false

export async function initCommunityLinks(): Promise<void> {
  if (initialized) return
  initialized = true
  try {
    const data = await communityApi.get()
    communityLinks.docs_url = data?.docs_url || ''
    communityLinks.channels = Array.isArray(data?.channels) ? data.channels : []
  } catch {
    /* 保持全空：菜单面板只显示固定导航 */
  }
}
