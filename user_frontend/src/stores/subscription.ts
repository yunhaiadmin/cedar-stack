import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { subscriptionApi, type MySubscription } from '@/api'
import { useUserStore } from '@/stores/user'

/**
 * 订阅状态单一数据源（2026-11-15 假空态根治）：
 * 此前概览（PersonalCenterView）与「我的」（AccountMenuView）各自独立
 * 调 subscriptionApi.getMine()，且都在数据返回前先按空数组渲染出
 * 「未开通」「普通用户」结论、数据回来再覆盖，造成录屏实锤的闪变。
 * 收敛后两页只读本 store：subscriptions / activeSub / loaded 同源同拍；
 * loaded=false（未落定）期间页面只许中性占位，不许渲染任何结论性文案。
 * 缓存按登录会话序列（userStore.sessionSeq）失效：换号/重登自动重拉。
 */
export const useSubscriptionStore = defineStore('subscription', () => {
  const subscriptions = ref<MySubscription[]>([])
  /** 是否已落定（成功或失败都算落定：失败时按真实空态展示，不假造结论） */
  const loaded = ref(false)
  let loadedSeq = -1
  let inflight: Promise<void> | null = null

  const activeSub = computed(
    () =>
      subscriptions.value.find((s) => s.status === 'active' && s.days_left > 0) ??
      null,
  )

  async function ensureLoaded(force = false): Promise<void> {
    const seq = useUserStore().sessionSeq
    if (!force && loaded.value && loadedSeq === seq) return
    if (inflight) return inflight
    inflight = (async () => {
      try {
        subscriptions.value = await subscriptionApi.getMine()
      } catch {
        /* 拉不到就留空：loaded 照样落定，页面展示真实空态而非假结论 */
      } finally {
        loaded.value = true
        loadedSeq = seq
        inflight = null
      }
    })()
    return inflight
  }

  /** 订阅发生变更（购买/续费在本页内完成）后强制重拉 */
  function refresh(): Promise<void> {
    return ensureLoaded(true)
  }

  return { subscriptions, loaded, activeSub, ensureLoaded, refresh }
})
