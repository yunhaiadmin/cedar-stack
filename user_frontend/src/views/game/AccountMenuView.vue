<template>
  <div class="am-page">
    <!-- 身份头：头像圆＋用户名＋订阅状态行（原站「我的」页形态） -->
    <header class="am-identity">
      <span class="am-avatar" aria-hidden="true">{{ avatarInitial }}</span>
      <div class="am-identity__text">
        <h1 class="am-username">{{ displayName }}</h1>
        <p class="am-status">{{ statusLine }}</p>
      </div>
    </header>

    <main class="am-body">
      <section v-for="group in groups" :key="group.title" class="am-group" :aria-label="group.title">
        <h2 class="am-group__title">{{ group.title }}</h2>
        <div class="am-card">
          <template v-for="item in group.items" :key="item.key">
            <RouterLink v-if="item.to" :to="item.to" class="am-row">
              <component :is="item.icon" :size="20" class="am-row__icon" aria-hidden="true" />
              <span class="am-row__name">{{ item.label }}</span>
              <span v-if="item.value" class="am-row__value">{{ item.value }}</span>
              <ChevronRight :size="17" class="am-row__chev" aria-hidden="true" />
            </RouterLink>
            <button v-else type="button" class="am-row" :class="{ 'am-row--danger': item.danger }" @click="item.action?.()">
              <component :is="item.icon" :size="20" class="am-row__icon" aria-hidden="true" />
              <span class="am-row__name">{{ item.label }}</span>
              <span v-if="item.value" class="am-row__value">{{ item.value }}</span>
              <ChevronRight :size="17" class="am-row__chev" aria-hidden="true" />
            </button>
          </template>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 「我的」（/game/account/menu）——用户中心移动端底坞的第二个页签页。
 * 形态照原站用户中心「我的」页：身份头＋分组菜单列表行（右值＋右箭头）。
 * 条目与 UhdAccountLayout 侧栏分组一一对应（我的/资产/补给站/探险设施
 * 的全部真实功能一个不丢，代理中心同口径仅代理可见），另加外观切换与
 * 退出登录两个动作行。深色 app 形态与订阅页同一套 UHD 语言；
 * 桌面为同结构居中单列。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bird, Briefcase, CalendarCheck, CalendarDays, ChevronRight, Compass,
  CreditCard, Crown, Home, List, LogOut, Moon, Mountain, Package, Plug,
  Radio, Settings, Shirt, Sparkles, Users, Wallet,
  type LucideIcon,
} from 'lucide-vue-next'
import { useUserStore } from '@/stores/user'
import { useTheme } from '@/composables/useTheme'
import { getAgentMe } from '@/api/agent'
import { pointsApi } from '@/api/economy'
import { subscriptionApi, type MySubscription } from '@/api'

interface MenuItem {
  key: string
  label: string
  icon: LucideIcon
  to?: string
  value?: string
  danger?: boolean
  action?: () => void
}
interface MenuGroup {
  title: string
  items: MenuItem[]
}

const router = useRouter()
const userStore = useUserStore()
const { resolved, setPreference } = useTheme()

const mySubs = ref<MySubscription[]>([])
const pointsBalance = ref<number | null>(null)
const isAgent = ref(false)

const displayName = computed(() => userStore.user?.username || '我的')
const avatarInitial = computed(() => (displayName.value.trim().charAt(0) || '我').toUpperCase())
const activeSub = computed(
  () => mySubs.value.find((s) => s.status === 'active' && s.days_left > 0) ?? null,
)
const statusLine = computed(() => {
  const s = activeSub.value
  if (s) return `${s.plan_name} · 剩 ${s.days_left} 天`
  if (userStore.user?.is_welfare) return '公益服用户'
  return '普通用户'
})

const groups = computed<MenuGroup[]>(() => {
  const finance: MenuItem[] = [
    {
      key: 'subscribe',
      label: '订阅服务',
      icon: CreditCard,
      to: '/game/account/subscribe',
      value: activeSub.value?.plan_name || undefined,
    },
    { key: 'member', label: '我的会员', icon: Crown, to: '/game/account/member' },
    {
      key: 'shop',
      label: '补给站',
      icon: Wallet,
      to: '/shop',
      value: pointsBalance.value == null ? undefined : `能量 ${pointsBalance.value.toLocaleString()}`,
    },
    { key: 'ledger', label: '能量账本', icon: List, to: '/game/ledger' },
    { key: 'supply', label: '补给记录', icon: Package, to: '/game/supply' },
  ]
  const account: MenuItem[] = [
    { key: 'overview', label: '我的概览', icon: Home, to: '/game/account' },
    { key: 'settings', label: '账号与设置', icon: Settings, to: '/game/account/settings' },
    { key: 'wishlist', label: '许愿池', icon: Sparkles, to: '/game/wishlist' },
  ]
  if (isAgent.value) {
    account.push({ key: 'agent', label: '代理中心', icon: Briefcase, to: '/game/agent' })
  }
  const explore: MenuItem[] = [
    { key: 'explore', label: '探险日志', icon: Mountain, to: '/game' },
    { key: 'checkin', label: '每日报到', icon: CalendarCheck, to: '/game/checkin' },
    { key: 'calendar', label: '探险预告', icon: CalendarDays, to: '/game/calendar' },
    { key: 'signal', label: '求救信号', icon: Radio, to: '/game/signal' },
    { key: 'pigeon', label: '信鸽', icon: Bird, to: '/game/pigeon' },
    { key: 'companion', label: '结伴同行', icon: Users, to: '/game/companion' },
    { key: 'guide', label: '向导', icon: Compass, to: '/game/guide' },
    { key: 'outfit', label: '换装', icon: Shirt, to: '/game/outfit' },
    { key: 'operator', label: '接线员', icon: Plug, to: '/game/operator' },
  ]
  const system: MenuItem[] = [
    {
      key: 'theme',
      label: '外观',
      icon: Moon,
      value: resolved.value === 'dark' ? '深色' : '浅色',
      action: () => setPreference(resolved.value === 'dark' ? 'light' : 'dark'),
    },
    {
      key: 'logout',
      label: '退出登录',
      icon: LogOut,
      danger: true,
      action: () => void handleLogout(),
    },
  ]
  return [
    { title: '订阅与财务', items: finance },
    { title: '账户', items: account },
    { title: '探险设施', items: explore },
    { title: '系统', items: system },
  ]
})

async function handleLogout() {
  await userStore.logout()
  void router.push('/login')
}

onMounted(async () => {
  const [subs, pts] = await Promise.allSettled([
    subscriptionApi.getMine(),
    pointsApi.log({ limit: 1 }),
  ])
  if (subs.status === 'fulfilled') mySubs.value = subs.value
  if (pts.status === 'fulfilled') pointsBalance.value = pts.value.balance ?? null
  // 代理可见性按 api/agent 契约取 is_agent（getAgentMe 永不 reject，
  // 失败时回 { is_agent: false }，不能用「请求成功」当可见判据）
  const me = await getAgentMe()
  isAgent.value = me.is_agent === true
})
</script>

<style scoped>
/* 「我的」：深色 app 形态，与订阅页同一套 UHD 语言 */
.am-page {
  min-height: 100dvh;
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.am-identity {
  max-width: 760px;
  margin: 0 auto;
  padding: calc(22px + env(safe-area-inset-top)) 20px 4px;
  display: flex;
  align-items: center;
  gap: 14px;
}
.am-avatar {
  flex: 0 0 auto;
  width: 54px;
  height: 54px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--uhd-d-surface-raised);
  border: 1px solid var(--uhd-d-border);
  color: var(--uhd-d-text);
  font-size: 22px;
  font-weight: var(--uhd-fw-extrabold);
}
.am-identity__text {
  min-width: 0;
}
.am-username {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 21px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  color: var(--uhd-d-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.am-status {
  margin: 3px 0 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-3);
}
.am-body {
  max-width: 760px;
  margin: 0 auto;
  padding: 18px 16px 128px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.am-group__title {
  margin: 0 0 10px 4px;
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-d-text-3);
}
.am-card {
  border-radius: var(--uhd-r-lg);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  overflow: hidden;
}
.am-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 15px 16px;
  border: none;
  border-top: 1px solid #1e1e21;
  background: transparent;
  color: var(--uhd-d-text);
  font-family: inherit;
  font-size: var(--uhd-fs-body);
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  box-sizing: border-box;
}
.am-row:first-child {
  border-top: none;
}
.am-row:active {
  background: rgba(255, 255, 255, 0.05);
}
.am-row__icon {
  flex: 0 0 auto;
  color: var(--uhd-d-text-2);
}
.am-row__name {
  flex: 1 1 auto;
  font-weight: var(--uhd-fw-medium);
}
.am-row__value {
  flex: 0 1 auto;
  min-width: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.am-row__chev {
  flex: 0 0 auto;
  color: var(--uhd-d-text-3);
}
.am-row--danger .am-row__name,
.am-row--danger .am-row__icon {
  color: var(--uhd-danger);
}
</style>
