<script setup lang="ts">
/**
 * 官网 www 首页（UHD 换皮批 1 · 深色营销面）——说明书逐页对照表第 18 行
 * 五区块骨架：100vh Hero（棱镜光束 + 文案轮换 + 规格 chip）→ 客户端/规格跑马灯
 * → 钉住滚动影院/音频展示（钉住只此一处，说明书第 5 节①）→ 一份订阅三卡 → 单行页脚。
 * 文案为 Aetrix 自有口径；营销数字一律不编造：片库统计位为占位待接（第 5 节④）。
 * 页面独立于应用壳（App.vue 对 landing 路由名不渲染全局顶栏/底坞），自带悬浮胶囊导航。
 */
import { onMounted, onUnmounted, ref, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { Monitor, Sun, Layers, ShieldCheck, Menu, X, RefreshCw, Bell, Sparkles, Home, Tag, BookOpen, ExternalLink, Users, Bot, MessageCircle, Send, Globe, ArrowRight } from 'lucide-vue-next'
import UhdNavPill, { type UhdNavItem } from '@/components/uhd/UhdNavPill.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdChip from '@/components/uhd/UhdChip.vue'
import { branding } from '@/composables/useBranding'
import { communityLinks } from '@/composables/useCommunityLinks'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const navItems: UhdNavItem[] = [
  { key: 'home', label: '首页' },
  { key: 'pricing', label: '定价' },
  { key: 'login', label: '登录' },
]

function onNav(key: string) {
  if (key === 'pricing') router.push('/pricing')
  else if (key === 'login') router.push('/login')
  else window.scrollTo({ top: 0, behavior: 'smooth' })
}
/* 移动端菜单＝底部抽屉（原站 _public 头部组件源码形态）：遮罩＋上滑面板，
   开合带动画；顶栏滚动 >40px 时整条玻璃浮起、汉堡 chip 变平（原站 pub-nav-pill 形态） */
const menuOpen = ref(false)
const menuClosing = ref(false)
const navScrolled = ref(false)
function onScrollNav() {
  navScrolled.value = window.scrollY > 40
}
function setMenu(open: boolean) {
  menuOpen.value = open
  menuClosing.value = false
  document.documentElement.style.overflow = open ? 'hidden' : ''
}
function closeMenuAnimated() {
  if (!menuOpen.value || menuClosing.value) return
  menuClosing.value = true
  document.documentElement.style.overflow = ''
  window.setTimeout(() => setMenu(false), 300)
}
function toggleMenu() {
  if (menuOpen.value) closeMenuAnimated()
  else setMenu(true)
}
function onMobileNav(key: string) {
  setMenu(false)
  onNav(key)
}
/* 菜单面板下半（2026-11-13 用户发友站截图定版，整体 1:1）：
   文档行与「社区渠道」卡片全部来自「官网社区渠道」能力配置（后台可配），
   未配的项不渲染、四格全空时整个小节不出现；底部大钮「进入后台」
   已登录进用户中心概览、未登录去登录页。 */
/* 渠道图标种类 → 线性图标组件（与后台配置的图标选项一一对应） */
const CHANNEL_ICONS: Record<string, Component> = {
  bell: Bell,
  users: Users,
  bot: Bot,
  message: MessageCircle,
  send: Send,
  globe: Globe,
}
function channelIcon(kind: string): Component {
  return CHANNEL_ICONS[kind] || MessageCircle
}
function goConsole() {
  setMenu(false)
  router.push(userStore.isLoggedIn ? '/game/account' : '/login')
}

/* 拖拽条下滑关闭：按住把手向下拖，面板跟手；松手过阈值（90px）关，
   没过回弹。拖拽期间关掉过渡动画，位移直接写在面板上。 */
const dragY = ref(0)
const dragging = ref(false)
let dragStartY = 0
let dragPointerId = -1
function onHandlePointerDown(event: PointerEvent) {
  if (dragging.value) return
  dragging.value = true
  dragPointerId = event.pointerId
  dragStartY = event.clientY
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}
function onHandlePointerMove(event: PointerEvent) {
  if (!dragging.value || event.pointerId !== dragPointerId) return
  dragY.value = Math.max(0, event.clientY - dragStartY)
}
function onHandlePointerUp(event: PointerEvent) {
  if (!dragging.value || event.pointerId !== dragPointerId) return
  dragging.value = false
  const shouldClose = dragY.value > 90
  dragY.value = 0
  if (shouldClose) closeMenuAnimated()
}
const sheetDragStyle = () => {
  if (menuClosing.value || !dragging.value) return undefined
  return { transform: `translateY(${dragY.value}px)`, transition: 'none' }
}

function goRegister() {
  router.push({ path: '/login', query: { mode: 'register' } })
}
function goPricing() {
  router.push('/pricing')
}

/* Hero 副文案轮换（仅营销面，说明书 1.6 / 第 5 节⑦） */
const heroSubs = [
  '无广告干扰，沉浸观影从按下播放开始',
  '手机、电视、电脑，观看进度多端同步',
  '新片自动入库，追剧更新不用催',
]
const heroSubIdx = ref(0)
let heroTimer: ReturnType<typeof setInterval> | null = null

/* 钉住滚动展示：滚动进度驱动两态模糊交叉淡入（动态返工，原站 GSAP 时间线逐项实测，
   见 dynamics-spec.md ②：钉住行程 140% 视口、影院块 .32–.50 模糊退出、音频三件 .50–.82 依次模糊入场） */
const pinSection = ref<HTMLElement | null>(null)
const pinProgress = ref(0)
/* Hero 离场进度：首个视口行程 0→1，驱动 mask --reveal 等值（原站公式，见 spec ①） */
const heroProgress = ref(0)
/* 钉住退场视差进度：钉住结束后 stage 离场的一个视口行程 0→1 */
const pinExit = ref(0)
/* 光柱是否在屏（离屏暂停自主动画） */
const pinInView = ref(false)
let rafPending = false
function clamp01(v: number) {
  return Math.min(1, Math.max(0, v))
}
/* 归一化时间线分段进度 */
function seg(t: number, start: number, dur: number) {
  return clamp01((t - start) / dur)
}
function updatePin() {
  rafPending = false
  heroProgress.value = clamp01(window.scrollY / window.innerHeight)
  const el = pinSection.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const total = rect.height - window.innerHeight
  if (total <= 0) return
  pinProgress.value = clamp01(-rect.top / total)
  pinExit.value = clamp01((-rect.top - total) / window.innerHeight)
}
function onScroll() {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(updatePin)
}

/* 两态样式（reduced-motion 时退回纯 opacity 交叉：无 blur/位移/缩放） */
const reduceMotion = ref(false)
function blurPx(v: number) {
  return reduceMotion.value ? 'blur(0px)' : `blur(${v.toFixed(1)}px)`
}
const cinemaCopyStyle = () => {
  const t = pinProgress.value
  const k = seg(t, 0.32, 0.18)
  const rm = reduceMotion.value
  return {
    opacity: String(1 - k),
    filter: blurPx(14 * k),
    transform: rm ? 'none' : `translateY(${(-50 * k).toFixed(1)}px)`,
    visibility: k >= 1 ? ('hidden' as const) : ('visible' as const),
  }
}
/* 音频三件：idx 0=眉题 1=标题 2=副文案，参数照原站 fromTo（spec ② 表） */
const AUDIO_PARTS = [
  { start: 0.5, dur: 0.18, y: 60, blur: 14, scaleFrom: 1 },
  { start: 0.54, dur: 0.22, y: 110, blur: 22, scaleFrom: 0.92 },
  { start: 0.62, dur: 0.2, y: 60, blur: 12, scaleFrom: 1 },
]
function audioPartStyle(idx: number) {
  const p = AUDIO_PARTS[idx]
  const k = seg(pinProgress.value, p.start, p.dur)
  const rm = reduceMotion.value
  const style: Record<string, string> = {
    opacity: String(k),
    filter: blurPx(p.blur * (1 - k)),
    visibility: k <= 0 ? 'hidden' : 'visible',
  }
  if (!rm) {
    style.transform =
      `translateY(${(p.y * (1 - k)).toFixed(1)}px)` +
      (p.scaleFrom !== 1 ? ` scale(${(p.scaleFrom + (1 - p.scaleFrom) * k).toFixed(3)})` : '')
    if (idx === 0) style.letterSpacing = `${(0.7 - 0.26 * k).toFixed(3)}em`
  }
  return style
}
const vignetteStyle = () => ({ opacity: String(seg(pinProgress.value, 0.3, 0.22)) })
const pinVisualStyle = () => {
  const t = pinProgress.value
  const rm = reduceMotion.value
  const scale = rm ? 1 : 1 + 0.06 * seg(t, 0.3, 0.6)
  const y = rm ? 0 : pinExit.value * 60
  return { transform: `scale(${scale.toFixed(3)}) translateY(${y.toFixed(1)}px)` }
}
const pinDriftStyle = () => {
  const rm = reduceMotion.value
  return { transform: rm ? 'none' : `translateX(${(-16 * pinProgress.value).toFixed(1)}px)` }
}
/* 声场圆环：三环依次扩散（原站 .42/.49/.56 起，入 .18、出 .28） */
function ringStyle(i: number) {
  const t = pinProgress.value
  const start = 0.42 + i * 0.07
  const kin = seg(t, start, 0.18)
  const kout = seg(t, start + 0.18, 0.28)
  const rm = reduceMotion.value
  const scale = rm ? 1 : 0.55 + 0.5 * kin + 0.6 * kout
  return {
    opacity: String(0.6 * kin * (1 - kout)),
    transform: `translate(-50%, -50%) scale(${scale.toFixed(3)})`,
    visibility: kin <= 0 ? ('hidden' as const) : ('visible' as const),
  }
}
/* Hero 文字层离场 mask（原站公式逐字采用：--reveal 取 heroProgress） */
const heroInnerStyle = () => {
  const r = heroProgress.value
  if (reduceMotion.value || r <= 0) return {}
  const mask = `linear-gradient(to top, transparent ${(r * 100 - 20).toFixed(1)}%, black ${(r * 120).toFixed(1)}%, black 100%)`
  return { maskImage: mask, WebkitMaskImage: mask }
}
const heroHintStyle = () => ({ opacity: String(1 - clamp01(heroProgress.value * 2.5)) })

/* ---------- 区块 reveal（原站 GSAP 模式的等价实现，见 spec ④） ----------
   [data-reveal] 元素初始为 from 态（CSS .rv* 类），IO 在元素 top 到达视口约 75% 时加 .is-in 播放入场；
   reverse：滚出后移除 .is-in（与原站 toggleActions play/reverse 同口径）。 */
const landingRoot = ref<HTMLElement | null>(null)
const platformsTitle = ref<HTMLElement | null>(null)
let revealIO: IntersectionObserver | null = null
let pinRevealIO: IntersectionObserver | null = null
let pinIO: IntersectionObserver | null = null
function splitTitleChars() {
  const el = platformsTitle.value
  if (!el || el.dataset.splitDone) return
  el.dataset.splitDone = '1'
  const nodes = [...el.childNodes]
  el.textContent = ''
  let idx = 0
  for (const node of nodes) {
    if (node.nodeName === 'BR') {
      el.appendChild(document.createElement('br'))
      continue
    }
    for (const ch of node.textContent ?? '') {
      const wrap = document.createElement('span')
      wrap.className = 'pt-char-wrap'
      const inner = document.createElement('span')
      inner.className = 'pt-char'
      inner.style.transitionDelay = `${(0.1 + idx * 0.035).toFixed(3)}s`
      inner.textContent = ch === ' ' ? ' ' : ch
      wrap.appendChild(inner)
      el.appendChild(wrap)
      idx++
    }
  }
}
function setupReveal() {
  const root = landingRoot.value
  if (!root) return
  const els = root.querySelectorAll('[data-reveal]')
  const pinEls = root.querySelectorAll('[data-reveal-pin]')
  if (reduceMotion.value || !('IntersectionObserver' in window)) {
    els.forEach((e) => e.classList.add('is-in'))
    pinEls.forEach((e) => e.classList.add('is-in'))
    root.querySelectorAll('.pt-char').forEach((e) => e.classList.add('is-in'))
    return
  }
  revealIO = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (en.target === platformsTitle.value) {
          en.target.querySelectorAll('.pt-char').forEach((c) => c.classList.toggle('is-in', en.isIntersecting))
        } else {
          en.target.classList.toggle('is-in', en.isIntersecting)
        }
      }
    },
    { rootMargin: '0px 0px -25% 0px', threshold: 0 },
  )
  els.forEach((e) => revealIO?.observe(e))
  if (platformsTitle.value) revealIO.observe(platformsTitle.value)
  /* 影院文案入场照原站：以 section top 到达视口 70% 为触发（rootMargin 底 −30%），
     不能按子元素自身位置触发——子元素钉在 stage 下半区、top 永远到不了 75% 线 */
  if (pinSection.value) {
    pinRevealIO = new IntersectionObserver(
      (entries) => {
        for (const en of entries) pinEls.forEach((e) => e.classList.toggle('is-in', en.isIntersecting))
      },
      { rootMargin: '0px 0px -30% 0px', threshold: 0 },
    )
    pinRevealIO.observe(pinSection.value)
  }
}

onMounted(() => {
  window.addEventListener('scroll', onScrollNav, { passive: true })
  onScrollNav()
  reduceMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  splitTitleChars()
  setupReveal()
  if (pinSection.value && 'IntersectionObserver' in window) {
    pinIO = new IntersectionObserver((es) => es.forEach((e) => (pinInView.value = e.isIntersecting)), {
      threshold: 0,
    })
    pinIO.observe(pinSection.value)
  }
  if (!reduceMotion.value) {
    heroTimer = setInterval(() => {
      heroSubIdx.value = (heroSubIdx.value + 1) % heroSubs.length
    }, 4200)
  }
  window.addEventListener('scroll', onScroll, { passive: true })
  updatePin()
})
onUnmounted(() => {
  if (heroTimer) clearInterval(heroTimer)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('scroll', onScrollNav)
  revealIO?.disconnect()
  pinRevealIO?.disconnect()
  pinIO?.disconnect()
  document.documentElement.style.overflow = ''
})

/* 跑马灯内容：Aetrix 自有口径——支持的客户端与规格字标，纯文字白标（第 5 节②） */
const marqueeClients = ['Emby', 'Infuse', 'Kodi', 'Apple TV', 'Android TV', 'Samsung TV', 'LG webOS', 'Windows', 'macOS', 'iOS', 'Android']
const marqueeSpecs = ['4K Ultra HD', 'HDR10+', 'Dolby Vision', 'Dolby Atmos', 'DTS:X', 'H.265 / HEVC', 'Hi-Res Audio', '空间音频']

const year = new Date().getFullYear()
</script>

<template>
  <div ref="landingRoot" class="landing">
    <div class="landing-nav">
      <UhdNavPill
        :items="navItems"
        active-key="home"
        :brand="branding.site_name"
        brand-sub="SERVICE"
        cta-text="立即加入"
        shrink-on-scroll
        @select="onNav"
        @cta="goRegister"
      />
    </div>

    <!-- 移动端顶栏（≤900px）：照原站头部组件源码——左 logo、右玻璃 chip 内汉堡；
         未滚动时整条透明、滚动 >40px 玻璃 pill 浮起且 chip 变平；菜单为底部抽屉 -->
    <div class="m-nav" :class="{ 'is-on': navScrolled }">
      <span class="m-nav__brand">
        <span class="m-nav__logo" aria-hidden="true" />
        <span class="m-nav__brand-text">
          <span class="m-nav__brand-name">{{ branding.site_name }}</span>
          <span class="m-nav__brand-sub">SERVICE</span>
        </span>
      </span>
      <span class="m-nav__chip" :class="{ 'is-flat': navScrolled }">
        <button
          type="button"
          class="m-nav__burger"
          :aria-label="menuOpen ? '关闭菜单' : '打开菜单'"
          @click="toggleMenu"
        >
          <X v-if="menuOpen" :size="16" aria-hidden="true" />
          <Menu v-else :size="16" aria-hidden="true" />
        </button>
      </span>
    </div>
    <div v-if="menuOpen" class="m-scrim" :class="{ 'is-out': menuClosing }" @click="closeMenuAnimated" />
    <div
      v-if="menuOpen"
      class="m-sheet"
      :class="{ 'is-out': menuClosing }"
      :style="sheetDragStyle()"
      role="dialog"
      aria-modal="true"
      aria-label="菜单"
    >
      <div
        class="m-sheet__handle"
        aria-hidden="true"
        @pointerdown="onHandlePointerDown"
        @pointermove="onHandlePointerMove"
        @pointerup="onHandlePointerUp"
        @pointercancel="onHandlePointerUp"
      ><span /></div>
      <nav class="m-sheet__links" aria-label="主导航">
        <button type="button" class="m-sheet__row" @click="onMobileNav('home')">
          <Home :size="18" aria-hidden="true" />首页
        </button>
        <button type="button" class="m-sheet__row" @click="onMobileNav('pricing')">
          <Tag :size="18" aria-hidden="true" />定价
        </button>
        <a
          v-if="communityLinks.docs_url"
          class="m-sheet__row"
          :href="communityLinks.docs_url"
          target="_blank"
          rel="noopener noreferrer"
          @click="setMenu(false)"
        >
          <BookOpen :size="18" aria-hidden="true" />文档
          <ExternalLink :size="15" class="m-sheet__row-ext" aria-hidden="true" />
        </a>
      </nav>
      <template v-if="communityLinks.channels.length">
        <div class="m-sheet__divider" aria-hidden="true" />
        <section class="m-sheet__channels" aria-label="社区渠道">
          <h3 class="m-sheet__channels-title">社区渠道</h3>
          <div class="m-sheet__grid">
            <a
              v-for="(channel, idx) in communityLinks.channels"
              :key="idx"
              class="m-card"
              :href="channel.url"
              target="_blank"
              rel="noopener noreferrer"
              @click="setMenu(false)"
            >
              <span class="m-card__bubble">
                <component :is="channelIcon(channel.icon)" :size="20" aria-hidden="true" />
              </span>
              <span class="m-card__title">{{ channel.title }}</span>
              <span v-if="channel.subtitle" class="m-card__sub">{{ channel.subtitle }}</span>
            </a>
          </div>
        </section>
      </template>
      <div class="m-sheet__divider" aria-hidden="true" />
      <div class="m-sheet__cta">
        <UhdButton variant="primary" dark block @click="goConsole">进入后台</UhdButton>
      </div>
    </div>

    <!-- ① Hero -->
    <header class="hero">
      <div class="hero-beam" aria-hidden="true" />
      <!-- 移动端主视觉（返工单③）：自绘 SVG 棱镜＋彩虹光束＋星点，不用对方素材；桌面不显示 -->
      <div class="hero-visual" aria-hidden="true">
        <svg viewBox="0 0 390 236" preserveAspectRatio="xMidYMid meet" focusable="false">
          <defs>
            <linearGradient id="hv-prism" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#ffffff" stop-opacity="0.30" />
              <stop offset="0.5" stop-color="#9fd8ff" stop-opacity="0.10" />
              <stop offset="1" stop-color="#ffffff" stop-opacity="0.20" />
            </linearGradient>
            <linearGradient id="hv-inbeam" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0" stop-color="#ffffff" stop-opacity="0" />
              <stop offset="1" stop-color="#ffffff" stop-opacity="0.85" />
            </linearGradient>
            <radialGradient id="hv-glow" cx="0.5" cy="0.5" r="0.5">
              <stop offset="0" stop-color="#bcd9ff" stop-opacity="0.35" />
              <stop offset="1" stop-color="#bcd9ff" stop-opacity="0" />
            </radialGradient>
          </defs>
          <ellipse cx="195" cy="158" rx="150" ry="92" fill="url(#hv-glow)" />
          <g fill="#ffffff">
            <circle cx="28" cy="52" r="1.3" opacity="0.65" />
            <circle cx="64" cy="22" r="1" opacity="0.45" />
            <circle cx="102" cy="66" r="1.6" opacity="0.8" />
            <circle cx="140" cy="30" r="1" opacity="0.5" />
            <circle cx="176" cy="84" r="1.2" opacity="0.6" />
            <circle cx="214" cy="38" r="1.5" opacity="0.75" />
            <circle cx="248" cy="92" r="1" opacity="0.45" />
            <circle cx="282" cy="26" r="1.3" opacity="0.65" />
            <circle cx="318" cy="70" r="1.6" opacity="0.8" />
            <circle cx="356" cy="34" r="1.1" opacity="0.5" />
            <circle cx="44" cy="120" r="1" opacity="0.4" />
            <circle cx="344" cy="128" r="1.2" opacity="0.5" />
            <circle cx="88" cy="150" r="1.1" opacity="0.35" />
            <circle cx="300" cy="158" r="1" opacity="0.35" />
            <circle cx="160" cy="128" r="0.9" opacity="0.4" />
            <circle cx="232" cy="140" r="0.9" opacity="0.4" />
          </g>
          <g class="hv-fan" opacity="0.85">
            <polygon points="195,150 4,0 42,0" fill="#2f8fe0" />
            <polygon points="195,150 50,0 90,0" fill="#6f7ad8" />
            <polygon points="195,150 98,0 138,0" fill="#c46c90" />
            <polygon points="195,150 146,0 186,0" fill="#e8766f" />
            <polygon points="195,150 194,0 234,0" fill="#f2a45a" />
            <polygon points="195,150 242,0 282,0" fill="#ecd04f" />
            <polygon points="195,150 290,0 330,0" fill="#95c77d" />
            <polygon points="195,150 338,0 386,0" fill="#39b3ec" />
          </g>
          <polygon points="187,236 203,236 199,212 191,212" fill="url(#hv-inbeam)" />
          <polygon
            points="195,140 148,208 242,208"
            fill="url(#hv-prism)"
            stroke="rgba(255, 255, 255, 0.55)"
            stroke-width="1.2"
          />
        </svg>
      </div>
      <div class="hero-inner" :style="heroInnerStyle()">
        <p class="eyebrow">{{ branding.site_name }} SERVICE</p>
        <h1 class="hero-title">你的私人影院<br />随时随地开场</h1>
        <p class="hero-sub" aria-live="polite">
          <Transition name="hero-sub-fade" mode="out-in">
            <span :key="heroSubIdx">{{ heroSubs[heroSubIdx] }}</span>
          </Transition>
        </p>
        <div class="hero-actions">
          <!-- Hero 主钮分态（2026-11-13 照友站已登录宣传页定版）：已登录进站停
               宣传页，首屏主钮即「进入后台」（彩虹边＋玻璃底，与菜单面板大钮
               同一去向 goConsole → 概览）；未登录保持「免费注册」原样。 -->
          <UhdButton v-if="userStore.isLoggedIn" variant="primary" dark @click="goConsole">
            进入后台 <ArrowRight :size="18" aria-hidden="true" />
          </UhdButton>
          <UhdButton v-else variant="primary" dark @click="goRegister">免费注册</UhdButton>
          <UhdButton variant="secondary" dark @click="goPricing">查看定价</UhdButton>
        </div>
        <div class="hero-specs">
          <UhdChip variant="spec" sub="超高清">
            <template #icon><Monitor :size="20" aria-hidden="true" /></template>
            4K
          </UhdChip>
          <UhdChip variant="spec" sub="高动态范围">
            <template #icon><Sun :size="20" aria-hidden="true" /></template>
            HDR10+
          </UhdChip>
          <UhdChip variant="spec" sub="杜比视界">
            <template #icon><Layers :size="20" aria-hidden="true" /></template>
            Dolby Vision
          </UhdChip>
          <UhdChip variant="spec" sub="沉浸体验">
            <template #icon><ShieldCheck :size="20" aria-hidden="true" /></template>
            无广告干扰
          </UhdChip>
        </div>
      </div>
      <p class="hero-scroll-hint" :style="heroHintStyle()" aria-hidden="true">↓ 向下探索更多精彩</p>
    </header>

    <!-- ② 跑马灯 -->
    <section class="marquee-sec">
      <p class="eyebrow eyebrow--center rv" data-reveal>CLIENTS &amp; SPECS</p>
      <h2 ref="platformsTitle" class="sec-title sec-title--platforms">在你顺手的设备上，<br />以它最好的规格播放</h2>
      <p class="sec-sub rv rv--lede" data-reveal>播放交给专业客户端，服务器只做整理与供给——手机、电视、电脑，打开就能看。</p>
      <div class="marquee rv rv--marquee" data-reveal aria-hidden="true">
        <div class="marquee-track marquee-track--a">
          <span v-for="(w, i) in [...marqueeClients, ...marqueeClients]" :key="`a-${i}`" class="marquee-word">{{ w }}</span>
        </div>
        <div class="marquee-track marquee-track--b">
          <span v-for="(w, i) in [...marqueeSpecs, ...marqueeSpecs]" :key="`b-${i}`" class="marquee-word">{{ w }}</span>
        </div>
      </div>
    </section>

    <!-- ③ 钉住滚动展示（仅此一处）：240vh 行程（stage 100svh＋钉住 140%），
         光柱自绘＋滚动驱动模糊交叉（参数见 dynamics-spec.md ②/③） -->
    <section ref="pinSection" class="pin-sec">
      <div class="pin-stage">
        <div class="pin-visual" :style="pinVisualStyle()" aria-hidden="true">
          <div class="pin-drift" :style="pinDriftStyle()">
            <div class="pin-beams pin-beams--glow" :class="{ 'is-paused': !pinInView }">
              <span v-for="n in 8" :key="n" class="pin-bar" :class="`pin-bar--${n}`" />
            </div>
            <div class="pin-beams" :class="{ 'is-paused': !pinInView }">
              <span v-for="n in 8" :key="n" class="pin-bar" :class="`pin-bar--${n}`" />
            </div>
          </div>
          <div class="pin-vignette" :style="vignetteStyle()" />
        </div>
        <div class="pin-rings" aria-hidden="true">
          <span v-for="i in 3" :key="i" class="pin-ring" :class="`pin-ring--${i}`" :style="ringStyle(i - 1)" />
        </div>
        <div class="pin-copy" :style="cinemaCopyStyle()">
          <p class="eyebrow eyebrow--center rv rv--pin-eyebrow" data-reveal-pin>CINEMA EXPERIENCE</p>
          <h2 class="pin-title rv rv--pin-title" data-reveal-pin>影院级画面</h2>
          <p class="pin-sub rv rv--pin-lede" data-reveal-pin>4K Ultra HD · HDR10+ · Dolby Vision</p>
        </div>
        <div class="pin-copy pin-copy--audio">
          <p class="eyebrow eyebrow--center" :style="audioPartStyle(0)">AUDIO EXPERIENCE</p>
          <h2 class="pin-title" :style="audioPartStyle(1)">影院级声音</h2>
          <p class="pin-sub" :style="audioPartStyle(2)">Dolby Atmos · Hi-Res Audio · 空间音频 · DTS:X 全兼容</p>
        </div>
      </div>
    </section>

    <!-- ④ 一份订阅 -->
    <section class="essentials">
      <p class="eyebrow eyebrow--center rv" data-reveal>SERVICE ESSENTIALS</p>
      <h2 class="sec-title rv rv--sectitle" data-reveal>一份订阅，全部搞定</h2>
      <p class="sec-sub rv rv--lede2" data-reveal>一个账号打通所有设备与服务，不用为每块屏幕单独折腾。</p>

      <div class="ess-grid">
        <article class="ess-card ess-card--wide rv rv--card" data-reveal>
          <div class="ess-card-text">
            <p class="eyebrow">MULTI-DEVICE SYNC</p>
            <h3 class="ess-h3">一部片子，<br />所有屏幕接着看</h3>
            <p class="ess-desc">在电视上看到一半，出门用手机继续。播放进度、收藏与观看记录，在你的所有设备间自动同步。</p>
            <div class="ess-chips">
              <UhdChip variant="mini">TV</UhdChip>
              <UhdChip variant="mini">Computer</UhdChip>
              <UhdChip variant="mini">Tablet</UhdChip>
              <UhdChip variant="mini">手机</UhdChip>
            </div>
          </div>
          <div class="ess-devices" aria-hidden="true">
            <div class="dev dev--tv rv rv--dev" data-reveal><span class="dev-play" /><span class="dev-bar"><i style="width: 60%" /></span></div>
            <div class="dev dev--laptop rv rv--dev rv--dev-d1" data-reveal><span class="dev-play" /><span class="dev-bar"><i style="width: 60%" /></span></div>
            <div class="dev dev--tablet rv rv--dev rv--dev-d2" data-reveal><span class="dev-play" /></div>
            <div class="dev dev--phone rv rv--dev rv--dev-d3" data-reveal><span class="dev-play dev-play--sm" /></div>
            <p class="dev-note">上次同步 · 刚刚</p>
          </div>
          <!-- 移动端设备示意（返工单④①）：四台设备线框＋播放键＋进度条，仅 ≤900px 显示 -->
          <div class="ess-devices-m" aria-hidden="true">
            <div class="dm-row">
              <span class="dm dm--tv"><span class="dm-play" /><span class="dm-bar"><i style="width: 60%" /></span></span>
              <span class="dm dm--laptop"><span class="dm-play" /><span class="dm-bar"><i style="width: 60%" /></span></span>
              <span class="dm dm--tablet"><span class="dm-play dm-play--sm" /></span>
              <span class="dm dm--phone"><span class="dm-play dm-play--sm" /></span>
            </div>
            <p class="dm-note">SYNCED · 上次同步 刚刚</p>
          </div>
        </article>

        <article class="ess-card rv rv--card rv--d1" data-reveal>
          <p class="eyebrow">ZERO INTERRUPTION</p>
          <h3 class="ess-h3">没有广告，<br />只有电影</h3>
          <p class="ess-desc">从按下播放到片尾字幕，全程无插播、无贴片，沉浸感不被打断。</p>
          <div class="ess-timeline" aria-hidden="true">
            <div class="ess-timeline-meta"><span>00:00</span><span class="ess-timeline-mid">100% 主内容</span><span>02:15</span></div>
            <div class="ess-timeline-bar"><i /></div>
            <div class="ess-timeline-marks">
              <span>前贴片 <b>0:00</b></span>
              <span>中插 <b>0:00</b></span>
              <span>后贴片 <b>0:00</b></span>
            </div>
          </div>
        </article>

        <article class="ess-card ess-card--library rv rv--card rv--d2" data-reveal>
          <div class="ess-wall" aria-hidden="true">
            <span
              v-for="n in 12"
              :key="n"
              class="ess-tile rv rv--tile"
              :class="`ess-tile--${(n % 4) + 1}`"
              :style="{ transitionDelay: `${((n - 1) * 0.025).toFixed(3)}s` }"
              data-reveal
            />
          </div>
          <p class="eyebrow">EVER-GROWING LIBRARY</p>
          <h3 class="ess-h3">片库自己长大</h3>
          <p class="ess-desc">新片自动扫描入库、刮削补齐海报与资料；追的剧更新了，自动出现在你的片库里。</p>
          <!-- 无公开统计接口可读：不编造数字、不留占位行，改用不依赖数字的功能版式（返工单④③） -->
          <ul class="ess-points">
            <li>
              <RefreshCw :size="18" aria-hidden="true" />
              <span><b>自动入库</b>新片到盘，自动扫描进片库</span>
            </li>
            <li>
              <Sparkles :size="18" aria-hidden="true" />
              <span><b>资料补齐</b>海报、简介与评分自动刮削归位</span>
            </li>
            <li>
              <Bell :size="18" aria-hidden="true" />
              <span><b>追剧更新</b>追的剧一出新集，自动跟上</span>
            </li>
          </ul>
        </article>
      </div>
    </section>

    <!-- ⑤ 单行页脚 -->
    <footer class="landing-foot">
      <span>© {{ year }} {{ branding.site_name }} · 自建 Emby 影视服务平台</span>
      <span class="landing-foot-links">
        <RouterLink to="/pricing">定价</RouterLink>
        <RouterLink to="/privacy">隐私政策</RouterLink>
        <RouterLink to="/login">登录</RouterLink>
      </span>
    </footer>
  </div>
</template>

<style scoped>
.landing {
  min-height: 100vh;
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
  /* 必须用 clip 不能用 hidden：hidden 会把本元素变成滚动容器、令 .pin-stage 的 sticky 失效
     （动态返工实测：hidden 下 stage 随页面滚走、钉住从未生效） */
  overflow-x: hidden;
  overflow-x: clip;
}
.landing-nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 50;
}

/* ---------- 移动端顶栏与底部抽屉（仅 ≤900px 启用，桌面走 UhdNavPill）
   数值照原站头部组件源码：logo 图标 24px、站名 15px/字距 .18em、SERVICE 10px/字距 .34em；
   玻璃 pill 层滚动 >40px 才浮起（opacity .5s ease-sheet），汉堡 chip 同步变平 ---------- */
.m-nav,
.m-scrim,
.m-sheet {
  display: none;
}
.m-nav__brand {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--uhd-d-text);
}
.m-nav__logo {
  width: 24px;
  height: 22px;
  background: var(--uhd-rainbow-conic);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.m-nav__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1;
}
.m-nav__brand-name {
  font-family: var(--uhd-font-display);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  letter-spacing: 0.18em;
}
.m-nav__brand-sub {
  margin-top: 4px;
  font-size: 10px;
  letter-spacing: 0.34em;
  color: var(--uhd-d-text-2);
}
.m-nav__chip {
  position: relative;
  display: inline-flex;
  padding: 4px;
  border: var(--uhd-border-w) solid var(--uhd-glass-border);
  border-radius: 9999px;
  background: var(--uhd-glass-d);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  transition:
    background-color 0.5s cubic-bezier(0.32, 0.72, 0, 1),
    border-color 0.5s cubic-bezier(0.32, 0.72, 0, 1);
}
.m-nav__chip.is-flat {
  background: transparent;
  border-color: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.m-nav__burger {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-d-text);
  cursor: pointer;
}
@keyframes m-sheet-in {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}
@keyframes m-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
/* 原站 Hero 入场 hero-fade：淡入＋上移 16px、.74s ease-out（无模糊，桌面移动一致） */
@keyframes landing-hero-fade {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.eyebrow {
  margin: 0 0 18px;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  letter-spacing: var(--uhd-ls-eyebrow);
  text-transform: uppercase;
  color: var(--uhd-d-text-2);
}
.eyebrow--center { text-align: center; }
.sec-title {
  margin: 0 0 16px;
  text-align: center;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-service-hero: clamp(2rem, 4.4vw, 4.4rem) */
  font-size: clamp(32px, 4.4vw, 70.4px);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.15;
}
.sec-title--platforms {
  /* 原站 --text-landing-platforms-title: clamp(1.8rem, 3.4vw, 2.8rem) */
  font-size: clamp(28.8px, 3.4vw, 44.8px);
}
.sec-sub {
  margin: 0 auto;
  max-width: 640px;
  text-align: center;
  font-size: var(--uhd-fs-lead);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-d-text-2);
}

/* ---------- Hero ---------- */
.hero {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  overflow: hidden;
}
.hero-beam {
  position: absolute;
  top: -22%;
  right: -12%;
  width: 62vw;
  height: 90vh;
  background: var(--uhd-rainbow-conic);
  opacity: 0.5;
  filter: blur(70px);
  transform: rotate(18deg);
  pointer-events: none;
}
/* 棱镜主视觉（D1-03）：桌面居右上、移动端全幅居顶（见文末断点） */
.hero-visual {
  position: absolute;
  top: 84px;
  right: 1.5%;
  width: min(620px, 46vw);
  aspect-ratio: 390 / 236;
  pointer-events: none;
  /* 桌面：四边径向柔化，扇面融进底色不留矩形硬边（移动端全幅出血不套此罩） */
  -webkit-mask-image: radial-gradient(ellipse 92% 90% at 50% 46%, #000 52%, transparent 97%);
  mask-image: radial-gradient(ellipse 92% 90% at 50% 46%, #000 52%, transparent 97%);
}
.hero-visual svg {
  display: block;
  width: 100%;
  height: 100%;
}
.hero-visual .hv-fan {
  filter: blur(5px);
  mix-blend-mode: screen;
}
.hero-inner {
  position: relative;
  width: 100%;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  padding: 130px 28px 120px;
  animation: landing-hero-fade 0.74s ease-out both;
}
.hero-title {
  margin: 0 0 22px;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-hero: clamp(2.1rem, 4.1vw, 3.9rem) */
  font-size: clamp(33.6px, 4.1vw, 62.4px);
  font-weight: var(--uhd-fw-black);
  line-height: var(--uhd-lh-tight);
}
.hero-sub {
  margin: 0 0 34px;
  min-height: 1.6em;
  font-size: var(--uhd-fs-lead);
  color: var(--uhd-d-text-2);
}
.hero-sub-fade-enter-active,
.hero-sub-fade-leave-active {
  transition: opacity var(--uhd-dur-fade) var(--uhd-ease-standard);
}
.hero-sub-fade-enter-from,
.hero-sub-fade-leave-to { opacity: 0; }
.hero-actions {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.hero-specs {
  display: flex;
  gap: 34px;
  flex-wrap: wrap;
  margin-top: 64px;
}
.hero-scroll-hint {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 26px;
  margin: 0;
  white-space: nowrap;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
}

/* ---------- 跑马灯 ---------- */
.marquee-sec {
  padding: 130px 0 120px;
}
.marquee {
  margin-top: 64px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 60px;
  -webkit-mask-image: linear-gradient(90deg, transparent, black 12%, black 88%, transparent);
  mask-image: linear-gradient(90deg, transparent, black 12%, black 88%, transparent);
}
.marquee-track {
  display: flex;
  gap: 165px;
  width: max-content;
  padding-right: 165px;
  animation: uhd-marquee 46s linear infinite;
}
.marquee-track--b {
  animation-direction: reverse;
  animation-duration: 52s;
}
.marquee-word {
  font-family: var(--uhd-font-display);
  font-size: 22px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: 0.04em;
  white-space: nowrap;
  color: var(--uhd-d-text);
  opacity: 0.8;
}
@keyframes uhd-marquee {
  from { transform: translateX(0); }
  to { transform: translateX(-50%); }
}

/* ---------- 钉住滚动展示（动态返工：240vh＝stage 100vh＋钉住行程 140%，移动桌面同套） ---------- */
.pin-sec {
  height: 240vh;
  height: 240svh;
}
.pin-stage {
  position: sticky;
  top: 0;
  height: 100vh;
  height: 100svh;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #000;
}
/* 光柱视觉层：滚动 scale/退场视差由 pinVisualStyle 驱动 transform */
.pin-visual {
  position: absolute;
  inset: 0;
  will-change: transform;
}
.pin-drift {
  position: absolute;
  inset: 0;
  will-change: transform;
}
/* 光柱本体（按实测清单 §7 重做，代原 10 根等距彩虹幕）：黑底舞台上的 8 条疏密竖条。
   每条两层同构：柔晕层（--glow，整条宽、层 blur 28px）垫底＋锐核层（居中窄条、宽约整条 26%，
   背景取整条渐变的中央切片、层 blur 2px 得约 2px 锐边）；纵向明暗由 mask 给出
   （黑→约 1/3 高亮峰→黑），宽条经 clip-path 中部最宽、向顶部收窄。
   位置/主色逐条引 §7（x 以 1920 宽换算 %）；原站该区是视频、自带缓慢漂移/呼吸，
   自发运动保留图层级 26s/32s 呼吸作等价自绘，不引用原站素材 */
.pin-beams {
  position: absolute;
  inset: 0;
  filter: blur(2px) saturate(1.3);
  animation: pin-breathe 26s ease-in-out infinite alternate;
  will-change: transform, filter;
}
.pin-beams--glow {
  filter: blur(28px) saturate(1.45);
  opacity: 0.6;
  animation-duration: 32s;
  animation-delay: -14s;
}
.pin-beams.is-paused {
  animation-play-state: paused;
}
@keyframes pin-breathe {
  from {
    transform: translateX(-8px);
    filter: blur(2px) saturate(1.26) brightness(0.96);
  }
  to {
    transform: translateX(10px);
    filter: blur(2px) saturate(1.38) brightness(1.1);
  }
}
.pin-beams--glow {
  animation-name: pin-breathe-glow;
}
@keyframes pin-breathe-glow {
  from {
    transform: translateX(8px) scale(1.02);
    filter: blur(28px) saturate(1.4) brightness(1.04);
  }
  to {
    transform: translateX(-10px) scale(1.05);
    filter: blur(28px) saturate(1.55) brightness(1.16);
  }
}
.pin-bar {
  position: absolute;
  top: 0;
  bottom: 0;
  -webkit-mask-image: linear-gradient(to top, transparent 0%, #000 17%, #000 27%, rgba(0, 0, 0, 0.5) 55%, transparent 92%);
  mask-image: linear-gradient(to top, transparent 0%, #000 17%, #000 27%, rgba(0, 0, 0, 0.5) 55%, transparent 92%);
}
/* 宽条向顶部收窄（中部最宽）；两条细线（--1/--8）不收窄 */
.pin-bar--2,
.pin-bar--3,
.pin-bar--4,
.pin-bar--5,
.pin-bar--6,
.pin-bar--7 {
  clip-path: polygon(12% 100%, 0% 58%, 10% 26%, 28% 0%, 72% 0%, 90% 26%, 100% 58%, 88% 100%);
}
/* 逐条落位（§7，x/1920 换算 %）：left＝条左缘、width＝条宽，此为柔晕层整条 */
.pin-bar--1 { left: 18.96%; width: 0.42%; background: linear-gradient(to right, transparent, #001b8b 50%, transparent); }
.pin-bar--2 { left: 25.52%; width: 6.25%; background: linear-gradient(to right, #013bc2, #000003); }
.pin-bar--3 { left: 31.67%; width: 5.83%; background: linear-gradient(to right, rgba(0, 96, 194, 0) 0%, #0060c2 30%, #00b1e5 50%, #0060c2 70%, rgba(0, 96, 194, 0) 100%); }
.pin-bar--4 { left: 37.92%; width: 3.75%; background: linear-gradient(to right, #77f0ee, #02eeef, #f4f4d8, #b0ece0); }
.pin-bar--5 { left: 50%; width: 4.17%; background: linear-gradient(to right, #fff08a, #ffeda3); }
.pin-bar--6 { left: 56.04%; width: 6.04%; background: linear-gradient(to right, #df7db1, #f492ae, #ffa189, #ffb17e, #ffc873, #ffd56d, #fff59f); }
.pin-bar--7 { left: 62.29%; width: 6.04%; background: linear-gradient(to right, #010105, #070026, #200653, #4c147c, #691e8f, #952e95, #b9409c, #e66aa5, #f9aeb0); }
.pin-bar--8 { left: 73.96%; width: 0.42%; background: linear-gradient(to right, transparent, #2a1b6e 50%, transparent); }
/* 锐核层：居中窄条（整条宽的 26%），横向渐变背景放大到整条全幅、只露中央切片，
   切片边缘即约 2px 锐边（青条 §7 注明核心色纵向变化，单独给纵向渐变、不走切片） */
.pin-beams:not(.pin-beams--glow) .pin-bar {
  background-size: 384.6% 100%;
  background-position: 50% 50%;
  background-repeat: no-repeat;
}
.pin-beams:not(.pin-beams--glow) .pin-bar--1 { left: 19.12%; width: 0.11%; }
.pin-beams:not(.pin-beams--glow) .pin-bar--2 { left: 27.83%; width: 1.63%; }
.pin-beams:not(.pin-beams--glow) .pin-bar--3 {
  left: 33.83%;
  width: 1.52%;
  background: linear-gradient(to top, #0060c2 0%, #00b1e5 33%, #0060c2 90%);
}
.pin-beams:not(.pin-beams--glow) .pin-bar--4 { left: 39.31%; width: 0.98%; }
.pin-beams:not(.pin-beams--glow) .pin-bar--5 { left: 51.54%; width: 1.08%; }
.pin-beams:not(.pin-beams--glow) .pin-bar--6 { left: 58.27%; width: 1.57%; }
.pin-beams:not(.pin-beams--glow) .pin-bar--7 { left: 64.52%; width: 1.57%; }
.pin-beams:not(.pin-beams--glow) .pin-bar--8 { left: 74.12%; width: 0.11%; }
/* 文案区恒常柔影（保证两态文字在亮柱上可读） */
.pin-visual::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.5), transparent 46%),
    radial-gradient(ellipse 90% 70% at 50% 55%, transparent 34%, rgba(0, 0, 0, 0.5) 100%);
}
/* 过渡暗角（原站 cinema-vignette：径向暗角＋紫辉，进度 .30–.52 渐显） */
.pin-vignette {
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at center, transparent 26%, rgba(0, 0, 0, 0.78) 78%),
    radial-gradient(circle at center, rgba(120, 110, 255, 0.22) 0%, transparent 55%);
  opacity: 0;
}
/* 声场圆环（原站三环 42/70/98vmin，边框透明度递减） */
.pin-rings {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.pin-ring {
  position: absolute;
  left: 50%;
  top: 50%;
  border-radius: 50%;
  opacity: 0;
}
.pin-ring--1 { width: 42vmin; height: 42vmin; border: 1px solid rgba(255, 255, 255, 0.3); }
.pin-ring--2 { width: 70vmin; height: 70vmin; border: 1px solid rgba(255, 255, 255, 0.2); }
.pin-ring--3 { width: 98vmin; height: 98vmin; border: 1px solid rgba(255, 255, 255, 0.12); }
.pin-copy {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 16vh;
  bottom: 16svh;
  z-index: 2;
  text-align: center;
  padding: 0 24px;
  transition: none;
  will-change: opacity, filter, transform;
}
.pin-copy .eyebrow,
.pin-copy .pin-title,
.pin-copy .pin-sub {
  text-shadow: 0 10px 28px rgba(0, 0, 0, 0.34);
}

/* ---------- 区块 reveal（原站 GSAP from 态的 CSS 等价，参数见 spec ④） ---------- */
.rv {
  opacity: 0;
  transform: translateY(30px);
  transition:
    opacity 0.7s cubic-bezier(0.215, 0.61, 0.355, 1),
    transform 0.7s cubic-bezier(0.215, 0.61, 0.355, 1),
    filter 0.7s cubic-bezier(0.215, 0.61, 0.355, 1);
}
.rv.is-in {
  opacity: 1;
  transform: translateY(0) scale(1);
  filter: blur(0px);
}
.rv--lede { transition-delay: 0.3s; }
.rv--marquee {
  transform: translateY(50px);
  transition-duration: 0.9s;
  transition-delay: 0.45s;
}
.rv--sectitle {
  transform: translateY(50px) scale(0.97);
  filter: blur(14px);
  transition-duration: 0.95s;
  transition-delay: 0.12s;
}
.rv--lede2 {
  transform: translateY(26px);
  filter: blur(10px);
  transition-duration: 0.75s;
  transition-delay: 0.32s;
}
.essentials .rv.eyebrow {
  transform: translateY(24px);
  filter: blur(10px);
  transition-duration: 0.8s;
}
.rv--card {
  transform: translateY(60px);
  filter: blur(12px);
  transition-duration: 0.9s;
}
.rv--d1 { transition-delay: 0.12s; }
.rv--d2 { transition-delay: 0.24s; }
.rv--dev {
  transform: translateY(24px);
  transition-duration: 0.7s;
}
.rv--dev-d1 { transition-delay: 0.08s; }
.rv--dev-d2 { transition-delay: 0.16s; }
.rv--dev-d3 { transition-delay: 0.24s; }
.rv--tile {
  transform: translateY(12px);
  transition-duration: 0.6s;
}
.rv--pin-eyebrow {
  transform: translateY(24px);
  filter: blur(10px);
  transition-duration: 0.8s;
}
.rv--pin-title {
  transform: translateY(58px) scale(0.96);
  filter: blur(18px);
  transition-duration: 1s;
  transition-delay: 0.12s;
}
.rv--pin-lede {
  transform: translateY(30px);
  filter: blur(12px);
  transition-duration: 0.8s;
  transition-delay: 0.38s;
}
/* 跑马灯标题逐字（原站 yPercent:120＋stagger .035） */
.pt-char-wrap {
  display: inline-block;
  overflow: hidden;
  vertical-align: bottom;
  padding-bottom: 0.12em;
  margin-bottom: -0.12em;
}
.pt-char {
  display: inline-block;
  transform: translateY(120%);
  opacity: 0;
  transition:
    transform 0.8s cubic-bezier(0.215, 0.61, 0.355, 1),
    opacity 0.8s cubic-bezier(0.215, 0.61, 0.355, 1);
}
.pt-char.is-in {
  transform: translateY(0);
  opacity: 1;
}
.pin-title {
  margin: 0 0 14px;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-immersive-title: clamp(2.25rem, 4.8vw, 5.4rem) */
  font-size: clamp(36px, 4.8vw, 86.4px);
  font-weight: var(--uhd-fw-black);
  line-height: 1.1;
}
.pin-sub {
  margin: 0;
  font-size: var(--uhd-fs-lead);
  color: #cfd8e3;
}

/* ---------- 一份订阅 ---------- */
.essentials {
  padding: 130px 28px 120px;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
}
.ess-grid {
  display: grid;
  grid-template-columns: 1fr 2fr;
  gap: var(--uhd-card-gap);
  margin-top: 56px;
}
.ess-card {
  position: relative;
  padding: var(--uhd-card-pad);
  border: var(--uhd-border-w) solid var(--uhd-d-border);
  border-radius: var(--uhd-radius-card-lg);
  background:
    var(--uhd-grid-tex) 0 0 / var(--uhd-grid-tex-size) var(--uhd-grid-tex-size),
    var(--uhd-d-surface);
  overflow: hidden;
}
.ess-card--wide {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 30px;
  align-items: center;
  min-height: 335px;
}
.ess-h3 {
  margin: 0 0 12px;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-service-title: clamp(1.6rem, 2.4vw, 2.4rem) */
  font-size: clamp(25.6px, 2.4vw, 38.4px);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.2;
}
.ess-card--wide .ess-h3 {
  /* 原站 --text-landing-service-title-lg: clamp(1.6rem, 2.6vw, 2.6rem) */
  font-size: clamp(25.6px, 2.6vw, 41.6px);
}
.ess-desc {
  margin: 0;
  max-width: 460px;
  font-size: var(--uhd-fs-body);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-d-text-2);
}
.ess-chips {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 22px;
}
/* 设备线框（CSS 绘制，照原站宽卡右侧设备图口径） */
.ess-devices {
  position: relative;
  height: 240px;
}
.dev {
  position: absolute;
  border: 1.5px solid var(--uhd-d-chip-border);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dev--tv { width: 225px; height: 130px; right: 150px; top: 6px; }
.dev--laptop { width: 180px; height: 120px; right: 0; top: 66px; }
.dev--tablet { width: 100px; height: 140px; right: 300px; top: 84px; }
.dev--phone { width: 55px; height: 115px; right: 96px; top: 108px; }
.dev-play {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1.5px solid var(--uhd-d-text-2);
  position: relative;
}
.dev-play::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 55%;
  transform: translate(-50%, -50%);
  border-style: solid;
  border-width: 5px 0 5px 8px;
  border-color: transparent transparent transparent var(--uhd-d-text);
}
.dev-play--sm { width: 20px; height: 20px; }
.dev-bar {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 12px;
  height: 3px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.18);
}
.dev-bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--uhd-d-text);
}
.dev-note {
  position: absolute;
  right: 190px;
  bottom: 0;
  margin: 0;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--uhd-d-text-2);
}
/* 移动端设备示意条：桌面不显示，仅 ≤900px（见文末断点） */
.ess-devices-m {
  display: none;
}
.dm-row {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 14px;
}
.dm {
  position: relative;
  flex: none;
  border: 1.5px solid var(--uhd-d-chip-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dm--tv { width: 104px; height: 62px; }
.dm--laptop { width: 84px; height: 56px; }
.dm--tablet { width: 46px; height: 62px; }
.dm--phone { width: 28px; height: 56px; }
.dm-play {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 1.5px solid var(--uhd-d-text-2);
  position: relative;
}
.dm-play::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 55%;
  transform: translate(-50%, -50%);
  border-style: solid;
  border-width: 3.5px 0 3.5px 6px;
  border-color: transparent transparent transparent var(--uhd-d-text);
}
.dm-play--sm { width: 15px; height: 15px; }
.dm-play--sm::after { border-width: 2.5px 0 2.5px 4px; }
.dm-bar {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 7px;
  height: 3px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.18);
}
.dm-bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--uhd-d-text);
}
.dm-note {
  margin: 14px 0 0;
  text-align: center;
  font-size: var(--uhd-fs-aux-sm);
  letter-spacing: 0.14em;
  color: var(--uhd-d-text-2);
}
/* 左窄卡时间轴 */
.ess-timeline { margin-top: 34px; }
.ess-timeline-meta {
  display: flex;
  justify-content: space-between;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-d-text-2);
  margin-bottom: 10px;
}
.ess-timeline-bar {
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.16);
}
.ess-timeline-bar i {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: 2px;
  background: var(--uhd-d-text);
}
.ess-timeline-mid {
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-d-text);
}
.ess-timeline-marks {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--uhd-d-text-2);
  white-space: nowrap;
}
.ess-timeline-marks b {
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
}
/* 右宽卡海报砖墙占位 */
.ess-card--library { padding-top: 0; }
.ess-wall {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin: 0 -30px 26px;
  padding: 22px 30px 0;
}
.ess-tile {
  height: 62px;
  border-radius: var(--uhd-radius-navitem);
  background: var(--uhd-d-surface-2);
}
.ess-tile--2 { background: #151515; }
.ess-tile--3 { background: #101010; }
.ess-tile--4 { background: #131313; }
/* 片库卡功能行（无数字版式） */
.ess-points {
  list-style: none;
  margin: 28px 0 0;
  padding: 0;
  display: grid;
  gap: 15px;
}
.ess-points li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: var(--uhd-fs-body);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-d-text-2);
}
.ess-points li svg {
  flex: none;
  margin-top: 3px;
  color: var(--uhd-d-text-2);
}
.ess-points b {
  margin-right: 10px;
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
}

/* ---------- 页脚 ---------- */
.landing-foot {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  padding: 22px 28px 30px;
  border-top: var(--uhd-border-w) solid var(--uhd-d-border);
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-d-text-2);
}
.landing-foot-links {
  display: inline-flex;
  gap: 22px;
}
.landing-foot-links a {
  color: var(--uhd-d-text-2);
  text-decoration: none;
}
.landing-foot-links a:hover { color: var(--uhd-d-text); }

@media (max-width: 900px) {
  /* ---- 顶栏：原站形态——未滚动整条透明、滚动后玻璃 pill 浮起；菜单为底部抽屉 ---- */
  .landing-nav { display: none; }
  .m-nav {
    position: fixed;
    top: 16px;
    left: 12px;
    right: 12px;
    z-index: 70;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 8px;
  }
  .m-nav::before {
    content: '';
    position: absolute;
    inset: 0;
    border: var(--uhd-border-w) solid var(--uhd-glass-border);
    border-radius: 9999px;
    background: var(--uhd-glass-d);
    backdrop-filter: var(--uhd-glass-filter);
    -webkit-backdrop-filter: var(--uhd-glass-filter);
    opacity: 0;
    transition: opacity 0.5s cubic-bezier(0.32, 0.72, 0, 1);
    pointer-events: none;
  }
  .m-nav.is-on::before { opacity: 1; }
  .m-scrim {
    position: fixed;
    inset: 0;
    z-index: 65;
    display: block;
    background: rgba(0, 0, 0, 0.45);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    animation: m-fade-in 0.3s ease-out;
  }
  .m-scrim.is-out {
    opacity: 0;
    transition: opacity 0.3s ease-out;
  }
  .m-sheet {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 66;
    display: block;
    border-top: var(--uhd-border-w) solid var(--uhd-glass-border);
    border-radius: 28px 28px 0 0;
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.03) 32%, rgba(255, 255, 255, 0) 62%),
      rgba(20, 21, 25, 0.88);
    backdrop-filter: blur(20px) saturate(160%);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    box-shadow: 0 -18px 50px rgba(0, 0, 0, 0.45);
    animation: m-sheet-in 0.38s cubic-bezier(0.32, 0.72, 0, 1);
  }
  .m-sheet.is-out {
    transform: translateY(100%);
    transition: transform 0.3s cubic-bezier(0.32, 0.72, 0, 1);
  }
  .m-sheet__handle {
    display: flex;
    justify-content: center;
    padding: 14px 0 8px;
    touch-action: none;
    cursor: grab;
  }
  .m-sheet__handle span {
    width: 40px;
    height: 3px;
    border-radius: 9999px;
    background: rgba(255, 255, 255, 0.2);
  }
  .m-sheet__links {
    display: flex;
    flex-direction: column;
    padding: 4px 12px 12px;
  }
  .m-sheet__row {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    padding: 14px 16px;
    border: none;
    border-radius: 16px;
    background: transparent;
    color: var(--uhd-d-text);
    font-size: 15px;
    font-weight: var(--uhd-fw-medium);
    text-align: left;
    cursor: pointer;
  }
  .m-sheet__row svg {
    flex: none;
    opacity: 0.4;
  }
  a.m-sheet__row { text-decoration: none; }
  .m-sheet__row .m-sheet__row-ext {
    margin-left: auto;
    opacity: 0.45;
  }
  .m-sheet__row:active { background: rgba(255, 255, 255, 0.08); }
  /* 社区渠道：小节标题 + 2×2 卡片（圆角深灰底、圆形图标气泡、标题、@副标） */
  .m-sheet__channels {
    padding: 16px 20px 20px;
  }
  .m-sheet__channels-title {
    margin: 0 0 12px;
    font-size: 13px;
    font-weight: var(--uhd-fw-medium);
    letter-spacing: 0.04em;
    color: rgba(255, 255, 255, 0.45);
  }
  .m-sheet__grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .m-card {
    display: flex;
    flex-direction: column;
    padding: 16px;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 20px;
    background: rgba(255, 255, 255, 0.045);
    color: var(--uhd-d-text);
    text-decoration: none;
  }
  .m-card:active { background: rgba(255, 255, 255, 0.09); }
  .m-card__bubble {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    margin-bottom: 10px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.85);
  }
  .m-card__title {
    font-size: 15px;
    font-weight: var(--uhd-fw-semibold);
    line-height: 1.35;
  }
  .m-card__sub {
    margin-top: 3px;
    font-size: 12.5px;
    color: rgba(255, 255, 255, 0.42);
    word-break: break-all;
  }
  .m-sheet__divider {
    height: 1px;
    margin: 0 20px;
    background: rgba(255, 255, 255, 0.07);
  }
  .m-sheet__cta {
    padding: 16px 16px max(20px, env(safe-area-inset-bottom));
  }
  .m-sheet__cta :deep(.uhd-btn) {
    padding-top: 20px;
    padding-bottom: 20px;
    font-size: 15px;
  }

  /* ---- Hero：入场用原站 hero-fade（单层淡入上移，无模糊无叠影）；糊光光斑让位棱镜主视觉（返工单①③） ---- */
  .hero {
    min-height: 100vh;
    min-height: 100svh;
    align-items: flex-start;
  }
  .hero-beam { display: none; }
  .hero-visual {
    display: block;
    position: absolute;
    top: 60px;
    left: 0;
    right: 0;
    width: auto;
    height: 236px;
    pointer-events: none;
    -webkit-mask-image: none;
    mask-image: none;
  }
  .hero-visual svg {
    width: 100%;
    height: 100%;
  }
  .hero-inner {
    padding: 308px 22px 92px;
  }
  .hero-specs {
    gap: 18px 24px;
    margin-top: 40px;
  }
  .hero-scroll-hint {
    left: 0;
    right: 0;
    bottom: 18px;
    transform: none;
    text-align: center;
  }

  /* ---- 跑马灯：收紧字距行距与区块留白（返工单⑥） ---- */
  .marquee-sec { padding: 84px 0 76px; }
  .marquee {
    gap: 30px;
    margin-top: 44px;
  }
  .marquee-track {
    gap: 72px;
    padding-right: 72px;
  }
  .marquee-word { font-size: 19px; }

  /* ---- 钉住区：移动端与桌面共用同一套钉住＋模糊交叉（动态返工，恢复 240svh 行程）；
         光柱层上下柔边融入黑底（柱体羽化见 .pin-beams），文案绝对居下半区 ---- */
  .pin-sec { height: 240vh; height: 240svh; }
  .pin-stage {
    position: sticky;
    top: 0;
    height: 100vh;
    height: 100svh;
  }
  .pin-visual {
    -webkit-mask-image: linear-gradient(180deg, transparent, black 7%, black 93%, transparent);
    mask-image: linear-gradient(180deg, transparent, black 7%, black 93%, transparent);
  }
  .pin-copy {
    bottom: 14vh;
    bottom: 14svh;
    padding: 0 24px;
  }

  /* ---- 一份订阅：卡片区收紧留白、补移动设备示意、片库砖墙换彩谱渐变（返工单④） ---- */
  .essentials { padding: 84px 20px 76px; }
  .ess-grid { grid-template-columns: 1fr; }
  .ess-card--wide { grid-template-columns: 1fr; }
  .ess-devices { display: none; }
  .ess-devices-m {
    display: block;
    margin-top: 28px;
  }
  .ess-tile {
    background: linear-gradient(160deg, #233447, #12161d);
  }
  .ess-tile--2 { background: linear-gradient(160deg, #3a2b45, #17121d); }
  .ess-tile--3 { background: linear-gradient(160deg, #453026, #1d1410); }
  .ess-tile--4 { background: linear-gradient(160deg, #274435, #101a14); }
  .ess-points b { display: block; margin: 0 0 2px; }
}
@media (prefers-reduced-motion: reduce) {
  .hero-inner { animation: none; }
  .marquee-track { animation: none; }
  .pin-beams { animation: none; }
  .rv,
  .pt-char {
    opacity: 1;
    transform: none;
    filter: none;
    transition: none;
  }
  .m-sheet,
  .m-scrim { animation: none; }
  .hero-sub-fade-enter-active,
  .hero-sub-fade-leave-active { transition: none; }
}
</style>
