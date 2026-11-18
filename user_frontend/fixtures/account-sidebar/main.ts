/**
 * 本地隔离夹具。不进生产构建，不使用真实登录会话。
 * 订阅服务（/game/account/subscribe）只在 regress.mjs 置起
 * window.__blockSubscribe 时被守卫取消，用来测导航失败后抽屉仍开着。
 * （原导航失败替身路由已随 2026-11-18 删除单整页删除；订阅服务
 * 平时不拦截，batch2-shot 直接加载该页不受影响。）
 */
import '@/styles/aurora.css'
import '@/styles/neo-noir-tokens.css'
import '@/styles/index.css'
import '@/styles/mobile.css'
import '@/styles/v4-paper.css'
import '@/styles/uhd-tokens.css'
import '@/styles/uhd-topbar.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import { engageModalIsolation, lockModalOverflow, unlockModalOverflow } from '@/composables/useModalIsolation'
import FixtureApp from './FixtureApp.vue'
import AccountHarness from './AccountHarness.vue'
import FixtureLanding from './FixtureLanding.vue'
import OnceLayout from './OnceLayout.vue'
import PersonalCenterView from '@/views/game/PersonalCenterView.vue'
import SubscriptionPlansView from '@/views/game/SubscriptionPlansView.vue'
import LibraryBrowseView from '@/views/panel/LibraryBrowseView.vue'
import MediaDetailView from '@/views/panel/MediaDetailView.vue'
import FavoritesView from '@/views/panel/FavoritesView.vue'

const Stub = { name: 'FixtureStub', template: '<main id="fixture-stub">跳转页</main>' }

const router = createRouter({
  history: createWebHistory('/'),
  routes: [
    { path: '/', redirect: '/game/account' },
    { path: '/game/account', component: AccountHarness },
    { path: '/wallet', component: AccountHarness },
    { path: '/game/once', component: OnceLayout },
    { path: '/game/speedtest', component: AccountHarness },
    { path: '/landing', component: FixtureLanding },
    { path: '/overview-fixture', component: PersonalCenterView },
    { path: '/game/account/subscribe', component: SubscriptionPlansView },
    { path: '/library/movies', name: 'library-movies', component: LibraryBrowseView },
    { path: '/library/series', name: 'library-series', component: LibraryBrowseView },
    { path: '/media/:id', name: 'media-detail', component: MediaDetailView },
    { path: '/person/:key', name: 'person-detail', component: LibraryBrowseView },
    { path: '/favorites', name: 'favorites', component: FavoritesView },
    { path: '/pricing', component: Stub },
    { path: '/login', component: Stub },
  ],
})
router.beforeEach((to) => {
  // 导航失败替身：仅 regress.mjs 置起 __blockSubscribe 的那两步拦截订阅服务，
  // 其余时候放行（batch2-shot 直接加载订阅页不受影响）。
  if (to.path === '/game/account/subscribe' && (window as unknown as { __blockSubscribe?: boolean }).__blockSubscribe) return false
  if (to.path === '/game/speedtest') throw new Error('router-threw')
})
router.onError((err) => {
  ;(window as unknown as { __lastNavError?: string }).__lastNavError = String(err?.message || err)
})

const app = createApp(FixtureApp).use(createPinia()).use(router)
const w = window as unknown as {
  __fixtureGo?: (path: string) => Promise<unknown>
  __lock?: () => void
  __unlock?: () => void
  __engage?: (el: HTMLElement) => void
  __releaseLast?: () => void
  __holds?: Array<() => void>
}
w.__fixtureGo = (path) => router.push(path)
w.__lock = lockModalOverflow
w.__unlock = unlockModalOverflow
w.__holds = []
w.__engage = (el) => {
  w.__holds?.push(engageModalIsolation(el))
}
w.__releaseLast = () => {
  w.__holds?.pop()?.()
}
app.mount('#app')
