<script setup lang="ts">
/**
 * 官网定价（UHD 换皮批 1）——说明书逐页对照表第 19 行、第 5 节③
 * 深色营销面外壳（悬浮胶囊导航 + 定价 Hero）+ 与站内订阅共用的单选对比表组件
 * （UhdPlanCompare，官网版去掉结算栏，按钮「立即开通」跳登录后订阅）。
 * 套餐与价格全部走公开接口 paymentApi.plans() 实时读取，不写死任何价格；
 * 公益服（is_free）时不卖套餐，按后端下发口径展示免费开放说明。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Check } from 'lucide-vue-next'
import UhdNavPill, { type UhdNavItem } from '@/components/uhd/UhdNavPill.vue'
import UhdPlanCompare from '@/components/uhd/UhdPlanCompare.vue'
import { paymentApi, type SubscriptionPlan } from '@/api/economy'
import { branding } from '@/composables/useBranding'

const router = useRouter()

const navItems: UhdNavItem[] = [
  { key: 'home', label: '首页' },
  { key: 'pricing', label: '定价' },
  { key: 'login', label: '登录' },
]
function onNav(key: string) {
  if (key === 'home') router.push('/welcome')
  else if (key === 'login') router.push('/login')
}
function goRegister() {
  router.push({ path: '/login', query: { mode: 'register' } })
}

const loading = ref(true)
const failed = ref(false)
const plans = ref<SubscriptionPlan[]>([])
const plansEnabled = ref(true)
const isFree = ref(false)
const accessNote = ref('')

async function loadPlans() {
  loading.value = true
  failed.value = false
  try {
    const res = await paymentApi.plans()
    plans.value = res.plans || []
    plansEnabled.value = res.enabled !== false
    isFree.value = res.is_free === true || res.access_mode === 'free'
    accessNote.value = res.access_note || ''
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(loadPlans)

function onSelectPlan(_plan: SubscriptionPlan) {
  // 官网版无结算栏：开通动作回站内订阅页完成（未登录先登录）
  router.push('/login')
}

const includes = ['多设备同步观看', '无广告干扰', '4K · HDR 规格支持', '片库持续更新', '求片与追剧']
const faqs = [
  {
    q: '套餐怎么开通？',
    a: '注册账号并登录后，在站内订阅页选择套餐即可开通。官网价格由同一套餐配置实时读取，与站内完全一致。',
  },
  {
    q: '公益服和付费服有什么区别？',
    a: '公益服免费开放，凭注册码注册、靠日常活力保号；付费服按套餐订阅开通。两者是同一套服务的不同运营口径，可按自己的情况选择。',
  },
  {
    q: '支持哪些客户端？',
    a: 'Emby 官方客户端与 Infuse 等第三方播放器均可接入，手机、电视、电脑都支持，观看进度多端同步。',
  },
  {
    q: '可以几台设备同时看？',
    a: '同播路数按所选套餐的权益约定，见上方套餐规格；不同套餐档位不同，选购时留意对比表中的说明。',
  },
  {
    q: '价格以后会变吗？',
    a: '本页价格与站内订阅页同源、实时读取后台套餐配置；已开通的订阅在有效期内不受调价影响。',
  },
]
const year = new Date().getFullYear()
</script>

<template>
  <div class="pricing">
    <div class="pricing-nav">
      <UhdNavPill
        :items="navItems"
        active-key="pricing"
        :brand="branding.site_name"
        brand-sub="SERVICE"
        cta-text="立即加入"
        @select="onNav"
        @cta="goRegister"
      />
    </div>

    <header class="pricing-hero">
      <p class="eyebrow eyebrow--center">PRICING</p>
      <h1 class="pricing-title">简单透明的定价</h1>
      <p class="pricing-sub">一份订阅，全部设备通用。先注册免费体验，再按需要开通。</p>
    </header>

    <main class="pricing-main">
      <div v-if="loading" class="pricing-state" role="status">
        <span class="spinner" aria-hidden="true" />
        <span>正在加载…</span>
      </div>

      <div v-else-if="failed" class="pricing-state">
        <p>套餐加载失败，稍后再试。</p>
        <button type="button" class="retry-btn" @click="loadPlans">重试</button>
      </div>

      <div v-else-if="isFree" class="pricing-note">
        <h2 class="pricing-note-title">当前为公益服，免费开放</h2>
        <p class="pricing-note-text">{{ accessNote || '凭注册码注册即可使用，靠日常活力保号，无需购买套餐。' }}</p>
      </div>

      <div v-else-if="!plansEnabled || plans.length === 0" class="pricing-note">
        <h2 class="pricing-note-title">套餐暂未上架</h2>
        <p class="pricing-note-text">订阅套餐正在准备中，稍后再来看看；已注册用户可先免费浏览片库。</p>
      </div>

      <template v-else>
        <UhdPlanCompare :plans="plans" cta-text="立即开通" @select="onSelectPlan" />

        <p class="pricing-includes-title">所有套餐都包含</p>
        <ul class="pricing-includes">
          <li v-for="item in includes" :key="item">
            <Check :size="16" class="includes-check" aria-hidden="true" />{{ item }}
          </li>
        </ul>
      </template>

      <section class="pricing-faq">
        <h2 class="faq-title">常见问题</h2>
        <article v-for="f in faqs" :key="f.q" class="faq-card">
          <h3 class="faq-q">{{ f.q }}</h3>
          <p class="faq-a">{{ f.a }}</p>
        </article>
      </section>

      <section class="pricing-team">
        <div>
          <h2 class="team-title">团体与更多方案</h2>
          <p class="team-text">多人共用或有特殊需求？注册后联系管理员，获取适合你的方案。</p>
        </div>
        <RouterLink to="/login" class="team-btn">联系开通</RouterLink>
      </section>
    </main>

    <footer class="pricing-foot">
      <span>© {{ year }} {{ branding.site_name }} · 自建 Emby 影视服务平台</span>
      <span class="pricing-foot-links">
        <RouterLink to="/welcome">首页</RouterLink>
        <RouterLink to="/privacy">隐私政策</RouterLink>
        <RouterLink to="/login">登录</RouterLink>
      </span>
    </footer>
  </div>
</template>

<style scoped>
.pricing {
  min-height: 100vh;
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
}
.pricing-nav {
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

.pricing-hero {
  padding: 150px 28px 54px;
  text-align: center;
  animation: uhd-blur-in var(--uhd-dur-hero) var(--uhd-ease-pop);
}
.pricing-title {
  margin: 0 0 16px;
  font-size: clamp(40px, 5.6vw, 68px);
  font-weight: var(--uhd-fw-black);
  line-height: 1.1;
}
.pricing-sub {
  margin: 0 auto;
  max-width: 560px;
  font-size: var(--uhd-fs-lead);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-d-text-2);
}

/* 定价主体：浅色办事面（对照表第 19 行：深色外壳 + 单选表组件） */
.pricing-main {
  background: var(--uhd-l-bg);
  color: var(--uhd-l-text);
  padding: 56px 28px 84px;
}
.pricing-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 210px;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text-2);
}
.spinner {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-border);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 0.8s linear infinite;
}
.retry-btn {
  height: 38px;
  padding: 0 18px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font: inherit;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.pricing-note {
  max-width: 680px;
  margin: 0 auto;
  padding: 34px 32px;
  text-align: center;
  background: var(--uhd-warn-bg-2);
  border: var(--uhd-border-w) solid var(--uhd-warn-bg);
  border-radius: var(--uhd-radius-card-sm);
}
.pricing-note-title {
  margin: 0 0 10px;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-warn-text);
}
.pricing-note-text {
  margin: 0;
  font-size: var(--uhd-fs-body);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-warn-text);
}

.pricing-includes-title {
  margin: 54px 0 18px;
  text-align: center;
  font-size: var(--uhd-fs-title);
  font-weight: var(--uhd-fw-bold);
}
.pricing-includes {
  list-style: none;
  display: flex;
  justify-content: center;
  gap: 14px 30px;
  flex-wrap: wrap;
  margin: 0 auto;
  padding: 0;
  max-width: var(--uhd-container-pricing);
  font-size: var(--uhd-fs-body);
}
.pricing-includes li {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.includes-check { color: var(--uhd-success); }

/* FAQ：单列展开式（原站口径） */
.pricing-faq {
  max-width: 680px;
  margin: 76px auto 0;
}
.faq-title {
  margin: 0 0 26px;
  text-align: center;
  font-size: var(--uhd-fs-h2);
  font-weight: var(--uhd-fw-extrabold);
}
.faq-card {
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card-sm);
  padding: 28px;
  margin-bottom: 16px;
}
.faq-q {
  margin: 0 0 8px;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
}
.faq-a {
  margin: 0;
  font-size: var(--uhd-fs-body);
  line-height: 1.7;
  color: var(--uhd-l-text-2);
}

/* 团体横条 */
.pricing-team {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
  max-width: var(--uhd-container-pricing);
  margin: 56px auto 0;
  padding: 28px 30px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card);
}
.team-title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: var(--uhd-fw-bold);
}
.team-text {
  margin: 0;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text-2);
}
.team-btn {
  display: inline-flex;
  align-items: center;
  height: 44px;
  padding: 0 24px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  text-decoration: none;
  white-space: nowrap;
}

.pricing-foot {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  padding: 22px 28px 30px;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-d-text-2);
}
.pricing-foot-links {
  display: inline-flex;
  gap: 22px;
}
.pricing-foot-links a {
  color: var(--uhd-d-text-2);
  text-decoration: none;
}
.pricing-foot-links a:hover { color: var(--uhd-d-text); }

@media (prefers-reduced-motion: reduce) {
  .pricing-hero { animation: none; }
  .spinner { animation-duration: 1.6s; }
}
</style>
