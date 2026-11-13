<script setup lang="ts">
/**
 * UhdAccountLayout —— 用户中心左栏框架（说明书 2.10，批 2 办事面）
 *
 * 用法：办事面页面（/game/* 与 /shop）的根模板包一层即可：
 *   <UhdAccountLayout title="我的会员" eyebrow="订阅与资格">…原内容…</UhdAccountLayout>
 * - 左栏 250px 白底（UhdSidebar，批 0 组件），分组导航与主导航同源；
 * - 内容区约 1000px、H1 32–36px；≤768px 左栏隐藏、走底部坞（App.vue 常驻）。
 * - 皮肤：非 scoped 样式块把 .v4-scope 的纸色令牌在 .uhd-account 作用域内
 *   覆盖为 UHD 浅色令牌（#f8f8f8 底 / 白卡 / #e5e5e5 边 / 蓝主色），
 *   各页原有结构与业务逻辑一字不动，卡片/行/按钮随令牌自动换皮。
 * - 代理中心只对代理可见：getAgentMe 成功才显示入口（失败静默隐藏，
 *   与 PersonalCenterView 同口径；模块级缓存，一次会话只查一次）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Briefcase, CreditCard, Crown,
  Megaphone, Plug, Receipt, Settings, ShoppingBag, Sun, Moon, User, Wallet,
} from 'lucide-vue-next'
import UhdSidebar, { type UhdSidebarGroup } from './UhdSidebar.vue'
import { getAgentMe } from '@/api/agent'
import { useTheme } from '@/composables/useTheme'
import { useUserStore } from '@/stores/user'

const props = withDefaults(
  defineProps<{
    /** 内容区 H1（不传则不渲染布局头，页面自带 .v4-loghead 时用那套） */
    title?: string
    /** H1 上方眉题 */
    eyebrow?: string
    /** 强制激活项（默认取当前路由 path） */
    activeKey?: string
    /** showcase 预览：不跳转，只 emit select */
    navigate?: boolean
  }>(),
  { title: '', eyebrow: '', activeKey: '', navigate: true },
)

const emit = defineEmits<{ select: [key: string] }>()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { preference: themePreference, resolved: themeResolved, setPreference: setThemePreference } = useTheme()

const active = computed(() => props.activeKey || route.path)

/* 代理入口可见性（模块级缓存） */
let agentChecked: Promise<boolean> | null = null
function checkAgent(): Promise<boolean> {
  if (!agentChecked) {
    agentChecked = getAgentMe()
      .then((me) => me.is_agent)
      .catch(() => false)
  }
  return agentChecked
}
const showAgent = ref(false)
onMounted(async () => {
  showAgent.value = await checkAgent()
})

const groups = computed<UhdSidebarGroup[]>(() => {
  const list: UhdSidebarGroup[] = [
    {
      title: '我的',
      items: [
        { key: '/game/account', label: '我的概览', icon: User },
        { key: '/game/account/member', label: '我的会员', icon: Crown },
        { key: '/game/account/subscribe', label: '订阅服务', icon: CreditCard },
        { key: '/game/account/settings', label: '账号与设置', icon: Settings },
      ],
    },
    {
      title: '资产',
      items: [
        { key: '/game/ledger', label: '能量明细', icon: Wallet },
        { key: '/game/supply', label: '商店记录', icon: Receipt },
      ],
    },
    {
      title: '商店',
      items: [
        { key: '/shop', label: '商店', icon: ShoppingBag },
        { key: '/game/requests', label: '求片反馈', icon: Megaphone },
        ...(showAgent.value
          ? [{ key: '/game/agent', label: '代理中心', icon: Briefcase }]
          : []),
      ],
    },
    {
      title: '探险设施',
      items: [
        { key: '/game/operator', label: '接线员', icon: Plug },
      ],
    },
  ]
  return list
})

function onSelect(key: string) {
  if (props.navigate) void router.push(key)
  else emit('select', key)
}

function goAccount() {
  if (props.navigate) void router.push('/game/account')
  else emit('select', '/game/account')
}

/* 外观切换：与 AppHeader 同口径（system → 按当前解析值切到另一档） */
function cycleTheme() {
  const cur = themePreference.value
  if (cur === 'system') setThemePreference(themeResolved.value === 'light' ? 'dark' : 'light')
  else setThemePreference(cur === 'light' ? 'dark' : 'light')
}
const themeTitle = computed(
  () => `外观：${themeResolved.value === 'light' ? '白日' : '黑暗'}（点击切换）`,
)

const displayName = computed(() => userStore.user?.username || '我的')
const avatarInitial = computed(() => (userStore.user?.username || '账').slice(0, 1).toUpperCase())
const accountSub = computed(() => {
  if (userStore.isFreeRealm) return '公益服'
  if (userStore.isVIP) return '会员'
  return '普通用户'
})
</script>

<template>
  <div class="uhd-account">
    <UhdSidebar :groups="groups" :active-key="active" @select="onSelect">
      <template #account>
        <button type="button" class="uhd-account__who" @click="goAccount">
          <span class="uhd-account__avatar" aria-hidden="true">{{ avatarInitial }}</span>
          <span class="uhd-account__who-text">
            <span class="uhd-account__who-name">{{ displayName }}</span>
            <span class="uhd-account__who-sub">{{ accountSub }}</span>
          </span>
        </button>
      </template>
    </UhdSidebar>

    <main class="uhd-account__main">
      <header v-if="title" class="uhd-account__head">
        <div>
          <p v-if="eyebrow" class="uhd-account__eyebrow">{{ eyebrow }}</p>
          <h1 class="uhd-account__title">{{ title }}</h1>
        </div>
        <button type="button" class="uhd-account__theme" :title="themeTitle" :aria-label="themeTitle" @click="cycleTheme">
          <Sun v-if="themeResolved === 'dark'" :size="18" />
          <Moon v-else :size="18" />
        </button>
      </header>
      <slot />
    </main>
  </div>
</template>

<style scoped>
.uhd-account {
  display: flex;
  align-items: stretch;
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--uhd-l-bg);
  font-family: var(--uhd-font-sans);
}
/* 左栏吸顶满高 */
.uhd-account :deep(.uhd-side) {
  position: sticky;
  top: 0;
  height: 100vh;
  height: 100dvh;
}
.uhd-account__main {
  flex: 1;
  min-width: 0;
  padding: 28px 32px 120px;
}
.uhd-account__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  max-width: var(--uhd-container-app);
  margin: 0 auto 20px;
}
.uhd-account__eyebrow {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  letter-spacing: var(--uhd-ls-eyebrow);
  text-transform: uppercase;
  color: var(--uhd-l-text-3);
}
.uhd-account__title {
  margin: 0;
  /* D1-09：标题一律无衬线特粗。全局 aurora.css 有 h1/h2/h3 衬线规则
     （--au-font-serif 宋体）直命中元素，这里用类声明 UHD 无衬线令牌压过它；
     全局规则本身的修订归批 3/首页单，本文件只管用户中心作用域。 */
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-h1-app);
  font-weight: var(--uhd-fw-extrabold);
  line-height: var(--uhd-lh-tight);
  color: var(--uhd-l-text);
  letter-spacing: -0.01em;
}
.uhd-account__theme {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex: none;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: 50%;
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  cursor: pointer;
}
.uhd-account__theme:hover { background: var(--uhd-l-input-bg); }
/* 侧栏底部账号胶囊 */
.uhd-account__who {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 4px;
  border: none;
  border-radius: var(--uhd-radius-navitem);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.uhd-account__who:hover { background: var(--uhd-l-input-bg); }
.uhd-account__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 50%;
  background: var(--uhd-l-text);
  color: var(--uhd-l-surface);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-bold);
}
.uhd-account__who-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.3;
}
.uhd-account__who-name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.uhd-account__who-sub {
  font-size: var(--uhd-fs-aux-sm);
  color: var(--uhd-l-text-3);
}
@media (max-width: 768px) {
  .uhd-account :deep(.uhd-side) { display: none; }
  .uhd-account__main { padding: 20px 16px 120px; }
}
</style>

<!--
  皮肤覆盖（非 scoped）：.v4-scope 自己重定义了纸色令牌，祖先继承赢不了它，
  须用更高特异性的 .uhd-account .v4-scope 在办事面作用域内逐项覆盖为 UHD 浅色值。
  只动令牌与版式，不动任何业务标记与行为。
-->
<style>
.uhd-account .v4-scope {
  --paper: #f8f8f8;
  --paper2: #f5f5f5;
  --ink: #111111;
  --ink2: #6b7280;
  --line: #e5e5e5;
  --paper-card: #ffffff;
  --blue: #2563eb;
  --green: #16a34a;
  --red: #dc2626;
  --gold: #b45309;
  --gold-tint: #fef3c7;
  --line-deep: #9ca3af;
  --paper-r-sm: 8px;
  --paper-r-md: 10px;
  --paper-r-lg: 16px;
  --paper-r-xl: 20px;
  --paper-r-2xl: 24px;
  background: var(--uhd-l-bg);
  background-image: none;
}
.uhd-account .v4-scope {
  --paper-bg: var(--paper);
  --paper-bg-soft: var(--paper2);
  --paper-ink: var(--ink);
  --paper-ink-2: var(--ink2);
  --paper-line: var(--line);
  --paper-red: var(--red);
  --paper-blue: var(--blue);
  --paper-green: var(--green);
  --paper-gold: var(--gold);
}
/* 主色（浅色纸厅深青 #016a71 → UHD 链接蓝 #2563eb）与渐变/焦点环 */
.uhd-account {
  --paper-primary: #2563eb;
  --paper-primary-strong: #1d4ed8;
  --paper-primary-soft: rgba(37, 99, 235, 0.08);
  --paper-primary-border: rgba(37, 99, 235, 0.28);
  --paper-border-focus: rgba(37, 99, 235, 0.35);
  --paper-gradient: linear-gradient(#2563eb, #2563eb);
}
/* 页面容器：桌面放宽到办事面 1000px（原 430px 手机容器）；手机保持原口径 */
@media (min-width: 1024px) {
  .uhd-account .v4-page { max-width: 1000px; }
  /* 个人中心与账号设置自带 1120px 桌面宽，布局内统一收回 1000px */
  .uhd-account .v4-page.pc-page,
  .uhd-account .v4-page.settings-page { max-width: 1000px; }
}
/* 补给站自定义头：H1 32–36px/800（与 .v4-loghead 同口径）；
   font-family 同 D1-09：压过 aurora.css 的全局衬线标题规则 */
.uhd-account .shop-head h1 { font-family: var(--uhd-font-sans); font-size: 34px; font-weight: 800; letter-spacing: -0.01em; }
/* 日志头：H1 32–36px/800、1px 分隔线（原 24px/500、2px 墨线） */
.uhd-account .v4-loghead {
  padding: 26px 4px 14px;
  border-bottom: 1px solid var(--line);
}
.uhd-account .v4-loghead h1 {
  font-family: var(--uhd-font-sans);
  font-size: 34px;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.uhd-account .v4-loghead p { font-size: 14px; }
/* 分区标题：H2 24–28px/800（原 17–18px 级） */
.uhd-account .v4-sec-title { font-family: var(--uhd-font-sans); font-size: 24px; font-weight: 800; letter-spacing: -0.01em; }
.uhd-account .v4-sec-sub { font-size: 14px; color: var(--ink2); }
/* 个人中心 / 账号设置分组标题：同 H2 口径 */
.uhd-account .pc-gtitle { font-family: var(--uhd-font-sans); font-size: 24px; font-weight: 800; letter-spacing: -0.01em; }
/* 空态虚线卡：210px 高、16px 圆角、15px 灰字（说明书 2.7） */
.uhd-account .v4-empty {
  min-height: 210px;
  border-radius: 16px;
  font-size: 15px;
  color: var(--ink2);
}
/* 卡片圆角随令牌走 16–20px；行间分隔用 #eee */
.uhd-account .v4-card { border-radius: 16px; }
</style>
