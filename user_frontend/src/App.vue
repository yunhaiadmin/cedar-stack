<script setup lang="ts">
import { RouterView, useRoute, useRouter } from 'vue-router'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import Toast from '@/components/Toast.vue'
import AppHeader from '@/components/AppHeader.vue'
import PanelTabbar from '@/components/panel/PanelTabbar.vue'
import UhdAccountDock from '@/components/uhd/UhdAccountDock.vue'
import TgBindModal from '@/components/TgBindModal.vue'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import { usePanelStore } from '@/stores/panel'
import { refreshEmbyBaseUrl } from '@/api/emby'
import { tgApi } from '@/api/tg'

const { messages, remove, warning } = useToast()
const userStore = useUserStore()
const panelStore = usePanelStore()

/* 账号切换（含退出变 null）时清空面板缓存：新账号不许看到上一个账号的面板数据。
   watch 默认非 immediate，挂载时不触发；id 未变时回调内再跳过一次，双保险。 */
watch(
  () => userStore.user?.id ?? null,
  (id, prevId) => {
    if (id === prevId) return
    panelStore.reset()
    // 切号后按路由重载，避免面板卡空态
    if (route.path.startsWith('/collector')) {
      void panelStore.loadCollector(true)
    } else if (route.path.startsWith('/server')) {
      void panelStore.loadServer(true)
    } else if (route.path.startsWith('/game')) {
      void panelStore.loadCollector(true)
    }
    // 旧发现页（loadHome 的今日/包裹/脉搏/信件面）已删除，不再预载。
  },
)
const route = useRoute()

/**
 * 全局导航壳：同一份导航定义（src/config/navigation.ts），两种断点形态：
 *
 *   ≥769px  顶栏：品牌 + 主导航（≥900px 横排、769~900px 顶栏第二行滑动选项卡）
 *   ≤768px  底部坞（PanelTabbar，纸色）：拇指区常驻 Tab 栏是移动端的肌肉记忆，
 *           条目与顶栏完全同源、不增不减；桌面 ≥769px 由 CSS 隐藏。
 *
 * 页面底部留白用 --au-dock-space 跟着坞的高度走：≥769px 为 0，≤768px 给坞让位。
 */
const showChrome = computed(
  // 登录页、UHD 营销面（官网/定价，自带悬浮胶囊导航）与 404 页为独立全幅版式，
  // 不渲染全局顶栏/底坞；其余页面照旧。
  // 根路径未登录渲染官网（RootView 分发，自带导航）同样不叠全局壳；
  // 已登录根路径会被守卫送去概览 /game/account（自带壳），不会停在这里。
  () =>
    !['login', 'landing', 'pricing', 'not-found'].includes(route.name as string) &&
    !(route.path === '/' && !userStore.isLoggedIn),
)

/**
 * UHD 换皮批 2：办事面（/game/* 与 /shop）走 UhdAccountLayout 左栏框架，
 * 全局顶栏在这些路由下不再渲染（导航由左栏承担）。
 *
 * UHD 订阅/用户中心单（2026-11-13）：手机底部坞按路由区域分两套——
 * 用户中心区域渲染玻璃双页签 UhdAccountDock（概览/我的），不再渲染
 * PanelTabbar；片库区域（批 3 的 isStreamArea）渲染它自己的双玻璃块，
 * 两区域路径不重叠、条件互斥，不会并存。订阅页自身有吸底结算条占底，
 * 双页签在该页隐藏（isSubscribePage）。
 * 变基对齐批 3 后，PanelTabbar 的条件应为 showChrome && !isAccountArea && !isStreamArea。
 */
const isAccountArea = computed(() => route.path.startsWith('/game'))
const isSubscribePage = computed(() => route.name === 'game-account-subscribe')

/**
 * UHD 换皮批 3：片库面（/library*、/search、/media/*、/favorites）自带
 * 双胶囊顶栏与手机液态玻璃底部导航（StreamShell），全局顶栏与
 * PanelTabbar 在这些路由下都不再渲染，避免双导航并存。
 * 变基 2026-11-13：/play 也计入片库面（播放页隐藏全局顶栏/底坞，
 * 与 #85 web-player 全屏覆盖一致）。
 */
const isStreamArea = computed(
  () =>
    route.path === '/library' ||
    route.path.startsWith('/library/') ||
    route.path === '/search' ||
    route.path.startsWith('/media/') ||
    route.path === '/favorites' ||
    route.path === '/play' ||
    route.path.startsWith('/play/'),
)
const showHeader = computed(() => showChrome.value && !isAccountArea.value && !isStreamArea.value)

/**
 * 底部坞改走 App.vue 全局常驻（PanelTabbar，≤768px 唯一一套）：
 * 各视图不再自带 <PanelTabbar/>，非 v4 路由（/tg-bind 等）也认同一套，
 * 桌面 ≥769px 由 CSS 隐藏（DESIGN.md v1.1：桌面导航只留顶栏）。
 */

/**
 * 常驻缓存视图（个人中心 PersonalCenterView 等）常驻缓存：切 tab 不再销毁重建，
 * 回来秒出上次的数据，各视图在 onActivated 里做静默刷新（不闪骨架屏）。
 *
 * 缓存 key = 路由 path + 用户 id + 登录会话序列：
 * - 同一 tab 内的实例复用，滚动位置、选项卡状态都保留；
 * - 登出 / 换号 / 重新登录后旧实例不再复用，不会看到上一个登录态的数据；
 * - 未列入 include 的页面（登录等）不受影响，照常每次重建。
 *
 * 2026-10-13：钱包页已删，WalletView 移出缓存；旧页面清理，
 * ProfileView（/profile 已删）→ AccountSettingsView（/game/account）。
 * 2026-11-13：商店整店删除，ShopView 移出缓存。
 * 2026-11-06：/game/account 改为个人中心 PersonalCenterView，缓存对象随之更换；
 * 账号设置（/game/account/settings）与我的会员（/game/account/member）是子页，不进缓存。
 */
// RootView 是根路径分发组件（官网面），一并常驻缓存
const KEEP_ALIVE_VIEWS = ['RootView', 'PersonalCenterView']
const viewCacheKey = computed(
  () => `${route.path}:${userStore.user?.id ?? 'guest'}#${userStore.sessionSeq}`,
)

const router = useRouter()

let lastTgNotify = 0
function onTgNotBound(e: Event) {
  // 节流：多接口同时 403 时只处理一次
  const now = Date.now()
  if (now - lastTgNotify < 3000) return
  lastTgNotify = now
  const message = (e as CustomEvent)?.detail?.message || '公益服要绑定 Telegram 才能用，正在带你去绑定'
  warning(message + '，正在前往绑定')
  setTimeout(() => router.push('/game/account'), 600)
}

// ===== 全局 TG 绑定自动弹窗 =====
// 新用户注册后（或任何未绑定用户进入应用时），自动弹出绑定弹窗。
// 这是 LoginView → /tg-bind 页面跳转之外的第二道保险：即使跳转被绕过，
// 用户依然会看到弹窗。已绑定用户永远不会弹。
const showGlobalTgBind = ref(false)
const TG_GUIDE_SHOWN_KEY = 'tg_bind_guide_shown'

async function checkTgBindGuide() {
  // 未登录不查
  if (!userStore.isLoggedIn) return
  // 登录页、/tg-bind 引导页不打扰（2026-10-13：播放页已删）
  if (route.name === 'login' || route.path === '/tg-bind') return
  // 每个标签页会话只自动弹一次，避免刷新反复打扰
  if (sessionStorage.getItem(TG_GUIDE_SHOWN_KEY)) return
  try {
    const st = await tgApi.status()
    // 已绑定、或后端没要求绑定、或引导被管理员关闭 → 不弹
    if (!st.required || st.bound || !st.guide_enabled) return
    sessionStorage.setItem(TG_GUIDE_SHOWN_KEY, '1')
    showGlobalTgBind.value = true
  } catch {
    // 状态拉取失败：静默，不锁死用户
  }
}

function handleGlobalTgBound() {
  // 绑定成功后：标记已展示，下次不再自动弹（st.bound=true 也不会再弹，双保险）
  sessionStorage.setItem(TG_GUIDE_SHOWN_KEY, '1')
}

onMounted(() => {
  window.addEventListener('tg-not-bound', onTgNotBound)
  userStore.init()
  // P0#1：后台重新校验 EA 地址（8001→8002 这类变更 1 小时内自动纠正，不阻塞首屏）
  refreshEmbyBaseUrl()
  // 有登录 token 时后台刷新一次用户信息：购买订阅后 localStorage 里缓存的
  // is_vip 会过期，不刷新则媒体库详情页一直显示"没有生效中的订阅"
  //（2026-09-26 线上实测）。401 时拦截器会自动用 refresh token 续期。
  if (userStore.isLoggedIn) {
    userStore.fetchUser().catch(() => {})
  }
  // TG 绑定引导：延迟 1.5s 等首屏稳定后检查，避免与首屏加载抢资源
  setTimeout(() => { checkTgBindGuide() }, 1500)
})
onUnmounted(() => {
  window.removeEventListener('tg-not-bound', onTgNotBound)
})

// 登录状态变化（注册/登录成功）时检查一次：覆盖 LoginView 跳转被绕过的场景
watch(
  () => userStore.isLoggedIn,
  (loggedIn) => {
    if (loggedIn) {
      setTimeout(() => { checkTgBindGuide() }, 1500)
    } else {
      // 登出后清除标记，下次登录重新评估
      sessionStorage.removeItem(TG_GUIDE_SHOWN_KEY)
      showGlobalTgBind.value = false
    }
  },
)
// 路由切换时也检查一次（比如注册后直接落在首页的场景）
watch(
  () => route.path,
  () => {
    // 延迟到路由稳定后
    setTimeout(() => { checkTgBindGuide() }, 800)
  },
)
</script>

<template>
  <div class="app-shell">
    <AppHeader v-if="showHeader" />
    <RouterView v-slot="{ Component }">
      <!-- max=12：key 按登录会话隔离，登出/换号后旧实例不再命中，攒多了自动淘汰最旧的 -->
      <KeepAlive :include="KEEP_ALIVE_VIEWS" :max="12">
        <Transition name="page">
          <component :is="Component" :key="viewCacheKey" />
        </Transition>
      </KeepAlive>
    </RouterView>
    <PanelTabbar v-if="showChrome && !isAccountArea && !isStreamArea" />
    <UhdAccountDock v-if="showChrome && isAccountArea && !isSubscribePage" />
    <Toast :messages="messages" @remove="remove" />
    <!-- 全局 TG 绑定自动弹窗：新用户注册后自动弹出，已绑定不弹 -->
    <TgBindModal v-model="showGlobalTgBind" @bound="handleGlobalTgBound" />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  min-height: 100dvh;   /* 移动端地址栏收放时高度不跳 */
  background: var(--au-bg);
}

/* 顶栏是 sticky 的，占文档流；登录页全屏居中、播放页全黑无栏，两者已由 showChrome 排除 */

/* ===== 页面转场：新页面立即挂载、180ms 淡入（只动 opacity）；旧页面无淡出，导航零等待 ===== */
.page-enter-active {
  transition: opacity var(--au-dur-fast, 180ms) var(--au-ease, ease);
}
.page-enter-from {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .page-enter-active {
    transition: none;
  }
}
</style>
