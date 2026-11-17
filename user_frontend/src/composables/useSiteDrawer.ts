/**
 * 官网菜单抽屉（能力：官网菜单抽屉）
 *
 * 管理后台「系统设置 → 官网菜单抽屉」里配的导航项、社区渠道卡片与
 * 底部按钮，驱动官网落地页移动端菜单抽屉：前端只做数据驱动渲染，
 * 不写死文案与链接——渠道为空整个分组不出现、底按钮关掉不渲染。
 *
 * 拉取前/拉取失败（后端没起、老版本后端没有这个端点）时保持内置
 * 默认：导航只有站内既有路由「首页 / 定价」、无渠道、底按钮为
 * 内置入口，与后端无配置时的默认下发一致，不拖住落地页。
 */
import { reactive } from 'vue'
import { siteDrawerApi, type SiteDrawer } from '@/api'

export const siteDrawer = reactive<SiteDrawer>({
  nav_items: [
    { icon: 'home', label: '首页', url: '/', external: false },
    { icon: 'tag', label: '定价', url: '/pricing', external: false },
  ],
  channels_title: '社区渠道',
  channels: [],
  cta: { label: '进入后台', url: '' },
})

let initialized = false

export async function initSiteDrawer(): Promise<void> {
  if (initialized) return
  initialized = true
  try {
    const data = await siteDrawerApi.get()
    if (!data) return
    if (Array.isArray(data.nav_items)) siteDrawer.nav_items = data.nav_items
    siteDrawer.channels_title = data.channels_title ?? '社区渠道'
    siteDrawer.channels = Array.isArray(data.channels) ? data.channels : []
    siteDrawer.cta = data.cta ?? null
  } catch {
    /* 保持内置默认：抽屉仍可导航站内既有页面 */
  }
}
