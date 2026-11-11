import { onMounted, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

/**
 * 未绑盘点播引导的回跳参数（全程前端内闭环，不经后端）：
 *
 * 「去绑盘」时把来源页路径与片名写进设置页的 query（bind_return /
 * bind_title）并落到绑定组锚点 #my-drive；设置页观察到名下出现可用
 * 绑定后，replace 回来源页并带上 resume_title；来源页消费该参数，
 * 重新打开播放器选择 sheet 并立即清掉参数（防刷新重复弹）。
 */
export const BIND_RETURN_QUERY = 'bind_return'
export const BIND_TITLE_QUERY = 'bind_title'
export const RESUME_TITLE_QUERY = 'resume_title'

/** 绑盘页落点：账号设置页 + 绑定组锚点 + 回跳参数 */
export function bindDriveLocation(returnPath: string, title?: string): RouteLocationRaw {
  const query: Record<string, string> = { [BIND_RETURN_QUERY]: returnPath }
  const t = (title || '').trim()
  if (t) query[BIND_TITLE_QUERY] = t
  return { path: '/game/account/settings', hash: '#my-drive', query }
}

/**
 * 回跳目标只许站内绝对路径：拒绝外链（含协议相对 //）、空值与设置页
 * 自身（防自跳循环）。不合法时返回空串，调用方按「无回跳」处理。
 */
export function sanitizeBindReturnPath(raw: unknown): string {
  if (typeof raw !== 'string') return ''
  const p = raw.trim()
  if (!p.startsWith('/') || p.startsWith('//')) return ''
  if (p.startsWith('/game/account/settings')) return ''
  return p
}

/**
 * 来源页消费回跳：带 resume_title 回到本页时打开播放器选择 sheet，
 * 并把参数从地址里清掉。挂载时与 query 变化时各检查一次。
 */
export function useBindReturnResume(openPlayer: (title: string) => void) {
  const route = useRoute()
  const router = useRouter()

  function consume() {
    const raw = route.query[RESUME_TITLE_QUERY]
    const title = typeof raw === 'string' ? raw.trim() : ''
    if (!title) return
    const query = { ...route.query }
    delete query[RESUME_TITLE_QUERY]
    void router.replace({ path: route.path, query, hash: route.hash })
    openPlayer(title)
  }

  onMounted(consume)
  watch(() => route.query[RESUME_TITLE_QUERY], consume)
}
