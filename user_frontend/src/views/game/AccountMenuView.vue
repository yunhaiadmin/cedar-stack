<template>
  <div class="am-page">
    <!-- 页头品牌行已删（2026-11-14 顶栏统一单）：顶栏由 App.vue 的
         UhdTopBar 全区统一承接（本页标题传「我的」），不再自带一份 -->

    <!-- 身份区：头像 64 圆＋名 24/800＋状态副行（真实文案） -->
    <div class="am-identity">
      <span class="am-avatar" aria-hidden="true">{{ avatarInitial }}</span>
      <div class="am-identity__text">
        <p class="am-username">{{ displayName }}</p>
        <div class="am-idrow">
          <p class="am-status">{{ statusLine }}</p>
          <button
            v-if="userId"
            type="button"
            class="am-copy"
            aria-label="复制用户 ID"
            @click="copyUserId"
          >
            <Copy :size="14" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>

    <main class="am-body">
      <section v-for="group in groups" :key="group.title" class="am-group" :aria-label="group.title">
        <h2 class="am-group__title">{{ group.title }}</h2>
        <template v-for="item in group.items" :key="item.key">
          <!-- 外观行：右端内嵌三档分段控件（系统/浅色/深色），接现行外观机制 -->
          <div v-if="item.key === 'theme'" class="am-row am-row--static">
            <span class="am-row__name">{{ item.label }}</span>
            <div class="am-seg" role="group" aria-label="外观">
              <button
                v-for="opt in THEME_OPTIONS"
                :key="opt.value"
                type="button"
                class="am-seg__btn"
                :class="{ 'am-seg__btn--on': preference === opt.value }"
                :aria-pressed="preference === opt.value"
                @click="setPreference(opt.value)"
              >{{ opt.label }}</button>
            </div>
          </div>
          <RouterLink v-else-if="item.to" :to="item.to" class="am-row">
            <span class="am-row__name">{{ item.label }}</span>
            <span v-if="item.value" class="am-row__value">{{ item.value }}</span>
            <ChevronRight :size="16" class="am-row__chev" aria-hidden="true" />
          </RouterLink>
          <a
            v-else-if="item.href"
            :href="item.href"
            target="_blank"
            rel="noopener"
            class="am-row"
          >
            <span class="am-row__name">{{ item.label }}</span>
            <ChevronRight :size="16" class="am-row__chev" aria-hidden="true" />
          </a>
          <button v-else type="button" class="am-row" @click="item.action?.()">
            <span class="am-row__name">{{ item.label }}</span>
            <span v-if="item.value" class="am-row__value">{{ item.value }}</span>
            <ChevronRight :size="16" class="am-row__chev" aria-hidden="true" />
          </button>
        </template>
      </section>

      <!-- 退出登录：列表下方独立整宽胶囊按钮（原站形态），行为保持现有登出逻辑 -->
      <!-- 安装应用入口已进「帮助」组（整页 /game/install，2026-11-13）：
           原先这里的 PWA 行随之摘除，PWA 安装动作在整页内保留 -->
      <button type="button" class="am-logout" @click="handleLogout">退出登录</button>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 「我的」（/game/account/menu）——用户中心移动端底坞的第二个页签页。
 * 2026-11-13 按 UHDNow 原站 1:1 返工：平面列表（分组粗标题＋发丝线，
 * 无卡片、无行图标）＋页头「网页观影」胶囊＋外观三档内嵌分段，
 * 页面跟随用户外观（浅/深两套本地变量，不再强制深色）。
 * 2026-11-13 跟进（用户定调）：功能改名（补给站→商店、能量账本→能量明细、
 * 补给记录→商店记录、许愿池→求片、求救信号→反馈）；探险设施整组与
 * 「每日报到」「信鸽」两行摘除（签到/站内信网页页经友站核查后定整页删除）；
 * 代理中心同口径仅代理可见。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ChevronRight, Copy } from 'lucide-vue-next'
import { useUserStore } from '@/stores/user'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'
import { communityLinks, initCommunityLinks } from '@/composables/useCommunityLinks'
import { getAgentMe } from '@/api/agent'
import { subscriptionApi, type MySubscription } from '@/api'

interface MenuItem {
  key: string
  label: string
  to?: string
  /** 外部链接（使用说明→官网文档地址，后台可配；未配不渲染该行） */
  href?: string
  value?: string
  action?: () => void
}
interface MenuGroup {
  title: string
  items: MenuItem[]
}

const THEME_OPTIONS = [
  { value: 'system', label: '系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
] as const

const router = useRouter()
const userStore = useUserStore()
const toast = useToast()
const { preference, setPreference } = useTheme()

const mySubs = ref<MySubscription[]>([])
const isAgent = ref(false)

const displayName = computed(() => userStore.user?.username || '我的')
const avatarInitial = computed(() => (displayName.value.trim().charAt(0) || '我').toUpperCase())
const activeSub = computed(
  () => mySubs.value.find((s) => s.status === 'active' && s.days_left > 0) ?? null,
)
const userId = computed(() => userStore.user?.id ?? null)
// 身份副行照友站形态：订阅档名 · 用户 ID（复制钮在模板里）；剩余天数在概览页看
const statusLine = computed(() => {
  const s = activeSub.value
  const base = s
    ? s.plan_name
    : userStore.user?.is_welfare
      ? '公益服用户'
      : '普通用户'
  return userId.value ? `${base} · ${userId.value}` : base
})

async function copyUserId() {
  if (!userId.value) return
  try {
    await navigator.clipboard.writeText(String(userId.value))
    toast.success('用户 ID 已复制')
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

const groups = computed<MenuGroup[]>(() => {
  // 2026-11-13 细节对齐（用户圈图亲点）：分组与条目顺序照友站「我的」——
  // 订阅与财务（订阅服务/订阅记录/钱包）、账户（账号安全/邀请好友/团队管理）、
  // 帮助（求片反馈/安装应用/线路测速/使用说明）。钱包条目随钱包本体一起上，
  // 本单不摆空入口；我的订阅（订阅详情页）保留在财务组末尾。
  const finance: MenuItem[] = [
    {
      key: 'subscribe',
      label: '订阅服务',
      to: '/game/account/subscribe',
      value: activeSub.value?.plan_name || undefined,
    },
    { key: 'records', label: '订阅记录', to: '/game/account/subscription-records' },
    { key: 'member', label: '我的订阅', to: '/game/account/member' },
  ]
  const account: MenuItem[] = [
    { key: 'settings', label: '账号安全', to: '/game/account/settings' },
    { key: 'invite', label: '邀请好友', to: '/game/account/invite' },
    { key: 'team', label: '团队管理', to: '/game/account/team' },
  ]
  if (isAgent.value) {
    account.push({ key: 'agent', label: '代理中心', to: '/game/agent' })
  }
  // 用户 2026-11-13 圈选令＋条件令：探险设施整组摘除、「我的概览」行删除；
  // 「每日报到」「信鸽」两行摘除（友站核查无签到/站内消息，两页定整页删除，
  // 页面本体由并行删除单处理）；求救信号保留并改名「反馈」。
  // 2026-11-13 求片反馈合并页：「求片」行摘除、「反馈」行改名「求片反馈」
  // 并指向新页 /game/requests（入口由这一行覆盖，不留两个指向同页的行）。
  const help: MenuItem[] = [
    { key: 'requests', label: '求片反馈', to: '/game/requests' },
    // 安装应用（2026-11-13）：原先列表下方的 PWA 行改为进入整页
    // （服务器地址＋客户端下载＋教程），PWA 安装动作在整页内保留
    { key: 'install', label: '安装应用', to: '/game/install' },
    { key: 'speedtest', label: '线路测速', to: '/game/speedtest' },
  ]
  // 使用说明：指向官网文档外链（后台「官网社区渠道」可配），未配不渲染
  if (communityLinks.docs_url) {
    help.push({ key: 'guide', label: '使用说明', href: communityLinks.docs_url })
  }
  const system: MenuItem[] = [{ key: 'theme', label: '外观' }]
  return [
    { title: '订阅与财务', items: finance },
    { title: '账户', items: account },
    { title: '帮助', items: help },
    { title: '系统', items: system },
  ]
})

async function handleLogout() {
  await userStore.logout()
  void router.push('/login')
}

onMounted(async () => {
  void initCommunityLinks()
  const [subs] = await Promise.allSettled([
    subscriptionApi.getMine(),
  ])
  if (subs.status === 'fulfilled') mySubs.value = subs.value
  // 代理可见性按 api/agent 契约取 is_agent（getAgentMe 永不 reject，
  // 失败时回 { is_agent: false }，不能用「请求成功」当可见判据）
  const me = await getAgentMe()
  isAgent.value = me.is_agent === true
})
</script>

<style scoped>
/* 「我的」：跟随用户外观的平面列表。本地主题变量：缺省深色，
   html[data-theme='light'] 时整套换浅（与 useTheme 的 data-theme 口径一致）。 */
.am-page {
  min-height: 100dvh;
  background: #050505;
  color: #ffffff;
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}

/* 身份区 */
.am-identity {
  max-width: 760px;
  margin: 0 auto;
  padding: 20px 20px 2px;
  display: flex;
  align-items: center;
  gap: 14px;
}
.am-avatar {
  flex: 0 0 auto;
  width: 64px;
  height: 64px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #232329;
  color: #ffffff;
  font-size: 26px;
  font-weight: var(--uhd-fw-extrabold);
}
.am-identity__text {
  min-width: 0;
}
.am-username {
  margin: 0;
  font-size: 24px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  color: #ffffff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.am-status {
  margin: 0;
  font-size: 14.5px;
  color: rgba(255, 255, 255, 0.62);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.am-idrow {
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.am-copy {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: rgba(255, 255, 255, 0.62);
  cursor: pointer;
}
.am-copy:active {
  opacity: 0.7;
}

/* 平面分组列表：粗标题＋发丝线行，无卡片无行图标 */
.am-body {
  max-width: 760px;
  margin: 0 auto;
  padding: 4px 20px 150px;
  box-sizing: border-box;
}
.am-group__title {
  margin: 30px 0 6px;
  font-family: var(--uhd-font-sans);
  font-size: 20px;
  font-weight: var(--uhd-fw-extrabold);
  color: #ffffff;
}
.am-row {
  width: 100%;
  min-height: 57px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 2px;
  border: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  background: transparent;
  color: #ffffff;
  font-family: inherit;
  font-size: 17px;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  box-sizing: border-box;
}
.am-row--static {
  cursor: default;
}
.am-row:active {
  opacity: 0.72;
}
.am-row__name {
  flex: 1 1 auto;
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.am-row__value {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.am-row__chev {
  flex: 0 0 auto;
  color: rgba(255, 255, 255, 0.42);
}

/* 外观三档分段：选中＝填充＋轻阴影 */
.am-seg {
  flex: 0 0 auto;
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.09);
}
.am-seg__btn {
  border: none;
  background: transparent;
  padding: 6px 13px;
  border-radius: 999px;
  color: rgba(255, 255, 255, 0.62);
  font-family: inherit;
  font-size: 13.5px;
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}
.am-seg__btn--on {
  background: rgba(255, 255, 255, 0.2);
  color: #ffffff;
  font-weight: var(--uhd-fw-semibold);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14);
}

/* 退出登录：独立整宽胶囊 */
.am-logout {
  width: 100%;
  height: 54px;
  margin-top: 36px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: #17181c;
  color: var(--uhd-danger);
  font-family: inherit;
  font-size: 17px;
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
}
.am-logout:active {
  opacity: 0.78;
}

/* 浅色覆盖：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .am-page { background: #f8f8f8; color: #111111; }
html[data-theme='light'] .am-avatar { background: #e9e9ec; color: #111111; }
html[data-theme='light'] .am-username { color: #111111; }
html[data-theme='light'] .am-status { color: #55575c; }
html[data-theme='light'] .am-copy { color: #55575c; }
html[data-theme='light'] .am-group__title { color: #111111; }
html[data-theme='light'] .am-row { border-bottom-color: #e7e7e7; color: #111111; }
html[data-theme='light'] .am-row__value { color: #9a9da3; }
html[data-theme='light'] .am-row__chev { color: #9a9da3; }
html[data-theme='light'] .am-seg { background: #ececee; }
html[data-theme='light'] .am-seg__btn { color: #55575c; }
html[data-theme='light'] .am-seg__btn--on { background: #ffffff; color: #111111; }
html[data-theme='light'] .am-logout { background: #ffffff; border-color: #e7e7e7; }
</style>
