/**
 * 站点品牌信息
 *
 * 这个端点在 `/api/site/*` 下，而管理端的 axios 实例 baseURL 是
 * `/api/admin`，所以这里用裸 axios 走同源绝对路径：它是**公开**端点（站名要给未登录
 * 访客看），不需要带管理员 token，也没必要塞进带鉴权的实例。
 */
import axios from 'axios'

export interface Branding {
  site_name: string
  logo_url: string
  theme_color: string
  seo_title: string
  seo_description: string
  seo_keywords: string
}

export const siteApi = {
  branding: async () => (await axios.get<Branding>('/api/site/branding')).data,
}
