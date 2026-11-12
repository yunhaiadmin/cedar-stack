<script setup lang="ts">
/**
 * 官网 www 首页（UHD 换皮批 1 · 深色营销面）——说明书逐页对照表第 18 行
 * 五区块骨架：100vh Hero（棱镜光束 + 文案轮换 + 规格 chip）→ 客户端/规格跑马灯
 * → 钉住滚动影院/音频展示（钉住只此一处，说明书第 5 节①）→ 一份订阅三卡 → 单行页脚。
 * 文案为 Aetrix 自有口径；营销数字一律不编造：片库统计位为占位待接（第 5 节④）。
 * 页面独立于应用壳（App.vue 对 landing 路由名不渲染全局顶栏/底坞），自带悬浮胶囊导航。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Monitor, Sun, Layers, ShieldCheck } from 'lucide-vue-next'
import UhdNavPill, { type UhdNavItem } from '@/components/uhd/UhdNavPill.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdChip from '@/components/uhd/UhdChip.vue'
import { branding } from '@/composables/useBranding'

const router = useRouter()

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

/* 钉住滚动展示：滚动进度驱动两态交叉淡入（约 2 个视口切换一态） */
const pinSection = ref<HTMLElement | null>(null)
const pinProgress = ref(0)
let rafPending = false
function updatePin() {
  rafPending = false
  const el = pinSection.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const total = rect.height - window.innerHeight
  if (total <= 0) return
  pinProgress.value = Math.min(1, Math.max(0, -rect.top / total))
}
function onScroll() {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(updatePin)
}

const reduceMotion = ref(false)
onMounted(() => {
  reduceMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (!reduceMotion.value) {
    heroTimer = setInterval(() => {
      heroSubIdx.value = (heroSubIdx.value + 1) % heroSubs.length
    }, 4200)
    window.addEventListener('scroll', onScroll, { passive: true })
    updatePin()
  }
})
onUnmounted(() => {
  if (heroTimer) clearInterval(heroTimer)
  window.removeEventListener('scroll', onScroll)
})

/* 跑马灯内容：Aetrix 自有口径——支持的客户端与规格字标，纯文字白标（第 5 节②） */
const marqueeClients = ['Emby', 'Infuse', 'Kodi', 'Apple TV', 'Android TV', 'Samsung TV', 'LG webOS', 'Windows', 'macOS', 'iOS', 'Android']
const marqueeSpecs = ['4K Ultra HD', 'HDR10+', 'Dolby Vision', 'Dolby Atmos', 'DTS:X', 'H.265 / HEVC', 'Hi-Res Audio', '空间音频']

const year = new Date().getFullYear()
</script>

<template>
  <div class="landing">
    <div class="landing-nav">
      <UhdNavPill
        :items="navItems"
        active-key="home"
        :brand="branding.site_name"
        brand-sub="SERVICE"
        cta-text="立即加入"
        @select="onNav"
        @cta="goRegister"
      />
    </div>

    <!-- ① Hero -->
    <header class="hero">
      <div class="hero-beam" aria-hidden="true" />
      <div class="hero-inner">
        <p class="eyebrow">{{ branding.site_name }} SERVICE</p>
        <h1 class="hero-title">你的私人影院<br />随时随地开场</h1>
        <p class="hero-sub" aria-live="polite">
          <Transition name="hero-sub-fade" mode="out-in">
            <span :key="heroSubIdx">{{ heroSubs[heroSubIdx] }}</span>
          </Transition>
        </p>
        <div class="hero-actions">
          <UhdButton variant="primary" dark @click="goRegister">免费注册</UhdButton>
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
      <p class="hero-scroll-hint" aria-hidden="true">↓ 向下探索更多精彩</p>
    </header>

    <!-- ② 跑马灯 -->
    <section class="marquee-sec">
      <p class="eyebrow eyebrow--center">CLIENTS &amp; SPECS</p>
      <h2 class="sec-title">在你顺手的设备上，<br />以它最好的规格播放</h2>
      <p class="sec-sub">播放交给专业客户端，服务器只做整理与供给——手机、电视、电脑，打开就能看。</p>
      <div class="marquee" aria-hidden="true">
        <div class="marquee-track marquee-track--a">
          <span v-for="(w, i) in [...marqueeClients, ...marqueeClients]" :key="`a-${i}`" class="marquee-word">{{ w }}</span>
        </div>
        <div class="marquee-track marquee-track--b">
          <span v-for="(w, i) in [...marqueeSpecs, ...marqueeSpecs]" :key="`b-${i}`" class="marquee-word">{{ w }}</span>
        </div>
      </div>
    </section>

    <!-- ③ 钉住滚动展示（仅此一处） -->
    <section ref="pinSection" class="pin-sec">
      <div class="pin-stage">
        <div class="pin-stripes" aria-hidden="true" />
        <div
          class="pin-copy"
          :style="{ opacity: String(1 - Math.min(1, Math.max(0, (pinProgress - 0.45) / 0.3))) }"
        >
          <p class="eyebrow eyebrow--center">CINEMA EXPERIENCE</p>
          <h2 class="pin-title">影院级画面</h2>
          <p class="pin-sub">4K Ultra HD · HDR10+ · Dolby Vision</p>
        </div>
        <div
          class="pin-copy"
          :style="{ opacity: String(Math.min(1, Math.max(0, (pinProgress - 0.55) / 0.3))) }"
        >
          <p class="eyebrow eyebrow--center">AUDIO EXPERIENCE</p>
          <h2 class="pin-title">影院级声音</h2>
          <p class="pin-sub">Dolby Atmos · Hi-Res Audio · 空间音频 · DTS:X 全兼容</p>
        </div>
      </div>
    </section>

    <!-- ④ 一份订阅 -->
    <section class="essentials">
      <p class="eyebrow eyebrow--center">SERVICE ESSENTIALS</p>
      <h2 class="sec-title">一份订阅，全部搞定</h2>
      <p class="sec-sub">一个账号打通所有设备与服务，不用为每块屏幕单独折腾。</p>

      <div class="ess-grid">
        <article class="ess-card ess-card--wide">
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
            <div class="dev dev--tv"><span class="dev-play" /><span class="dev-bar"><i style="width: 60%" /></span></div>
            <div class="dev dev--laptop"><span class="dev-play" /><span class="dev-bar"><i style="width: 60%" /></span></div>
            <div class="dev dev--tablet"><span class="dev-play" /></div>
            <div class="dev dev--phone"><span class="dev-play dev-play--sm" /></div>
            <p class="dev-note">上次同步 · 刚刚</p>
          </div>
        </article>

        <article class="ess-card">
          <p class="eyebrow">ZERO INTERRUPTION</p>
          <h3 class="ess-h3">没有广告，<br />只有电影</h3>
          <p class="ess-desc">从按下播放到片尾字幕，全程无插播、无贴片，沉浸感不被打断。</p>
          <div class="ess-timeline" aria-hidden="true">
            <div class="ess-timeline-meta"><span>00:00</span><span>全程无广告</span><span>02:15</span></div>
            <div class="ess-timeline-bar"><i /></div>
          </div>
        </article>

        <article class="ess-card ess-card--library">
          <div class="ess-wall" aria-hidden="true">
            <span v-for="n in 12" :key="n" class="ess-tile" :class="`ess-tile--${(n % 4) + 1}`" />
          </div>
          <p class="eyebrow">EVER-GROWING LIBRARY</p>
          <h3 class="ess-h3">片库自己长大</h3>
          <p class="ess-desc">新片自动扫描入库、刮削补齐海报与资料；追的剧更新了，自动出现在你的片库里。</p>
          <!-- 数字一律数据驱动：公开统计接口接入前只占位，不编造（说明书第 5 节④） -->
          <div class="ess-stats">
            <div class="ess-stat"><p class="ess-stat-num">—</p><p class="ess-stat-label">片库条目 · 数据接入中</p></div>
            <div class="ess-stat"><p class="ess-stat-num">—</p><p class="ess-stat-label">本周新增 · 数据接入中</p></div>
            <div class="ess-stat"><p class="ess-stat-num">—</p><p class="ess-stat-label">持续更新 · 数据接入中</p></div>
          </div>
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
  overflow-x: hidden;
}
.landing-nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 50;
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
  font-size: clamp(36px, 4.6vw, 56px);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.15;
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
.hero-inner {
  position: relative;
  width: 100%;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  padding: 130px 28px 120px;
  animation: uhd-blur-in var(--uhd-dur-hero) var(--uhd-ease-pop);
}
.hero-title {
  margin: 0 0 22px;
  font-size: clamp(44px, 6vw, var(--uhd-fs-h1));
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
  right: 30px;
  bottom: 26px;
  margin: 0;
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

/* ---------- 钉住滚动展示 ---------- */
.pin-sec {
  height: 220vh;
}
.pin-stage {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #000;
}
.pin-stripes {
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg,
    #123a6b 0%, #123a6b 12.5%, #0e7d8c 12.5%, #0e7d8c 25%,
    #d8d8d8 25%, #d8d8d8 37.5%, #d9a03c 37.5%, #d9a03c 50%,
    #d97b3f 50%, #d97b3f 62.5%, #c95d7d 62.5%, #c95d7d 75%,
    #7a5fc0 75%, #7a5fc0 87.5%, #3f6fd9 87.5%, #3f6fd9 100%);
  opacity: 0.34;
}
.pin-stripes::after {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse 90% 70% at 50% 55%, transparent 30%, rgba(0, 0, 0, 0.72) 100%);
}
.pin-copy {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 16vh;
  text-align: center;
  padding: 0 24px;
  transition: none;
}
.pin-title {
  margin: 0 0 14px;
  font-size: clamp(48px, 6.4vw, 76px);
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
  font-size: clamp(28px, 3vw, 38px);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.2;
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
.ess-stats {
  display: flex;
  gap: 40px;
  flex-wrap: wrap;
  margin-top: 30px;
}
.ess-stat-num {
  margin: 0 0 4px;
  font-family: var(--uhd-font-display);
  font-size: var(--uhd-fs-display);
  font-weight: var(--uhd-fw-black);
  line-height: 1;
}
.ess-stat-label {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
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
  .ess-grid { grid-template-columns: 1fr; }
  .ess-card--wide { grid-template-columns: 1fr; }
  .ess-devices { display: none; }
  .hero-specs { gap: 20px; }
}
@media (prefers-reduced-motion: reduce) {
  .hero-inner { animation: none; }
  .marquee-track { animation: none; }
  .hero-sub-fade-enter-active,
  .hero-sub-fade-leave-active { transition: none; }
}
</style>
