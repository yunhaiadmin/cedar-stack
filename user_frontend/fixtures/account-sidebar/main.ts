/**
 * 本地隔离夹具。不进生产构建，不使用真实登录会话。
 * /game/operator 被守卫取消，用来测导航失败后抽屉仍开着。
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

const Stub = { name: 'FixtureStub', template: '<main id="fixture-stub">跳转页</main>' }

const router = createRouter({
  history: createWebHistory('/'),
  routes: [
    { path: '/', redirect: '/game/account' },
    { path: '/game/account', component: AccountHarness },
    { path: '/wallet', component: AccountHarness },
    { path: '/game/operator', component: AccountHarness },
    { path: '/game/once', component: OnceLayout },
    { path: '/game/speedtest', component: AccountHarness },
    { path: '/landing', component: FixtureLanding },
    { path: '/pricing', component: Stub },
    { path: '/login', component: Stub },
  ],
})
router.beforeEach((to) => {
  if (to.path === '/game/operator') return false
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
