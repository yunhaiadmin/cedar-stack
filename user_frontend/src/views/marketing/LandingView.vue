<script setup lang="ts">
/**
 * 官网 www 首页（UHD 换皮批 1 · 深色营销面）——说明书逐页对照表第 18 行
 * 六区块骨架：100vh Hero（棱镜光束 + 文案轮换 + 规格 chip）→ 流媒体伙伴字标行
 * → 客户端/规格跑马灯 → 钉住滚动影院/音频展示（钉住只此一处，说明书第 5 节①）
 * → 一份订阅三卡 → 页脚（免责声明＋单行信息）。
 * 文案为 Aetrix 自有口径；营销数字一律不编造：片库数据行读 /api/site/library-stats 实时统计取整呈现，接口不可用时回落快照（见 useLibraryStats）。
 * 页面独立于应用壳（App.vue 对 landing 路由名不渲染全局顶栏/底坞），自带悬浮胶囊导航。
 */
import { computed, nextTick, onDeactivated, onMounted, onUnmounted, ref, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { Monitor, Sparkles, Film, Ban, Menu, X, RefreshCw, Bell, Home, Tag, BookOpen, ExternalLink, Users, Bot, MessageCircle, Send, Globe, ArrowRight } from 'lucide-vue-next'
import UhdNavPill, { type UhdNavItem } from '@/components/uhd/UhdNavPill.vue'
import { engageModalIsolation, lockModalOverflow, trapTabKey, unlockModalOverflow } from '@/composables/useModalIsolation'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdChip from '@/components/uhd/UhdChip.vue'
import { branding } from '@/composables/useBranding'
import { communityLinks } from '@/composables/useCommunityLinks'
import { initLibraryStats, libraryStats } from '@/composables/useLibraryStats'
import { useUserStore } from '@/stores/user'

const router = useRouter()
/* 首屏棱镜图加载态：图未到位前只露纯黑中性底（不露半张图/半成品），load 后整图淡入 */
const heroImgReady = ref(false)
function onHeroImgLoad() { heroImgReady.value = true }
const userStore = useUserStore()

/* 片库数据行取整呈现（与原快照展示格式一致）：作品向下取整到千并加千分位，
   单集 / 总计向下取整到万。libraryStats 未拉到实时数时保持快照值。 */
function fmtThousandsPlus(n: number): string {
  return `${(Math.floor(n / 1000) * 1000).toLocaleString('en-US')}+`
}
function fmtWanPlus(n: number): string {
  return `${Math.floor(n / 10000)}万+`
}
const statsWorks = computed(() => fmtThousandsPlus(libraryStats.works))
const statsEpisodes = computed(() => fmtWanPlus(libraryStats.episodes))
const statsTotal = computed(() => fmtWanPlus(libraryStats.total_items))

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
const burgerBtn = ref<HTMLButtonElement | null>(null)
const sheetEl = ref<HTMLElement | null>(null)
const menuModal = ref<HTMLElement | null>(null)
let menuLocked = false
let closeTimer = 0
let releaseMenuIsolation: (() => void) | null = null
function cancelCloseTimer() {
  if (!closeTimer) return
  window.clearTimeout(closeTimer)
  closeTimer = 0
}
function onScrollNav() {
  navScrolled.value = window.scrollY > 40
}
function focusSheet() {
  const first = sheetEl.value?.querySelector<HTMLElement>('button, a')
  first?.focus()
}
function lockMenu() {
  if (menuLocked) return
  lockModalOverflow()
  menuLocked = true
  nextTick(() => {
    if (menuModal.value) releaseMenuIsolation = engageModalIsolation(menuModal.value)
    focusSheet()
  })
}
function unlockMenu() {
  releaseMenuIsolation?.()
  releaseMenuIsolation = null
  if (!menuLocked) return
  unlockModalOverflow()
  menuLocked = false
}
function setMenu(open: boolean) {
  cancelCloseTimer()
  menuOpen.value = open
  menuClosing.value = false
  if (open) lockMenu()
  else unlockMenu()
}
function forceReleaseMenu() {
  cancelCloseTimer()
  menuOpen.value = false
  menuClosing.value = false
  unlockMenu()
}
function closeMenuAnimated() {
  if (!menuOpen.value || menuClosing.value) return
  menuClosing.value = true
  unlockMenu()
  cancelCloseTimer()
  closeTimer = window.setTimeout(() => {
    closeTimer = 0
    menuOpen.value = false
    menuClosing.value = false
    burgerBtn.value?.focus()
  }, 300)
}
function closeMenuToOpener() {
  setMenu(false)
  nextTick(() => burgerBtn.value?.focus())
}
function onMenuKey(event: KeyboardEvent) {
  if (!menuOpen.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeMenuAnimated()
    return
  }
  trapTabKey(event, menuModal.value)
}
function toggleMenu() {
  if (menuClosing.value) {
    setMenu(true)
    return
  }
  if (menuOpen.value) closeMenuAnimated()
  else setMenu(true)
}
function onMobileNav(key: string) {
  if (key === 'home') closeMenuToOpener()
  else setMenu(false)
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

/* 三卡聚光灯（L-10，友站 useSpotlight 口径）：指针位置写 --mx/--my（px），
   卡面 ::after 的径向高光跟随；呈现只在 CSS (hover:hover) 段，触屏不触发。 */
function onSpotlight(e: PointerEvent) {
  const el = e.currentTarget as HTMLElement
  const r = el.getBoundingClientRect()
  el.style.setProperty('--mx', `${Math.round(e.clientX - r.left)}px`)
  el.style.setProperty('--my', `${Math.round(e.clientY - r.top)}px`)
}

function goRegister() {
  router.push({ path: '/login', query: { mode: 'register' } })
}
function goStream() {
  router.push('/library')
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
  return { transform: rm ? 'none' : `translateX(${(-6 * pinProgress.value).toFixed(1)}px)` }
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
  const scope = [...el.attributes].find((attr) => attr.name.startsWith('data-v-'))
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
      if (scope) {
        wrap.setAttribute(scope.name, '')
        inner.setAttribute(scope.name, '')
      }
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

let landingWide: MediaQueryList | null = null
function onLandingWide() {
  if (landingWide?.matches) forceReleaseMenu()
}
onMounted(() => {
  void initLibraryStats()
  window.addEventListener('scroll', onScrollNav, { passive: true })
  window.addEventListener('keydown', onMenuKey)
  landingWide = window.matchMedia('(min-width: 901px)')
  landingWide.addEventListener('change', onLandingWide)
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
onDeactivated(() => forceReleaseMenu())
onUnmounted(() => {
  if (heroTimer) clearInterval(heroTimer)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('scroll', onScrollNav)
  window.removeEventListener('keydown', onMenuKey)
  landingWide?.removeEventListener('change', onLandingWide)
  revealIO?.disconnect()
  pinRevealIO?.disconnect()
  pinIO?.disconnect()
  forceReleaseMenu()
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
        cta-text="进入后台"
        shrink-on-scroll
        @select="onNav"
        @cta="goConsole"
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
          ref="burgerBtn"
          type="button"
          class="m-nav__burger"
          :aria-label="menuOpen ? '关闭菜单' : '打开菜单'"
          :aria-expanded="menuOpen"
          @click="toggleMenu"
        >
          <X v-if="menuOpen" :size="19" aria-hidden="true" />
          <Menu v-else :size="19" aria-hidden="true" />
        </button>
      </span>
    </div>
    <div v-if="menuOpen" ref="menuModal" class="m-modal" data-modal-root>
    <div class="m-scrim" :class="{ 'is-out': menuClosing }" @click="closeMenuAnimated" />
    <div
      ref="sheetEl"
      class="m-sheet"
      :class="{ 'is-out': menuClosing }"
      :style="sheetDragStyle()"
      role="dialog"
      aria-modal="true"
      aria-label="菜单"
    >
      <button type="button" class="m-sheet__close" aria-label="关闭菜单" @click="closeMenuAnimated">
        <X :size="18" aria-hidden="true" />
      </button>
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
          @click="closeMenuToOpener"
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
              @click="closeMenuToOpener"
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
    </div>

    <!-- ① Hero -->
    <header class="hero">
      <!-- 移动端全幅背景（L-02 棱镜光束图，2026-11-15 换新图：深黑星空底、
           棱镜居左下、白色光束自左缘入射、光谱扇斜射右上、左上大片黑场留给
           文案）；图与脉冲叠层同装 .hero-bg-canvas（画布与图同比例 2048:1152、
           高度撑满首屏，叠层恒对位）；仅 ≤900px 显示，桌面走 hero-visual 同图 -->
      <div class="hero-bg" :class="{ 'is-ready': heroImgReady }" aria-hidden="true">
        <div class="hero-bg-canvas">
          <img class="hero-bg-img" src="/images/hero-prism-beam.webp" alt="" width="2048" height="1152" fetchpriority="high" @load="onHeroImgLoad" />
          <div class="hp">
            <span class="hp-beamglow" />
            <span class="hp-dot" />
            <span class="hp-flash hp-flash--entry" />
            <span class="hp-flash hp-flash--prism" />
            <span class="hp-refl" />
          </div>
        </div>
      </div>
      <!-- 桌面棱镜主视觉：与移动端同一张棱镜光束图（2048×1152）。帧与图同
           比例、高度撑满首屏，水平锚点把棱镜压到左下（几何见 .hero-visual-frame）。
           移动端不显示，走 .hero-bg。不再另叠彩虹渐变。 -->
      <div class="hero-visual" :class="{ 'is-ready': heroImgReady }" aria-hidden="true">
        <div class="hero-visual-frame">
          <img class="hero-visual-img" src="/images/hero-prism-beam.webp" alt="" width="2048" height="1152" @load="onHeroImgLoad" />
          <div class="hp">
            <span class="hp-beamglow" />
            <span class="hp-dot" />
            <span class="hp-flash hp-flash--entry" />
            <span class="hp-flash hp-flash--prism" />
            <span class="hp-refl" />
          </div>
        </div>
      </div>
      <div class="hero-scrim" aria-hidden="true" />
      <div class="hero-inner" :style="heroInnerStyle()">
        <div class="hero-copy">
        <p class="eyebrow hero-el hero-el--eyebrow">{{ branding.site_name }} SERVICE</p>
        <h1 class="hero-title hero-el hero-el--title">你的私人影院<br />随时随地开场</h1>
        <p class="hero-sub hero-el hero-el--sub" aria-live="polite">
          <Transition name="hero-sub-ticker">
            <span :key="heroSubIdx" class="hero-sub-line">{{ heroSubs[heroSubIdx] }}</span>
          </Transition>
        </p>
        <div class="hero-actions hero-el hero-el--actions">
          <!-- Hero 双钮分态（业务入口不动）：已登录＝「进入后台」＋「前往流媒体」；
               未登录＝「免费注册」＋「查看定价」。箭头只是主钮的视觉收束。 -->
          <UhdButton v-if="userStore.isLoggedIn" variant="primary" dark @click="goConsole">
            进入后台 <ArrowRight :size="18" aria-hidden="true" />
          </UhdButton>
          <UhdButton v-else variant="primary" dark @click="goRegister">
            免费注册 <ArrowRight :size="18" aria-hidden="true" />
          </UhdButton>
          <UhdButton v-if="userStore.isLoggedIn" variant="secondary" dark class="hero-btn-stream" @click="goStream">
            前往流媒体 <ArrowRight :size="18" aria-hidden="true" />
          </UhdButton>
          <UhdButton v-else variant="secondary" dark class="hero-btn-pricing" @click="goPricing">查看定价</UhdButton>
        </div>
        </div>
        <div class="hero-specs hero-el hero-el--specs">
          <UhdChip variant="spec" sub="超高清">
            <template #icon><Monitor :size="20" aria-hidden="true" /></template>
            4K
          </UhdChip>
          <UhdChip variant="spec" sub="高动态范围">
            <template #icon><Sparkles :size="20" aria-hidden="true" /></template>
            HDR10+
          </UhdChip>
          <UhdChip variant="spec" sub="杜比视界">
            <template #icon><Film :size="20" aria-hidden="true" /></template>
            Dolby Vision
          </UhdChip>
          <UhdChip variant="spec" sub="沉浸体验">
            <template #icon><Ban :size="20" aria-hidden="true" /></template>
            无广告干扰
          </UhdChip>
        </div>
      </div>
      <p class="hero-scroll-hint" :style="heroHintStyle()" aria-hidden="true">↓ 向下探索更多精彩</p>
    </header>

    <!-- ② 流媒体伙伴（2026-11-15 用户亲点，照友站结构）：英文节题＋一行三平台
         文字字标（排版实现，不用官方 logo 图），弱化白、等距排列 -->
    <section class="partners-sec">
      <p class="eyebrow eyebrow--center rv" data-reveal>STREAMING PARTNERS</p>
      <div class="partners-row rv" data-reveal>
        <span class="partner partner--netflix">Netflix</span>
        <span class="partner partner--disney">Disney+</span>
        <span class="partner partner--hbo">HBO Max</span>
      </div>
    </section>

    <!-- ③ 跑马灯 -->
    <section class="marquee-sec">
      <p class="eyebrow eyebrow--center rv" data-reveal>CLIENTS &amp; SPECS</p>
      <h2 id="platforms-heading" ref="platformsTitle" class="sec-title sec-title--platforms">在你顺手的设备上，<br />以它最好的规格播放</h2>
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

    <!-- ④ 钉住滚动展示（仅此一处）：240vh 行程（stage 100svh＋钉住 140%），
         光柱自绘＋滚动驱动模糊交叉（参数见 dynamics-spec.md ②/③） -->
    <section ref="pinSection" class="pin-sec">
      <div class="pin-stage">
        <div class="pin-visual" :style="pinVisualStyle()" aria-hidden="true">
          <div class="pin-drift" :style="pinDriftStyle()">
            <!-- 宽幅极光色带（2026-11-15 对友站录屏 f024/f025 补齐）：4 条竖向宽带整屏铺底，
                 色序青绿→橙黄→粉红→紫罗兰，每条约 25% 宽、带间大模糊柔边；纯 CSS 自绘，不用友站素材 -->
            <div class="pin-bands" :class="{ 'is-paused': !pinInView }">
              <span class="pin-band pin-band--1" />
              <span class="pin-band pin-band--2" />
              <span class="pin-band pin-band--3" />
              <span class="pin-band pin-band--4" />
            </div>
            <div class="pin-glass" />
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

    <!-- ⑤ 一份订阅 -->
    <section class="essentials">
      <p class="eyebrow eyebrow--center rv" data-reveal>SERVICE ESSENTIALS</p>
      <h2 class="sec-title rv rv--sectitle" data-reveal>一份订阅，全部搞定</h2>
      <p class="sec-sub rv rv--lede2" data-reveal>一个账号打通所有设备与服务，不用为每块屏幕单独折腾。</p>

      <div class="ess-grid">
        <article class="ess-card ess-card--wide rv rv--card" data-reveal @pointermove="onSpotlight">
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
            <div class="dev dev--tv rv rv--dev" data-reveal><span class="dev-play" /><span class="dev-bar"><i style="--bar-w: 60%" /></span><span class="dev-label">电视</span></div>
            <div class="dev dev--laptop rv rv--dev rv--dev-d1" data-reveal><span class="dev-play" /><span class="dev-bar"><i style="--bar-w: 60%" /></span><span class="dev-label">电脑</span></div>
            <div class="dev dev--tablet rv rv--dev rv--dev-d2" data-reveal><span class="dev-play" /><span class="dev-label">平板</span></div>
            <div class="dev dev--phone rv rv--dev rv--dev-d3" data-reveal><span class="dev-play dev-play--sm" /><span class="dev-label">手机</span></div>
            <p class="dev-note">上次同步 · 刚刚</p>
          </div>
          <!-- 移动端设备示意（返工单④①）：四台设备线框＋播放键＋进度条，仅 ≤900px 显示 -->
          <div class="ess-devices-m" aria-hidden="true">
            <div class="dm-row">
              <span class="dm dm--tv"><span class="dm-play" /><span class="dm-bar"><i style="--bar-w: 60%" /></span><span class="dm-label">电视</span></span>
              <span class="dm dm--laptop"><span class="dm-play" /><span class="dm-bar"><i style="--bar-w: 60%" /></span><span class="dm-label">电脑</span></span>
              <span class="dm dm--tablet"><span class="dm-play dm-play--sm" /><span class="dm-label">平板</span></span>
              <span class="dm dm--phone"><span class="dm-play dm-play--sm" /><span class="dm-label">手机</span></span>
            </div>
            <p class="dm-note">SYNCED · 上次同步 刚刚</p>
          </div>
        </article>

        <article class="ess-card rv rv--card rv--d1" data-reveal @pointermove="onSpotlight">
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

        <article class="ess-card ess-card--library rv rv--card rv--d2" data-reveal @pointermove="onSpotlight">
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
          <!-- 数据行：读 /api/site/library-stats 实时统计（ORM 口径：deleted_at IS NULL
               AND merged_into_id IS NULL），取整呈现；接口不可用时回落 useLibraryStats 里的
               2026-11-15 生产快照。排版照友站大数据行（大数字＋小标签横排）；
               不用 PB 等本站没有口径的数字。 -->
          <div class="ess-stats">
            <div class="ess-stat"><span class="ess-stat-num">{{ statsWorks }}</span><span class="ess-stat-label">影视作品</span></div>
            <div class="ess-stat"><span class="ess-stat-num">{{ statsEpisodes }}</span><span class="ess-stat-label">剧集单集</span></div>
            <div class="ess-stat"><span class="ess-stat-num">{{ statsTotal }}</span><span class="ess-stat-label">条目总计</span></div>
          </div>
          <p class="ess-stats-note">覆盖 {{ libraryStats.libraries }} 个媒体库，持续更新中</p>
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

    <!-- ⑥ 页脚：免责声明（2026-11-15 用户亲点，照友站结构、本站措辞）＋单行信息 -->
    <footer class="landing-foot">
      <p class="landing-foot-disclaimer">
        本站所有内容均来自互联网，仅供学习与交流使用，不作任何商业用途。如有侵权，请通过 TG 联系
        <a href="https://t.me/xiayea" target="_blank" rel="noopener noreferrer">@xiayea</a>，我们将在核实后及时删除。
      </p>
      <div class="landing-foot-row">
        <span>© {{ year }} {{ branding.site_name }} · 自建 Emby 影视服务平台</span>
        <span class="landing-foot-links">
          <RouterLink to="/pricing">定价</RouterLink>
          <RouterLink to="/privacy">隐私政策</RouterLink>
          <RouterLink to="/login">登录</RouterLink>
        </span>
      </div>
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
     （动态返工实测：hidden 下 stage 随页面滚走、钉住从未生效）。
     clip 只收住跑马灯轨道的横向溢出，标题本身必须在视口内换行，不许靠裁切藏字。 */
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
   字标尺寸照友站真机截图（2026-11-14）：站名约 21px/800 字距 .05em、SERVICE 11px 字距 .38em；
   玻璃 pill 层滚动 >40px 才浮起（opacity .5s ease-sheet），汉堡 chip 同步变平 ---------- */
.m-modal { display: contents; }
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
  width: 26px;
  height: 24px;
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
  font-size: 21px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: 0.05em;
}
.m-nav__brand-sub {
  margin-top: 5px;
  font-size: 11px;
  letter-spacing: 0.38em;
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
.m-nav__burger:focus-visible,
.m-sheet__close:focus-visible,
.m-sheet__row:focus-visible,
.m-card:focus-visible {
  outline: 2px solid #ffffff;
  outline-offset: 2px;
}
@keyframes m-sheet-in {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}
@keyframes m-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
/* Hero 入场已收编令牌入场库（L-11）：本页私有的 landing-hero-fade 与令牌
   uhd-hero-fade（Y16＋淡入、--uhd-dur-hero .74s）数值重复，删除私有件，
   入场编排改为分件交错（见 .hero-el 系列规则）。 */

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
  margin: 0 auto 16px;
  max-width: min(920px, calc(100% - 32px));
  text-align: center;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-service-hero: clamp(2rem, 4.4vw, 4.4rem) */
  font-size: clamp(32px, 4.4vw, 70.4px);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.28;
  overflow-wrap: anywhere;
}
.sec-title--platforms {
  /* 原站 --text-landing-platforms-title: clamp(1.8rem, 3.4vw, 2.8rem) */
  font-size: clamp(26px, 3.4vw, 44.8px);
  line-height: 1.35;
  scroll-margin-top: 96px;
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
  min-height: 100svh;
  display: flex;
  align-items: stretch;
  /* 文字层不许被裁掉。装饰图的出血只在 .hero-visual / .hero-bg 里收。 */
  overflow: visible;
}
/* 移动端全幅背景层：默认隐藏（桌面走 .hero-visual），仅 ≤900px 启用（见文末断点）。
   .hero-bg 作尺寸容器，内层画布 .hero-bg-canvas 与底图同比例（2048:1152）、
   高度撑满首屏：图与脉冲叠层同画布，任意视口下叠层坐标恒与图对位。::before 压暗左上文字区、::after 压暗
   底部卖点区（z-index 抬到画布之上），保证文字压在暗部上 */
.hero-bg {
  display: none;
  position: absolute;
  inset: 0;
  /* 画布只许在首屏内出血：旧 1:2 画布高过首屏、底边溢进下一节（2026-11-15
     真机实锤跑马灯节顶被图尾压住），这里 clip 兜底，画布几何本身也不再溢出 */
  overflow: hidden;
  pointer-events: none;
  container-type: size;
}
.hero-bg-canvas {
  position: absolute;
  /* 画布与新图同比例（2048:1152）、高度撑满首屏：图宽＝177.78cqh 恒覆盖屏宽。
     水平锚点：把棱镜中心（图 x≈21.3%）压到屏左 78px 处——棱镜落在左下、
     入射光束自左缘入画、光谱扇斜贯右上、左上黑场留给文案。
     脉冲几何覆盖值（对新图 2048×1152 像素实测，图内 % 坐标）：
     光束线 y≈81、入射星芒 (19.5, 79.5)、棱镜中心 (21.3, 76.5)、倒影心 y≈90.5 */
  top: 0;
  left: 0;
  height: 100%;
  aspect-ratio: 2048 / 1152;
  margin-left: calc(78px - 37.8cqh);
  --hp-beam-y: 81%;
  --hp-entry-x: 19.5%;
  --hp-prism-x: 21.3%;
  --hp-prism-y: 76.5%;
  --hp-refl-y: 90.5%;
}
.hero-bg-img {
  display: block;
  width: 100%;
  height: 100%;
  /* 加载态不露结论：图未到位前透明，只剩 .landing 纯黑中性底，load 后整图淡入 */
  opacity: 0;
  transition: opacity 0.6s ease;
}
.hero-bg.is-ready .hero-bg-img { opacity: 1; }
.hero-bg::before {
  content: '';
  position: absolute;
  z-index: 2;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(2, 2, 10, 0.42), transparent 34%),
    linear-gradient(97deg, rgba(2, 2, 10, 0.55) 0%, rgba(2, 2, 10, 0.18) 46%, transparent 72%),
    linear-gradient(90deg, transparent 58%, rgba(1, 1, 4, 0.32));
}
.hero-bg::after {
  content: '';
  position: absolute;
  z-index: 2;
  inset: 0;
  background: linear-gradient(0deg, rgba(1, 1, 4, 0.86), rgba(1, 1, 4, 0.55) 28%, transparent 56%);
}
/* 桌面棱镜：与移动端同一张图，帧与图同比例（2048:1152）撑满首屏，
   脉冲百分比跟图走；定位把棱镜压到左下、光谱扇斜贯右上，左上黑部留给文案。只裁装饰层。 */
.hero-visual {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  container-type: size;
}
.hero-visual-frame {
  position: absolute;
  /* 不用 transform 定位：全站减动效会把 transform 清掉，构图不能靠它。
     帧与图同比例（2048:1152）、高度撑满首屏（超宽屏 min-width 兜底、底对齐，
     图不拉伸不变形）；水平锚点：图比容器宽时把棱镜中心（图 x≈21.3%）压到
     容器宽 12% 处，图恰好/不足时贴左/右对齐兜底不露黑。脉冲坐标与移动画布
     同一组图内 % 值（同一张图、帧图同比例恒对位）。 */
  bottom: 0;
  height: 100%;
  min-width: 100%;
  aspect-ratio: 2048 / 1152;
  left: max(min(0px, calc(100cqw - 177.78cqh)), min(0px, calc(12cqw - 37.3cqh)));
  --hp-beam-y: 81%;
  --hp-entry-x: 19.5%;
  --hp-prism-x: 21.3%;
  --hp-prism-y: 76.5%;
  --hp-refl-y: 90.5%;
}
.hero-visual-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0;
  transition: opacity 0.6s ease;
}
.hero-visual.is-ready .hero-visual-img { opacity: 1; }
.hero-scrim {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background:
    linear-gradient(96deg, rgba(2, 2, 10, 0.82) 0%, rgba(2, 2, 10, 0.4) 26%, rgba(2, 2, 10, 0.08) 46%, transparent 62%),
    linear-gradient(180deg, rgba(2, 2, 10, 0.55) 0%, transparent 16%, transparent 72%, rgba(1, 1, 4, 0.72) 100%);
}

/* ---------- Hero 棱镜脉冲（L-02 唯一动效，2026-11-14 友站实测） ----------
   一轮 5s：亮点沿入射光束自左向右跑 1.3s（0–26%）→ 抵达入射点时入射
   星芒大亮、棱镜本体闪亮、地板倒影同步微亮 → 约 2.5s（50%）衰尽，
   后半程全静默。光谱扇／出射星芒／行星光晕／画面本体零动画（实测
   90s+ 恒定，不加）。周期 5s 字面值照本文件环境动画先例（uhd-marquee
   46s linear、pin-breathe 26s）；相位 % 由实测秒数换算；几何坐标走
   --hp-* 令牌（uhd-tokens.css §1.7 是旧图兜底值，现行几何在
   .hero-visual-frame／.hero-bg-canvas 内按新图实测覆盖，两端同值）。 */
.hp {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.hp-dot,
.hp-beamglow,
.hp-flash,
.hp-refl {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}
.hp-beamglow {
  left: 0;
  top: var(--hp-beam-y);
  width: var(--hp-entry-x);
  height: 4%;
  transform: translateY(-50%);
  background: linear-gradient(90deg, transparent 30%, rgba(255, 255, 255, 0.75));
  filter: blur(3px);
  animation: hp-beamglow 5s linear infinite;
}
.hp-dot {
  left: 0;
  top: var(--hp-beam-y);
  width: 15%;
  height: 8%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: radial-gradient(closest-side, rgba(255, 255, 255, 0.95), rgba(190, 215, 255, 0.4) 48%, transparent 72%);
  filter: blur(1px);
  animation: hp-travel 5s linear infinite;
}
.hp-flash {
  transform: translate(-50%, -50%);
  border-radius: 50%;
  mix-blend-mode: screen;
}
.hp-flash--entry {
  left: var(--hp-entry-x);
  top: var(--hp-beam-y);
  width: 24%;
  height: 24%;
  background: radial-gradient(closest-side, rgba(255, 255, 255, 0.95), rgba(200, 220, 255, 0.35) 46%, transparent 70%);
  animation: hp-flash-entry 5s linear infinite;
}
.hp-flash--prism {
  left: var(--hp-prism-x);
  top: var(--hp-prism-y);
  width: 58%;
  height: 58%;
  background: radial-gradient(closest-side, rgba(235, 242, 255, 0.55), rgba(170, 200, 255, 0.18) 52%, transparent 72%);
  animation: hp-flash-prism 5s linear infinite;
}
.hp-refl {
  left: var(--hp-prism-x);
  top: var(--hp-refl-y);
  width: 46%;
  height: 20%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: radial-gradient(closest-side, rgba(220, 232, 255, 0.4), transparent 70%);
  filter: blur(4px);
  animation: hp-refl 5s linear infinite;
}
@keyframes hp-travel {
  0% { left: -6%; opacity: 0; }
  5% { opacity: 1; }
  26% { left: var(--hp-entry-x); opacity: 1; }
  31%, 100% { left: var(--hp-entry-x); opacity: 0; }
}
@keyframes hp-beamglow {
  0%, 8% { opacity: 0; }
  26% { opacity: 0.5; }
  50%, 100% { opacity: 0; }
}
@keyframes hp-flash-entry {
  0%, 23% { opacity: 0; transform: translate(-50%, -50%) scale(0.72); }
  30% { opacity: 0.95; transform: translate(-50%, -50%) scale(1); }
  50%, 100% { opacity: 0; transform: translate(-50%, -50%) scale(1.15); }
}
@keyframes hp-flash-prism {
  0%, 24% { opacity: 0; transform: translate(-50%, -50%) scale(0.92); }
  32% { opacity: 0.6; transform: translate(-50%, -50%) scale(1); }
  52%, 100% { opacity: 0; transform: translate(-50%, -50%) scale(1.04); }
}
@keyframes hp-refl {
  0%, 25% { opacity: 0; }
  34% { opacity: 0.4; }
  54%, 100% { opacity: 0; }
}
.hero-inner {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  min-height: 100vh;
  min-height: 100svh;
  padding: 112px 40px 34px;
  box-sizing: border-box;
}
.hero-copy {
  max-width: min(540px, 46vw);
  margin-top: auto;
  margin-bottom: auto;
  padding-bottom: 88px;
}
/* Hero 入场编排（L-11）：整层一把梭改为分件交错，全走令牌入场库。
   交错步长 0.08s——与本站 .rv--dev 阶梯（0.08/0.16/0.24）的步距一致；
   取值表：眉题 uhd-fade-in（--uhd-dur-fade .52s，延迟 0s）→ 标题
   uhd-hero-fade（--uhd-dur-hero .74s、Y16，延迟 .08s）→ 副题 uhd-fade-up
   （.52s、Y24，延迟 .16s）→ 双钮 uhd-fade-up（延迟 .24s）→ 规格 chip
   uhd-fade-up（延迟 .32s）；缓动一律 --uhd-ease-standard。 */
.hero-el--eyebrow {
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard) both;
}
.hero-el--title {
  animation: uhd-hero-fade var(--uhd-dur-hero) var(--uhd-ease-standard) 0.08s both;
}
.hero-el--sub {
  animation: uhd-fade-up var(--uhd-dur-fade) var(--uhd-ease-standard) 0.16s both;
}
.hero-el--actions {
  animation: uhd-fade-up var(--uhd-dur-fade) var(--uhd-ease-standard) 0.24s both;
}
.hero-el--specs {
  animation: uhd-fade-up var(--uhd-dur-fade) var(--uhd-ease-standard) 0.32s both;
}
.hero-title {
  margin: 0 0 22px;
  max-width: 100%;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-hero: clamp(2.1rem, 4.1vw, 3.9rem) */
  font-size: clamp(33.6px, 4.1vw, 62.4px);
  font-weight: var(--uhd-fw-black);
  line-height: var(--uhd-lh-tight);
  overflow-wrap: anywhere;
}
.hero-sub {
  position: relative;
  overflow: hidden;
  margin: 0 0 34px;
  min-height: 1.6em;
  font-size: var(--uhd-fs-lead);
  color: var(--uhd-d-text-2);
}
/* 副标题轮换（L-01）：纯淡入淡出改为整行竖滑 ticker——新句 uhd-ticker-in
   （Y100%→0）自下滑入、旧句 uhd-ticker-out（→Y-100%）向上滑出，各
   --uhd-dur-ticker .36s＋--uhd-ease-standard，与友站 ticker-in/out 同帧
   交叉口径一致；容器 overflow:hidden 裁切竖滑轨道，离场句绝对定位脱流，
   与入场句同一 .36s 内交叉更替。关键帧本体在 uhd-tokens.css 入场库。 */
.hero-sub-line { display: block; }
.hero-sub-ticker-enter-active {
  animation: uhd-ticker-in var(--uhd-dur-ticker) var(--uhd-ease-standard) both;
}
.hero-sub-ticker-leave-active {
  position: absolute;
  inset: 0;
  animation: uhd-ticker-out var(--uhd-dur-ticker) var(--uhd-ease-standard) both;
}
.hero-actions {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
/* 「前往流媒体」改深色玻璃（2026-11-14 用户双站对图亲点）：UhdButton 的
   secondary+dark 基础配方是 transparent 底、无 backdrop-filter——空心描边，
   钮内直接透出背景条纹原色。本页用专属钩子 .hero-btn-stream 覆盖成深色
   玻璃（压暗＋模糊＋白亮边＋内高光＋柔影），不动共享组件，同 variant 的
   「查看定价」等他处次钮不受影响。字重走 .uhd-btn 基础 semibold，与左钮同级。 */
.hero-actions :deep(.uhd-btn--secondary.hero-btn-stream) {
  color: #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.32);
  background: rgba(10, 13, 26, 0.45);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.25),
    0 12px 32px -12px rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
}
.hero-specs {
  display: flex;
  gap: 22px 36px;
  flex-wrap: wrap;
  align-items: flex-start;
  max-width: min(720px, 58%);
  margin-top: 0;
}
/* 卖点：图标在左，主词在上、说明在下。只改本页规格 chip，不动共享 UhdChip。 */
.hero-specs :deep(.uhd-chip--spec) {
  display: grid;
  grid-template-columns: 22px auto;
  column-gap: 10px;
  row-gap: 2px;
  align-items: center;
  white-space: normal;
}
.hero-specs :deep(.uhd-chip__icon) {
  grid-row: 1 / span 2;
}
.hero-specs :deep(.uhd-chip__sub) {
  grid-column: 2;
}
.hero-scroll-hint {
  position: absolute;
  z-index: 2;
  right: max(28px, calc((100vw - var(--uhd-container-marketing)) / 2));
  bottom: 28px;
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
  text-shadow: 0 1px 10px rgba(0, 0, 0, 0.72);
}

/* ---------- 流媒体伙伴（文字字标行，2026-11-15） ----------
   节题沿用 .eyebrow 居中口径；字标一行等距、弱化白，桌面字高约 30px。 */
.partners-sec {
  padding: 88px 28px 4px;
}
.partners-row {
  display: flex;
  align-items: center;
  justify-content: space-evenly;
  flex-wrap: nowrap;
  max-width: var(--uhd-container-marketing);
  margin: 10px auto 0;
}
.partner {
  font-family: var(--uhd-font-display);
  font-size: 30px;
  line-height: 1;
  white-space: nowrap;
  color: rgba(255, 255, 255, 0.52);
}
.partner--netflix {
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.partner--disney {
  font-weight: var(--uhd-fw-bold);
  letter-spacing: 0.01em;
}
.partner--hbo {
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: 0.1em;
  text-transform: uppercase;
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
  /* 只淡出轨道两端的词，标题在轨道外面，不跟着被裁。 */
  -webkit-mask-image: linear-gradient(90deg, transparent, black 10%, black 90%, transparent);
  mask-image: linear-gradient(90deg, transparent, black 10%, black 90%, transparent);
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
/* 极光色带本体（2026-11-15 替换原 8 根细光柱）：4 条竖向宽带整屏铺底，每条约 25% 宽，
   横向负边距交叠＋单条 blur(16px) 做带间大模糊柔边；纵向 mask 把带头带尾羽化进黑底；
   整层 saturate 提色，缓慢呼吸漂移沿用原光柱的图层级自发运动口径（离屏暂停）。 */
.pin-bands {
  position: absolute;
  inset: -4% 0;
  display: flex;
  filter: saturate(1.12) brightness(0.92);
  -webkit-mask-image: linear-gradient(180deg, transparent, black 9%, black 91%, transparent);
  mask-image: linear-gradient(180deg, transparent, black 9%, black 91%, transparent);
  animation: pin-bands-breathe 26s ease-in-out infinite alternate;
  will-change: transform;
}
.pin-bands.is-paused { animation-play-state: paused; }
@keyframes pin-bands-breathe {
  from { transform: translateX(-4px) scale(1.01); }
  to { transform: translateX(5px) scale(1.03); }
}
.pin-band {
  flex: 1 1 0;
  margin: 0 -14px;
  filter: blur(12px) saturate(1.18);
}
.pin-band--1 { background: linear-gradient(180deg, #5fd8c2 0%, #0e9a8a 46%, #043d38 100%); }
.pin-band--2 { background: linear-gradient(180deg, #f5d76e 0%, #e8930c 46%, #8f2f0d 100%); }
.pin-band--3 { background: linear-gradient(180deg, #f2a7bf 0%, #e5547e 46%, #8c1d42 100%); }
.pin-band--4 { background: linear-gradient(180deg, #b78ae8 0%, #7434b8 46%, #2c1052 100%); }
/* 玻璃光感层（口径六件齐：blur＋saturate＋半透底＋内高光＋边缘光＋大柔影）：
   压在色带之上、文案之下，给整幅色带一层玻璃质感与上下边缘光。 */
.pin-glass {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(2px) saturate(1.5);
  -webkit-backdrop-filter: blur(2px) saturate(1.5);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    inset 0 -1px 0 rgba(255, 255, 255, 0.12),
    inset 0 48px 90px -48px rgba(255, 255, 255, 0.18),
    0 30px 80px -30px rgba(0, 0, 0, 0.6);
  pointer-events: none;
}
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
  background: radial-gradient(ellipse at center, transparent 42%, rgba(0, 0, 0, 0.42) 88%),
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
  box-sizing: border-box;
  max-width: 100%;
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
/* 眉题从态特异性高于 .rv.is-in，不补这条会永远停在 blur(10px)（2026-11-15 截图实锤） */
.essentials .rv.eyebrow.is-in {
  transform: translateY(0);
  filter: blur(0px);
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
  /* 上下留出字形墨水，静止帧不再被 overflow 切掉字头/字脚；离场的 translateY 仍被裁掉 */
  line-height: 1.35;
  padding: 0.14em 0 0.18em;
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
  max-width: 100%;
  font-family: var(--uhd-font-display);
  /* 原站 --text-landing-immersive-title: clamp(2.25rem, 4.8vw, 5.4rem) */
  font-size: clamp(32px, 4.8vw, 86.4px);
  font-weight: var(--uhd-fw-black);
  line-height: 1.15;
  overflow-wrap: anywhere;
}
.pin-sub {
  margin: 0 auto;
  max-width: min(920px, 100%);
  font-size: var(--uhd-fs-lead);
  line-height: 1.55;
  color: #cfd8e3;
  overflow-wrap: anywhere;
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
/* 三卡 hover（L-10）：spotlight 聚光灯＋background/transform .22s spring
   （友站原文过渡配方：background-color 走 base 档时长＋transform .22s
   spring 缓动；本站映射为 .22s＋--uhd-ease-spring，聚光灯坐标 --mx/--my
   的初值在 uhd-tokens.css 1.7 组件运行态，运行时由 onSpotlight 写 px 值）。
   ::after 径向高光以 --mx/--my 为圆心跟随指针，opacity 走 --uhd-dur-base
   淡入；hover 的 transform/background 过渡只挂在 :hover/:active 态上，
   不改 .ess-card 的常态 transition——避免顶掉 .rv 入场过渡（.9s）的
   时序（入场仍走 .rv，进入 hover 时才按 .22s spring 接管）。
   全部 hover 规则限定 (hover:hover) and (pointer:fine)，触屏不误触。 */
.ess-card::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(340px circle at var(--mx) var(--my), rgba(255, 255, 255, 0.1), transparent 70%);
  opacity: 0;
  transition: opacity var(--uhd-dur-base) var(--uhd-ease-standard);
  pointer-events: none;
}
@media (hover: hover) and (pointer: fine) {
  .ess-card.is-in:hover {
    transform: translateY(-2px);
    background-color: var(--uhd-d-surface-2);
    transition:
      transform 0.22s var(--uhd-ease-spring),
      background-color 0.22s var(--uhd-ease-spring);
  }
  .ess-card.is-in:active {
    transform: translateY(0) scale(0.99);
    transition: transform 0.22s var(--uhd-ease-spring);
  }
  .ess-card:hover::after { opacity: 1; }
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
/* 设备线框：横排四台，名称在框下。不用叠放，窄屏另走 .ess-devices-m。 */
.ess-devices {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: center;
  gap: 22px 22px;
  min-height: 168px;
}
.dev {
  position: relative;
  flex: none;
  border: 1.5px solid var(--uhd-d-chip-border);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dev--tv { width: 168px; height: 100px; }
.dev--laptop { width: 148px; height: 92px; }
.dev--tablet { width: 72px; height: 104px; }
.dev--phone { width: 48px; height: 92px; }
.dev-label {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 8px);
  text-align: center;
  font-size: 13px;
  color: var(--uhd-d-text-2);
}
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
  /* 同步进度线（L-05）：宽度不再写死——初始 0，设备框 reveal 落定
     （.dev.is-in）后生长到 --bar-w 目标宽（现行 60% 口径，模板内联给值）；
     时长走 --uhd-dur-slow 档＋0.2s 延迟，让线跟在设备框入场之后生长。 */
  width: 0;
  transition: width var(--uhd-dur-slow) var(--uhd-ease-standard) 0.2s;
}
.dev.is-in .dev-bar i { width: var(--bar-w, 60%); }
.dev-note {
  flex: 1 0 100%;
  margin: 36px 0 0;
  text-align: center;
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
  gap: 18px;
  padding-bottom: 26px;
}
.dm-label {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 6px);
  text-align: center;
  font-size: 12px;
  color: var(--uhd-d-text-2);
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
  /* 同步进度线（L-05）：与桌面 .dev-bar 同口径——初始 0，宽卡 reveal
     落定（.ess-card.is-in，移动设备条无独立 .rv）后生长到 --bar-w。 */
  width: 0;
  transition: width var(--uhd-dur-slow) var(--uhd-ease-standard) 0.2s;
}
.ess-card.is-in .dm-bar i { width: var(--bar-w, 60%); }
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

/* 片库数据行：照友站大数据行——大数字（28px）＋小标签（12px）横排，数字收小量级 */
.ess-stats {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
  margin-top: 26px;
}
.ess-stat { display: flex; flex-direction: column; }
.ess-stat-num {
  font-family: var(--uhd-font-display);
  font-size: 28px;
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.15;
}
.ess-stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--uhd-d-text-2);
}
.ess-stats-note {
  margin: 10px 0 0;
  font-size: 11px;
  color: var(--uhd-d-text-2);
}

/* ---------- 页脚 ---------- */
.landing-foot {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  padding: 22px 28px 30px;
  border-top: var(--uhd-border-w) solid var(--uhd-d-border);
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-d-text-2);
}
.landing-foot-disclaimer {
  margin: 0;
  max-width: 760px;
  line-height: 1.7;
}
.landing-foot-disclaimer a {
  color: var(--uhd-d-text);
  text-decoration: none;
}
.landing-foot-disclaimer a:hover { text-decoration: underline; }
.landing-foot-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
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
  .m-sheet__close {
    position: absolute;
    top: 10px;
    right: 12px;
    z-index: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border: none;
    border-radius: 9999px;
    background: rgba(255, 255, 255, 0.1);
    color: var(--uhd-d-text);
    cursor: pointer;
  }
  .m-sheet__links {
    display: flex;
    flex-direction: column;
    padding: 4px 12px 12px;
  }
  .m-sheet__row {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    padding: 17px 18px;
    border: none;
    border-radius: 16px;
    background: transparent;
    color: var(--uhd-d-text);
    font-size: 18px;
    font-weight: var(--uhd-fw-medium);
    text-align: left;
    cursor: pointer;
  }
  .m-sheet__row svg {
    flex: none;
    width: 22px;
    height: 22px;
    opacity: 0.4;
  }
  .m-sheet__row .m-sheet__row-ext {
    width: 17px;
    height: 17px;
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
    padding: 18px;
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
    width: 44px;
    height: 44px;
    margin-bottom: 12px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.85);
  }
  .m-card__title {
    font-size: 16px;
    font-weight: var(--uhd-fw-semibold);
    line-height: 1.35;
  }
  .m-card__sub {
    margin-top: 3px;
    font-size: 13px;
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

  /* ---- Hero：照友站真机截图整屏构图（2026-11-14 用户发图亲点）——全幅星空棱镜
     背景作底（.hero-bg），文案压在左上暗部（眉题约在屏高 1/4 处），卖点 2×2
     压在背景图下部；旧「顶部视觉带＋文案在下」的分带构图作废 ---- */
  .hero {
    min-height: 100vh;
    min-height: 100svh;
    flex-direction: column;
    align-items: stretch;
  }
  .hero-bg { display: block; }
  .hero-visual,
  .hero-scrim { display: none; }
  .hero-copy {
    max-width: none;
    margin: 0;
    padding-bottom: 0;
  }
  .hero-inner {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: 22svh 16px 8svh;
  }
  .hero-inner .eyebrow {
    margin-bottom: 20px;
  }
  .hero-title {
    font-size: clamp(28px, 8.4vw, 34px);
    line-height: 1.3;
    margin-bottom: 16px;
    overflow: visible;
  }
  .hero-sub {
    font-size: 16px;
    line-height: 1.75;
    min-height: 3.5em;
    margin-bottom: 30px;
  }
  /* 双钮照截图量值：高 52px、字 16px、左右垫 20px、间距 14px 并排
     （主钮配方在 UhdButton，玻璃次钮等高对齐；2026-11-14 截图复核后已登录态两颗都显示） */
  .hero-actions { gap: 14px; }
  .hero-actions :deep(.uhd-btn--primary),
  .hero-actions :deep(.uhd-btn--secondary) {
    height: 52px;
    padding: 0 20px;
    font-size: 16px;
  }
  /* 首屏卖点 2×2 网格（#151 排法不变，尺寸照友站真机截图放大）：格内图标＋标题
     一行、说明小字换行到标题下方（左缘与标题对齐＝图标 24px＋chip 间距 13px）；
     整组以 margin-top:auto 压到 Hero 下部、叠在背景图上；仅移动端，桌面横排不变 */
  .hero-specs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px 24px;
    max-width: none;
    margin-top: auto;
  }
  .hero-specs :deep(.uhd-chip--spec) {
    /* 沿用桌面 grid 分列（图标列＋文案列）：图标列收 24px、列距 13px，
       说明与主标题同列左缘对齐。旧写法在 grid 之上残留 flex 的
       padding-left:37px，副标题被多推 37px（真机量值：主标题 x=51、
       副标题文字 x=88），2026-11-15 实锤后摘除。 */
    grid-template-columns: 24px auto;
    column-gap: 13px;
    row-gap: 6px;
    font-size: 21px;
  }
  .hero-specs :deep(.uhd-chip__icon svg) {
    width: 24px;
    height: 24px;
  }
  .hero-specs :deep(.uhd-chip__sub) {
    font-size: 13.5px;
  }
  /* 友站首屏底部无滚动提示（卖点之下只有背景地板），移动端隐藏 */
  .hero-scroll-hint {
    display: none;
  }

  /* ---- 流媒体伙伴：字标收小、一行排下不换行 ---- */
  .partners-sec { padding: 56px 20px 0; }
  .partner { font-size: 19px; }

  /* ---- 跑马灯：收紧字距行距与区块留白（返工单⑥）；
     底距 76→40：旧底距叠加钉住区羽化形成约三分之一屏死黑（2026-11-15 真机实锤） ---- */
  .marquee-sec { padding: 84px 0 40px; }
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
         色带层上下柔边融入黑底（带体羽化见 .pin-bands），文案绝对居下半区 ---- */
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
  /* 字号按友站小尺度收（2026-11-15 录屏帧量值）：总起主标题约 30px、钉住区眉题 12px */
  .essentials .sec-title { font-size: 30px; }
  .pin-copy .eyebrow { font-size: 12px; }
  .ess-stats { gap: 18px; flex-wrap: nowrap; }
  .ess-stat-num { font-size: 25px; }
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
@media (max-width: 420px) {
  /* 390 档：跑马灯标题与钉住区副题整句落在视口内。靠换行和字号，不靠把 overflow 全藏掉。 */
  .hero-actions { gap: 10px; }
  .hero-actions :deep(.uhd-btn--primary),
  .hero-actions :deep(.uhd-btn--secondary) {
    padding: 0 14px;
  }
  .hero-specs { gap: 14px 12px; }
  .hero-specs :deep(.uhd-chip--spec) { font-size: 16px; }
  .sec-title,
  .sec-title--platforms,
  .pin-title,
  .ess-h3 { overflow: visible; }
  .sec-title--platforms {
    font-size: clamp(22px, 6.6vw, 26px);
    line-height: 1.45;
  }
  .pin-title { font-size: clamp(28px, 7.7vw, 30px); }
  .pin-sub { font-size: 15px; }
  .pin-copy { padding: 0 16px; }
  .landing-foot { padding-left: 16px; padding-right: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .hero-el { animation: none; }
  /* 棱镜脉冲（L-02）：减动效时全关，只留静态照片图 */
  .hp-dot,
  .hp-beamglow,
  .hp-flash,
  .hp-refl { animation: none; }
  .marquee-track { animation: none; }
  .pin-bands { animation: none; }
  .rv,
  .pt-char {
    opacity: 1;
    transform: none;
    filter: none;
    transition: none;
  }
  .m-sheet,
  .m-scrim { animation: none; }
  .hero-sub-ticker-enter-active,
  .hero-sub-ticker-leave-active { animation: none; }
  /* 进度线（L-05）：减动效时直接落定目标宽，不生长 */
  .dev-bar i,
  .dm-bar i {
    width: var(--bar-w, 60%);
    transition: none;
  }
}
</style>
