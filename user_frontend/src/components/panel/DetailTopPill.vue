<!-- 详情页移动端右上玻璃胶囊（2026-11-14 用户截图亲点的友站形态）：
     一颗毛玻璃胶囊里并排「外观切换（月牙/太阳）」与「关闭（X）」。
     外观走 StreamShell 注入的片库面主题口径（三态循环，与顶栏同键
     aetrix-stream-theme，不新造一套）；关闭 = 回上一页（由详情页传入）。
     仅移动端渲染——桌面详情头部没有这颗胶囊（桌面有顶栏承担外观切换）。
     2026-11-15 录屏逐帧核查（f046/f052/f054/f057）修订：
     胶囊实测约 90×38–40 CSS px（此前约 75 宽偏窄），X 带灰色圆片底；
     片库首页（menu 模式）点 X 不再直接退出，而是照友站弹出小菜单：
     用户头（头像字 + 用户名 + 套餐副行）/ 用户中心 / 线路测速 /
     退出登录（红色）。套餐副行走现行 subscriptionApi.getMine 同口径
     （与「我的」页 activeSub 同判定），菜单打开时才拉、不预拉。
     详情页不传 menu，X 保持原关闭行为不变。 -->
<template>
  <div class="dtp-wrap">
    <div class="dtp">
      <button
        v-if="theme"
        type="button"
        class="dtp__btn"
        aria-label="切换深浅外观"
        @click="theme.cycleTheme()"
      >
        <Moon v-if="isDark" :size="17" />
        <Sun v-else :size="17" />
      </button>
      <span v-if="theme" class="dtp__sep" aria-hidden="true"></span>
      <button
        type="button"
        class="dtp__btn dtp__btn--close"
        :aria-label="menu ? '打开用户菜单' : closeLabel"
        :aria-expanded="menu ? menuOpen : undefined"
        @click="onCloseBtn"
      >
        <X :size="15" />
      </button>
    </div>

    <template v-if="menu && menuOpen">
      <div class="dtp-veil" @click="menuOpen = false"></div>
      <div class="dtp-menu" role="menu" aria-label="用户菜单">
        <div class="dtp-menu__head">
          <span class="dtp-menu__avatar">{{ avatarText }}</span>
          <span class="dtp-menu__who">
            <span class="dtp-menu__name">{{ displayName }}</span>
            <span class="dtp-menu__sub">
              <span class="dtp-menu__dot" aria-hidden="true"></span>{{ subLine }}
            </span>
          </span>
        </div>
        <button type="button" class="dtp-menu__row" role="menuitem" @click="go('/game/account')">
          <LayoutGrid :size="17" aria-hidden="true" />
          <span class="dtp-menu__label">用户中心</span>
          <ChevronRight :size="16" class="dtp-menu__chev" aria-hidden="true" />
        </button>
        <button type="button" class="dtp-menu__row" role="menuitem" @click="go('/game/speedtest')">
          <Gauge :size="17" aria-hidden="true" />
          <span class="dtp-menu__label">线路测速</span>
          <ChevronRight :size="16" class="dtp-menu__chev" aria-hidden="true" />
        </button>
        <button type="button" class="dtp-menu__row dtp-menu__row--danger" role="menuitem" @click="onLogout">
          <LogOut :size="17" aria-hidden="true" />
          <span class="dtp-menu__label">退出登录</span>
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref, type ComputedRef, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronRight, Gauge, LayoutGrid, LogOut, Moon, Sun, X } from 'lucide-vue-next'
import { subscriptionApi } from '@/api'
import { useUserStore } from '@/stores/user'

const emit = defineEmits<{ close: [] }>()

/** 关闭钮的无障碍文案：详情页是「关闭详情」；片库首页复用本控件时
    语义是「退出媒体库」，由使用方传入。关闭动作本身走 close 事件。
    menu=true 时（片库首页）X 改为弹出用户菜单（友站 f057 形态），
    不再发 close；退出媒体库的去向由菜单内「用户中心」承担。 */
const props = withDefaults(
  defineProps<{ closeLabel?: string; menu?: boolean }>(),
  { closeLabel: '关闭详情', menu: false },
)

interface StreamThemeCtx {
  pref: Ref<'system' | 'light' | 'dark'>
  resolved: ComputedRef<'light' | 'dark'>
  cycleTheme: () => void
}

/** StreamShell provide 的片库面外观上下文；不在片库面内使用时为 null，
    此时只保留关闭钮（不假造主题能力） */
const theme = inject<StreamThemeCtx | null>('stream-theme', null)
const isDark = computed(() => theme?.resolved.value === 'dark')

/* ---------- 用户菜单（menu 模式，片库首页） ---------- */
const router = useRouter()
const userStore = useUserStore()
const menuOpen = ref(false)
const displayName = computed(() => userStore.user?.username || '用户')
const avatarText = computed(() => displayName.value.charAt(0).toUpperCase())
/** 套餐副行：打开菜单才拉现行订阅口；未拉到前只显用户名、不编造套餐信息 */
const subLine = ref('')
let subLoaded = false

async function loadSubLine() {
  if (subLoaded) return
  subLoaded = true
  try {
    const subs = await subscriptionApi.getMine()
    const active = subs.find((s) => s.status === 'active' && s.days_left > 0)
    subLine.value = active ? `${active.plan_name} · 剩 ${active.days_left} 天` : '普通用户'
  } catch {
    subLine.value = '普通用户'
  }
}

function onCloseBtn() {
  if (!props.menu) {
    emit('close')
    return
  }
  menuOpen.value = !menuOpen.value
  if (menuOpen.value) void loadSubLine()
}

function go(path: string) {
  menuOpen.value = false
  void router.push(path)
}

async function onLogout() {
  menuOpen.value = false
  await userStore.logout()
  void router.push('/login')
}
</script>

<style scoped>
.dtp-wrap {
  position: absolute;
  top: calc(env(safe-area-inset-top, 0px) + 20px);
  right: 16px;
  z-index: 4;
  display: none;
}
.dtp {
  display: flex;
  align-items: center;
  gap: 4px;
  /* 录屏实测胶囊约 90×38–40：内距与钮尺寸按此配平（32 外观钮 + 28 X 圆片） */
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    var(--uhd-s-shadow-pill);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 全局 mobile.css 的 button{min-height:44px} 会撑高胶囊内小钮，本作用域中和 */
.dtp button,
.dtp-menu button {
  min-height: 0;
}
.dtp__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.dtp__btn:active {
  background: rgba(128, 128, 128, 0.18);
}
/* X 圆片底（友站形态：X 坐在浅灰圆片上，圆片 Ø≈28） */
.dtp__btn--close {
  width: 28px;
  height: 28px;
  background: rgba(128, 128, 128, 0.22);
}
.dtp__sep {
  width: 1px;
  height: 16px;
  background: currentColor;
  opacity: 0.16;
}
/* ---------- 用户菜单（f057 形态）：胶囊正下方右对齐的玻璃卡 ---------- */
.dtp-veil {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.dtp-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 41;
  width: 272px;
  padding: 8px;
  border-radius: 20px;
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-panel-bg);
  box-shadow: var(--uhd-shadow-modal);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  color: var(--uhd-s-text);
}
.dtp-menu__head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 10px 12px;
  border-bottom: 1px solid var(--uhd-s-border);
  margin-bottom: 6px;
}
.dtp-menu__avatar {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--uhd-s-text);
  color: var(--uhd-s-bg);
  font-size: 18px;
  font-weight: var(--uhd-fw-bold);
}
.dtp-menu__who {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.dtp-menu__name {
  font-size: 16px;
  font-weight: var(--uhd-fw-bold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dtp-menu__sub {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
}
.dtp-menu__dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
}
.dtp-menu__row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 44px;
  padding: 0 10px;
  border: none;
  border-radius: 12px;
  background: transparent;
  color: var(--uhd-s-text);
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.dtp-menu__row:active {
  background: var(--uhd-s-highlight);
}
.dtp-menu__label {
  flex: 1;
}
.dtp-menu__chev {
  color: var(--uhd-s-text-2);
}
.dtp-menu__row--danger {
  color: #e5484d;
}
@media (max-width: 767px) {
  .dtp-wrap {
    display: block;
  }
}
</style>
