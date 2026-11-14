<script setup lang="ts">
/**
 * UhdAccountLayout —— 用户中心左栏框架（说明书 2.10，批 2 办事面）
 *
 * 用法：办事面页面（/game/* 与 /shop）的根模板包一层即可：
 *   <UhdAccountLayout title="我的订阅" eyebrow="订阅与资格">…原内容…</UhdAccountLayout>
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
  Briefcase, CreditCard, Crown, Gauge,
  Megaphone, Plug, Settings, Sun, Moon, User,
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
        { key: '/game/account/member', label: '我的订阅', icon: Crown },
        { key: '/game/account/subscribe', label: '订阅服务', icon: CreditCard },
        { key: '/game/account/settings', label: '账号与设置', icon: Settings },
      ],
    },
    {
      title: '服务',
      items: [
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
    {
      title: '帮助',
      items: [
        { key: '/game/speedtest', label: '线路测速', icon: Gauge },
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
/* 左栏吸顶满高：账户区上方有 App.vue 常驻的统一顶栏（sticky，
   高 --utb-h），侧栏吸顶位置与高度须让出这一条，滚动时不钻到
   玻璃顶栏底下（2026-11-14 顶栏统一单） */
.uhd-account :deep(.uhd-side) {
  position: sticky;
  top: var(--utb-h);
  height: calc(100vh - var(--utb-h));
  height: calc(100dvh - var(--utb-h));
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
  2026-11-14 统一壳单：账号页（我的订阅/账号设置/代理中心）已摘掉
  .v4-scope 纸面嵌套，页根改挂 .uhd-shell-scope 承接同一套壳内令牌值，
  两选择器并列同值，存量 .v4-scope 页（若再有）口径不变。
-->
<style>
.uhd-account .v4-scope,
.uhd-account .uhd-shell-scope {
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
/* .uhd-shell-scope 页根自承文字色：原 .v4-scope 的 color 由 v4-paper.css
   提供，摘嵌套后按壳内 --ink 值在此补齐，免继承全局主题文字色漂移 */
.uhd-account .uhd-shell-scope { color: var(--ink); }
.uhd-account .v4-scope,
.uhd-account .uhd-shell-scope {
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
/* 桌面并壳页贴合（2026-11-14 统一壳单）：经 UhdAccountShellGate 并入壳的
   全幅页（概览/订阅服务/订阅记录/邀请/团队/安装）原本是独立整页——自带
   100dvh 高与移动返回顶栏。壳只在桌面挂载，故下述规则只在桌面生效：
   - 高度：扣掉统一顶栏与布局 main 的上下内边距（28+120），不再溢出
     一截造无谓滚动；
   - 移动返回顶栏在壳内隐藏（侧栏常驻即返回路径，不再层层递进）；
     订阅服务页头只摘返回钮（标题与「我的订阅」入口是真实内容，保留）；
   - 订阅吸底结算条让开侧栏宽度，不再压住侧栏。 */
.uhd-account .ov-page,
.uhd-account .sub-page,
.uhd-account .sr-page,
.uhd-account .iv-page,
.uhd-account .tm-page,
.uhd-account .ia-page {
  min-height: calc(100dvh - var(--utb-h) - 148px);
}
/* 特异性须压过各页 scoped 的「类+data-v 属性」（0,2,0），故选择器带页根类走（0,3,0） */
.uhd-account .sr-page .sr-topbar,
.uhd-account .iv-page .iv-topbar { display: none; }
.uhd-account .ia-page .ia-back,
.uhd-account .sub-page .sub-back { display: none; }
.uhd-account .sub-page .sub-settle { left: var(--uhd-sidebar-w); }
.uhd-account .ia-page { padding: 0 0 48px; }
</style>
