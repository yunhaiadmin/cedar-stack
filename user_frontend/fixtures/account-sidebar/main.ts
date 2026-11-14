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
import FixtureApp from './FixtureApp.vue'
import AccountHarness from './AccountHarness.vue'
import FixtureLanding from './FixtureLanding.vue'

const router = createRouter({
  history: createWebHistory('/'),
  routes: [
    { path: '/', redirect: '/game/account' },
    { path: '/game/account', component: AccountHarness },
    { path: '/wallet', component: AccountHarness },
    { path: '/game/operator', component: AccountHarness },
    { path: '/landing', component: FixtureLanding },
  ],
})
router.beforeEach((to) => {
  if (to.path === '/game/operator') return false
})

const app = createApp(FixtureApp).use(createPinia()).use(router)
;(window as unknown as { __fixtureGo?: (path: string) => Promise<unknown> }).__fixtureGo = (path) => router.push(path)
app.mount('#app')
