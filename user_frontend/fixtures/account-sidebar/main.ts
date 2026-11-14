/**
 * 本地隔离夹具入口。不进生产路由，不读品牌接口，不使用真实登录会话。
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

const router = createRouter({
  history: createWebHistory('/'),
  routes: [{ path: '/', component: { template: '<div />' } }],
})

createApp(FixtureApp).use(createPinia()).use(router).mount('#app')
