<template>
  <div class="sub-page">
    <!-- 页头：左返回＋标题，右上真实入口（我的订阅）彩虹边框胶囊 -->
    <header class="sub-head">
      <button type="button" class="sub-back" aria-label="返回" @click="goBack">
        <ChevronLeft :size="24" aria-hidden="true" />
      </button>
      <h1 class="sub-title">订阅服务</h1>
      <RouterLink class="sub-head-link" to="/game/account/member">我的订阅</RouterLink>
    </header>

    <main class="sub-body">
      <div v-if="loading" class="sub-skeleton" aria-hidden="true"></div>

      <div v-else-if="loadError" class="sub-state">
        <p>订阅加载失败，请稍后重试</p>
        <button type="button" class="sub-retry" @click="loadAll">重试</button>
      </div>

      <!-- 公益服：不卖套餐，给免费开放说明＋资格入口（口径与补给站原「会员」区一致） -->
      <div v-else-if="isFreeRealm" class="sub-state">
        <p>当前为公益服，全片库免费开放，无需购买订阅</p>
        <p v-if="realmNote" class="sub-state__note">{{ realmNote }}</p>
        <RouterLink class="sub-retry" to="/game/account/member">查看我的资格</RouterLink>
      </div>

      <div v-else-if="!plansEnabled" class="sub-state">
        <p>订阅购买暂未开启，稍后再来看看</p>
        <button type="button" class="sub-retry" @click="loadAll">刷新看看</button>
      </div>

      <div v-else-if="plans.length === 0" class="sub-state">
        <p>套餐暂未上架，稍后再来看看</p>
        <button type="button" class="sub-retry" @click="loadAll">刷新看看</button>
      </div>

      <template v-else>
        <!-- 服分段切换：Aetrix 的真实维度是「服」（套餐 realm），多服时才出现 -->
        <div v-if="realmOptions.length > 1" class="sub-seg" role="tablist" aria-label="选择服">
          <button
            v-for="opt in realmOptions"
            :key="opt.value"
            type="button"
            role="tab"
            class="sub-seg__item"
            :class="{ 'sub-seg__item--on': opt.value === selectedRealm }"
            :aria-selected="opt.value === selectedRealm"
            @click="selectedRealm = opt.value"
          >{{ opt.label }}</button>
        </div>
        <p v-if="realmNote" class="sub-note">{{ realmNote }}</p>

        <!-- 套餐单选卡：选中提亮＋实心圆点，未选压暗；「当前」标记在用档 -->
        <section class="sub-cards" aria-label="选择套餐">
          <article
            v-for="plan in realmPlans"
            :key="plan.id"
            class="plan-card"
            :class="{ 'plan-card--on': plan.id === selectedPlanId }"
          >
            <button
              type="button"
              class="plan-card__head"
              :aria-pressed="plan.id === selectedPlanId"
              @click="selectedPlanId = plan.id"
            >
              <span class="plan-radio" :class="{ 'plan-radio--on': plan.id === selectedPlanId }" aria-hidden="true"></span>
              <span class="plan-name">{{ plan.name }}</span>
              <span v-if="isCurrent(plan)" class="plan-current">当前</span>
              <span class="plan-price">
                <span class="plan-price__row">
                  <b>¥ {{ fmtPrice(plan.price) }}</b><i>/ {{ durValue(plan.duration_days) }}</i>
                </span>
                <span v-if="monthlyEquiv(plan)" class="plan-price__per">约 ¥ {{ monthlyEquiv(plan) }} / 月</span>
              </span>
            </button>
            <ul v-if="specRows(plan).length || (plan.features && plan.features.length)" class="plan-specs">
              <li v-for="row in specRows(plan)" :key="row.label" class="plan-spec">
                <span class="plan-spec__label">{{ row.label }}</span>
                <span class="plan-spec__value">{{ row.value }}</span>
              </li>
              <li v-for="f in plan.features || []" :key="f" class="plan-spec plan-spec--feat">
                <span class="plan-spec__label"><Check :size="14" aria-hidden="true" /></span>
                <span class="plan-spec__value">{{ f }}</span>
              </li>
            </ul>
          </article>
        </section>

        <!-- 订阅时长：滑杆＋胶囊双向联动（档位＝在售套餐的真实时长） -->
        <section class="sub-dur" aria-label="订阅时长">
          <div class="sub-dur__head">
            <span class="sub-dur__label">订阅时长</span>
            <span class="sub-dur__value"><b>{{ selectedPlan ? durValueNum(selectedPlan.duration_days) : '–' }}</b> {{ selectedPlan ? durValueUnit(selectedPlan.duration_days) : '' }}</span>
          </div>
          <input
            type="range"
            min="0"
            :max="Math.max(0, realmPlans.length - 1)"
            step="1"
            :value="selectedPlanIndex"
            class="sub-slider"
            :style="{ '--fill': sliderFill }"
            aria-label="选择订阅时长"
            @input="onDurationSlider"
          />
          <div class="sub-dur__caps" role="group" aria-label="订阅时长快捷选择">
            <button
              v-for="(p, i) in realmPlans"
              :key="p.id"
              type="button"
              class="sub-cap"
              :class="{ 'sub-cap--on': i === selectedPlanIndex }"
              :aria-pressed="i === selectedPlanIndex"
              @click="selectedPlanIndex = i"
            >{{ durShort(p.duration_days) }}</button>
          </div>
        </section>

        <!-- 支付方式：人民币单轨（能量支付已随货币下线） -->
        <section class="sub-pay" aria-label="支付方式">
          <div class="sub-methods">
            <template v-if="methods.length > 1">
              <button
                v-for="m in methods"
                :key="m.id"
                type="button"
                class="sub-method"
                :class="{ 'sub-method--on': payMethod === m.id }"
                :aria-pressed="payMethod === m.id"
                @click="payMethod = m.id"
              >{{ m.name || '在线支付' }}</button>
            </template>
            <span v-else-if="methods.length === 1" class="sub-method sub-method--on">{{ methods[0].name || '在线支付' }}</span>
            <span v-if="methodsFailed" class="sub-pay__warn">支付方式加载失败，请检查网络后<button type="button" class="sub-inline-retry" @click="loadAll">重试</button></span>
            <span v-else-if="!methodsReady" class="sub-pay__warn">暂无可用支付方式，请联系服主</span>
          </div>
        </section>

        <!-- 优惠码：接现有 coupon 试算口（与下单同一套口径） -->
        <section v-if="couponEnabled" class="sub-coupon" aria-label="优惠码">
          <div class="sub-coupon__row">
            <label class="sub-coupon__box">
              <Tag :size="17" aria-hidden="true" />
              <input
                v-model="couponInput"
                class="sub-coupon__input"
                type="text"
                placeholder="优惠码（可选）"
                aria-label="优惠码（可选）"
                maxlength="64"
                autocomplete="off"
                :disabled="!!appliedCoupon"
                @keydown.enter.prevent="applyCoupon"
              />
            </label>
            <button
              v-if="!appliedCoupon"
              type="button"
              class="sub-coupon__apply"
              :disabled="!couponInput.trim() || couponApplying || !selectedPlan"
              @click="applyCoupon"
            >{{ couponApplying ? '验券中…' : '应用' }}</button>
            <button
              v-else
              type="button"
              class="sub-coupon__apply"
              aria-label="移除优惠券"
              @click="clearCoupon"
            >移除</button>
          </div>
          <p v-if="appliedCoupon" class="sub-coupon__ok">优惠券 <b>{{ appliedCoupon }}</b> 已应用，下单自动抵扣</p>
          <p v-else-if="couponError" class="sub-coupon__err">{{ couponError }}</p>
        </section>

        <!-- 价格明细：全部来自 coupon 试算口（与下单同口径，含优惠券与折扣权益；
             等级折扣已随等级体系于 2026-11-14 删除） -->
        <section v-if="selectedPlan" class="sub-summary" aria-label="价格明细">
          <div class="sub-sum-row">
            <span>{{ selectedPlan.name }} · {{ durValue(selectedPlan.duration_days) }}</span>
            <b>{{ moneyText(listTotal) }}</b>
          </div>
          <div v-if="otherCut > 0" class="sub-sum-row sub-sum-row--cut">
            <span>{{ otherCutLabel }}</span>
            <b>−{{ moneyText(otherCut) }}</b>
          </div>
        </section>
      </template>
    </main>

    <!-- 吸底结算条：左所选＋总价，右彩虹主按钮（有在用订阅＝续费，否则＝开通） -->
    <footer v-if="!loading && !isFreeRealm && selectedPlan" class="sub-settle">
      <div class="sub-settle__inner">
        <div class="sub-settle__info">
          <span class="sub-settle__name">{{ selectedPlan.name }} · {{ durValue(selectedPlan.duration_days) }}</span>
          <span class="sub-settle__price">
            <b>{{ moneyText(paidTotal) }}</b>
            <s v-if="savedTotal > 0">{{ moneyText(listTotal) }}</s>
          </span>
        </div>
        <button
          type="button"
          class="sub-settle__btn"
          :disabled="buyDisabled"
          :aria-busy="orderBusy"
          @click="buy"
        >{{ orderBusy ? '处理中…' : ctaText }}</button>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
/**
 * 订阅服务（/game/account/subscribe）——按 UHDNow 订阅页逻辑重做（用户 2026-11-13 08:39 令）：
 * 深色 app 式单列流：页头（返回＋标题＋我的订阅胶囊）→ 服分段 → 套餐单选卡
 * （选中提亮/未选压暗＋当前标记＋真实 entitlements 规格行）→ 时长滑杆＋胶囊
 * → 支付方式（人民币） → 优惠码 → 价格明细 → 吸底结算条。
 * 套餐/价格/时长/规格全部接口数据驱动；价格一律走 coupon 试算口
 * （与下单同一套口径：先等级折扣，再优惠券/折扣权益），下单仍走
 * paymentApi.createOrder，支付与资格发放规则不动。
 * Aetrix 没有的维度不硬造：原站「线路」分段以真实维度「服（realm）」替代；
 * 团体方案段无对应业务，整段省略；页头右上不放播放入口，放真实入口「我的订阅」。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Check, ChevronLeft, Tag } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import {
  couponApi,
  paymentApi,
  type CouponQuote,
  type PaymentMethod,
  type SubscriptionPlan,
} from '@/api/economy'
import { subscriptionApi, type MySubscription } from '@/api'

const toast = useToast()
const router = useRouter()

const loading = ref(true)
const loadError = ref(false)
const plans = ref<SubscriptionPlan[]>([])
const plansEnabled = ref(true)
const isFreeRealm = ref(false)
const realmNote = ref('')
const methods = ref<PaymentMethod[]>([])
const methodsReady = ref(false)
const methodsFailed = ref(false)
const payMethod = ref('alipay')
const mySubs = ref<MySubscription[]>([])
const couponEnabled = ref(true)

const orderBusy = ref(false)

/* ---------- 展示工具 ---------- */
/** 金额：整数不带小数，有分币时两位小数 */
function fmtPrice(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '–'
  return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2)
}
function moneyText(v: number | null | undefined): string {
  if (v == null) return '–'
  return `¥ ${fmtPrice(v)}`
}
/** 胶囊文案（与原站四档同形：1 个月/3 个月/6 个月/年付）；非整月天数如实写天数 */
function durShort(days: number): string {
  if (days >= 360) return '年付'
  if (days % 30 === 0) return `${days / 30} 个月`
  return `${days} 天`
}
/** 时长完整文案（价格后缀/结算摘要用） */
function durValue(days: number): string {
  if (days >= 360) return '1 年'
  if (days % 30 === 0) return `${days / 30} 个月`
  return `${days} 天`
}
function durValueNum(days: number): string {
  if (days >= 360) return '12'
  if (days % 30 === 0) return String(days / 30)
  return String(days)
}
function durValueUnit(days: number): string {
  if (days >= 360) return '个月'
  return days % 30 === 0 ? '个月' : '天'
}
/** 月均价（真实套餐价按时长折算，非虚构数字）；30 天档不显示 */
function monthlyEquiv(plan: SubscriptionPlan): string | null {
  if (plan.duration_days <= 30) return null
  const per = plan.price / (plan.duration_days / 30)
  return Number.isInteger(per) ? per.toLocaleString() : per.toFixed(1)
}

/* ---------- 服与套餐选择 ---------- */
const realmOptions = computed(() => {
  const names = [...new Set(plans.value.map((p) => p.realm_name || '默认'))]
  return names.map((n) => ({ value: n, label: n }))
})
const selectedRealm = ref('')
watch(realmOptions, (opts) => {
  if (!opts.some((o) => o.value === selectedRealm.value)) {
    selectedRealm.value = opts[0]?.value ?? ''
  }
}, { immediate: true })
/** 当前服套餐：按时长升序（滑杆/胶囊的档位顺序与卡片顺序一致） */
const realmPlans = computed(() => {
  const realm = selectedRealm.value || realmOptions.value[0]?.value || '默认'
  return [...plans.value]
    .filter((p) => (p.realm_name || '默认') === realm)
    .sort((a, b) => a.duration_days - b.duration_days || a.price - b.price)
})
const selectedPlanId = ref<number | null>(null)
const selectedPlan = computed(() => realmPlans.value.find((p) => p.id === selectedPlanId.value) ?? null)
const selectedPlanIndex = computed<number>({
  get: () => Math.max(0, realmPlans.value.findIndex((p) => p.id === selectedPlanId.value)),
  set: (i) => {
    const p = realmPlans.value[i]
    if (p) selectedPlanId.value = p.id
  },
})
const sliderFill = computed(() => {
  const n = realmPlans.value.length
  if (n <= 1) return '0%'
  return `${(selectedPlanIndex.value / (n - 1)) * 100}%`
})
function onDurationSlider(e: Event) {
  const v = Number((e.target as HTMLInputElement).value)
  if (Number.isFinite(v)) selectedPlanIndex.value = v
}

/* ---------- 当前订阅（「当前」标记与续费文案） ---------- */
const activeSub = computed(
  () => mySubs.value.find((s) => s.status === 'active' && s.days_left > 0) ?? null,
)
function isCurrent(plan: SubscriptionPlan): boolean {
  const s = activeSub.value
  if (!s || s.plan_name !== plan.name) return false
  if (s.realm_id != null && plan.realm_id != null && s.realm_id !== plan.realm_id) return false
  return true
}
watch(realmPlans, (list) => {
  if (!list.some((p) => p.id === selectedPlanId.value)) {
    const cur = list.find((p) => isCurrent(p))
    const popular = list.find((p) => p.is_popular)
    selectedPlanId.value = cur?.id ?? popular?.id ?? list[0]?.id ?? null
  }
}, { immediate: true })
const ctaText = computed(() => (activeSub.value ? '续费' : '开通'))

/* ---------- 规格行：只映射真实 entitlements ---------- */
function specRows(plan: SubscriptionPlan): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [
    { label: '时长', value: `${plan.duration_days} 天` },
  ]
  const ent = plan.entitlements || {}
  if (ent.media_seek_monthly_limit != null) {
    rows.push({ label: '求片额度', value: `每月 ${ent.media_seek_monthly_limit} 部` })
  }
  if (ent.concurrent_streams != null) {
    rows.push({ label: '同播路数', value: `${ent.concurrent_streams} 路` })
  }
  return rows
}

/* ---------- 试算（与下单同一套口径） ---------- */
const quote = ref<CouponQuote | null>(null)
const couponInput = ref('')
const appliedCoupon = ref('')
const couponError = ref('')
const couponApplying = ref(false)
let quoteToken = 0

async function refreshQuote() {
  const plan = selectedPlan.value
  if (!plan) {
    quote.value = null
    return
  }
  const token = ++quoteToken
  try {
    const q = await couponApi.quote({
      code: appliedCoupon.value,
      kind: 'subscription',
      item_id: plan.id,
    })
    if (token !== quoteToken) return
    quote.value = q
  } catch (err) {
    if (token !== quoteToken) return
    quote.value = null
    // 已应用的券在新选择下试算失败＝对当前选择不可用，诚实移除并提示
    if (appliedCoupon.value) {
      appliedCoupon.value = ''
      couponError.value = '这张券不适用于当前选择，已移除'
    } else {
      couponError.value = ''
      void err
    }
  }
}

async function applyCoupon() {
  const code = couponInput.value.trim()
  const plan = selectedPlan.value
  if (!code || !plan || couponApplying.value) return
  couponApplying.value = true
  couponError.value = ''
  try {
    const q = await couponApi.quote({
      code,
      kind: 'subscription',
      item_id: plan.id,
    })
    if (!q.valid) {
      couponError.value = '这张券用不上，换一张试试'
      return
    }
    appliedCoupon.value = q.code || code
    quote.value = q
    couponInput.value = ''
    toast.success('优惠券已应用，下单自动抵扣')
  } catch (err) {
    couponError.value = humanizeError(err)
  } finally {
    couponApplying.value = false
  }
}

function clearCoupon() {
  appliedCoupon.value = ''
  couponError.value = ''
  void refreshQuote()
}

watch(selectedPlanId, () => {
  couponError.value = ''
  void refreshQuote()
})

const listTotal = computed<number | null>(() => {
  const plan = selectedPlan.value
  if (!plan) return null
  if (quote.value) return quote.value.list_price
  return plan.price
})
const paidTotal = computed<number | null>(() => {
  if (quote.value) return quote.value.paid_amount
  return listTotal.value
})
const otherCut = computed(() =>
  Math.max(0, quote.value?.discount_amount ?? 0),
)
const otherCutLabel = computed(() => {
  if (appliedCoupon.value) return `优惠券 ${appliedCoupon.value}`
  if (quote.value?.exchange_discount_pct) return '折扣权益'
  return '优惠'
})
const savedTotal = computed(() =>
  listTotal.value != null && paidTotal.value != null
    ? Math.max(0, listTotal.value - paidTotal.value)
    : 0,
)

/* ---------- 下单（口径与补给站原结算栏一致，规则不动） ---------- */
const buyDisabled = computed(() => {
  if (orderBusy.value || !selectedPlan.value) return true
  return !methodsReady.value
})

async function buy() {
  const plan = selectedPlan.value
  if (!plan || buyDisabled.value) return
  orderBusy.value = true
  try {
    const res = await paymentApi.createOrder({
      kind: 'subscription',
      item_id: plan.id,
      payment_method: payMethod.value,
      coupon_code: appliedCoupon.value ? appliedCoupon.value : undefined,
    })
    if (res.pay_url) {
      toast.success('正在跳转支付')
      window.location.href = res.pay_url
    } else {
      toast.error('支付链接获取失败，请稍后再试')
    }
  } catch (err) {
    toast.error(humanizeError(err))
  } finally {
    orderBusy.value = false
  }
}

function goBack() {
  if (window.history.length > 1) router.back()
  else void router.push('/game/account/member')
}

/* ---------- 加载 ---------- */
async function loadAll() {
  loading.value = true
  loadError.value = false
  const [pls, mth, subs, cpCfg] = await Promise.allSettled([
    paymentApi.plans(),
    paymentApi.methods(),
    subscriptionApi.getMine(),
    couponApi.config(),
  ])
  if (pls.status === 'fulfilled') {
    plans.value = pls.value.plans || []
    plansEnabled.value = pls.value.enabled !== false
    isFreeRealm.value = pls.value.is_free === true || pls.value.access_mode === 'free'
    realmNote.value = pls.value.access_note || ''
  } else {
    loadError.value = true
  }
  if (mth.status === 'fulfilled') {
    methods.value = Array.isArray(mth.value) ? mth.value : []
    methodsReady.value = methods.value.length > 0
    methodsFailed.value = false
    if (methods.value.length > 0) payMethod.value = methods.value[0].id
  } else {
    methods.value = []
    methodsReady.value = false
    methodsFailed.value = true
  }
  if (subs.status === 'fulfilled') mySubs.value = subs.value
  if (cpCfg.status === 'fulfilled') couponEnabled.value = cpCfg.value.enabled !== false
  loading.value = false
  void refreshQuote()
}

onMounted(() => {
  void loadAll()
})
</script>

<style scoped>
/* ============ 订阅服务页：深色 app 单列流（UHD 语言） ============ */
.sub-page {
  min-height: 100dvh;
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}

/* 页头：返回＋标题＋右上彩虹边框胶囊（真实入口） */
.sub-head {
  max-width: 760px;
  margin: 0 auto;
  padding: calc(14px + env(safe-area-inset-top)) 16px 6px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.sub-back {
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  margin-left: -8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--uhd-d-text);
  cursor: pointer;
}
.sub-back:active {
  background: rgba(255, 255, 255, 0.08);
}
.sub-title {
  flex: 1 1 auto;
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 20px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  color: var(--uhd-d-text);
}
/* 彩虹边框胶囊：彩虹渐变边＋深色毛玻璃底＋玻璃栈（原站「网页观影」位，改为真实入口；
   2026-11-13 补齐玻璃：内高光 + 柔影 + backdrop） */
.sub-head-link {
  flex: 0 0 auto;
  padding: 7px 14px;
  border-radius: 999px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-d-text);
  text-decoration: none;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  border: 1.5px solid transparent;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -12px rgba(0, 0, 0, 0.5);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}

.sub-body {
  max-width: 760px;
  margin: 0 auto;
  padding: 14px 16px 148px;
  display: flex;
  flex-direction: column;
  gap: 26px;
}

/* 骨架与状态 */
.sub-skeleton {
  height: 320px;
  border-radius: var(--uhd-r-lg);
  background: linear-gradient(100deg, #101010 40%, #181818 50%, #101010 60%);
  background-size: 200% 100%;
  animation: sub-shimmer 1.4s infinite;
}
@keyframes sub-shimmer {
  from { background-position: 120% 0; }
  to { background-position: -80% 0; }
}
.sub-state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 26px 22px;
  border-radius: var(--uhd-r-lg);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  color: var(--uhd-d-text-2);
  font-size: var(--uhd-fs-body);
}
.sub-state p {
  margin: 0;
}
.sub-state__note {
  color: var(--uhd-d-text-3);
  font-size: var(--uhd-fs-caption);
}
.sub-retry {
  padding: 9px 18px;
  border-radius: 999px;
  border: 1px solid var(--uhd-d-border);
  background: var(--uhd-d-surface-raised);
  color: var(--uhd-d-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
  text-decoration: none;
}

/* 分段切换（服 / 支付口径共用）：胶囊容器＋深底提亮选中项 */
.sub-seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  width: fit-content;
  max-width: 100%;
  border-radius: 999px;
  background: #1a1a1c;
  border: 1px solid var(--uhd-d-border);
  overflow-x: auto;
  scrollbar-width: none;
}
.sub-seg::-webkit-scrollbar {
  display: none;
}
.sub-seg__item {
  flex: 0 0 auto;
  padding: 8px 18px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--uhd-d-text-2);
  font-family: inherit;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
  transition: background var(--uhd-dur-ui) ease, color var(--uhd-dur-ui) ease;
}
.sub-seg__item--on {
  background: #2e2e33;
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
}
.sub-note {
  margin: -14px 2px 0;
  color: var(--uhd-d-text-3);
  font-size: var(--uhd-fs-caption);
  line-height: 1.55;
}

/* 套餐单选卡：选中提亮（#1d 面＋亮边＋实心圆点），未选压暗（无面、整卡降透明） */
.sub-cards {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.plan-card {
  border-radius: var(--uhd-r-lg);
  border: 1px solid transparent;
  padding: 4px 16px;
  opacity: 0.48;
  transition: opacity var(--uhd-dur-ui) ease, background var(--uhd-dur-ui) ease;
}
.plan-card--on {
  opacity: 1;
  background: #1d1d20;
  border-color: rgba(255, 255, 255, 0.14);
}
.plan-card__head {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 0 10px;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.plan-radio {
  flex: 0 0 auto;
  width: 21px;
  height: 21px;
  margin-top: 5px;
  border-radius: 50%;
  border: 2px solid #6b6b70;
  box-sizing: border-box;
}
.plan-radio--on {
  border-color: #fff;
  background: radial-gradient(circle, #fff 0 4px, transparent 4.6px);
}
.plan-name {
  padding-top: 3px;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-d-text);
  white-space: nowrap;
}
.plan-current {
  margin-top: 6px;
  font-size: var(--uhd-fs-micro);
  font-weight: var(--uhd-fw-medium);
  color: var(--uhd-d-text-3);
  white-space: nowrap;
}
.plan-price {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.plan-price__row {
  display: flex;
  align-items: baseline;
  gap: 5px;
}
.plan-price__row b {
  font-family: var(--uhd-font-display);
  font-size: 30px;
  font-weight: var(--uhd-fw-black);
  letter-spacing: -0.02em;
  color: var(--uhd-d-text);
}
.plan-price__row i {
  font-style: normal;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-3);
}
.plan-price__per {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-3);
}
.plan-specs {
  list-style: none;
  margin: 2px 0 0;
  padding: 0 0 10px 31px;
}
.plan-spec {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 0;
  border-top: 1px solid #26262a;
  font-size: var(--uhd-fs-caption);
}
.plan-spec__label {
  flex: 0 0 76px;
  color: var(--uhd-d-text-3);
  display: inline-flex;
  align-items: center;
}
.plan-spec__value {
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-medium);
}
.plan-spec--feat .plan-spec__value {
  color: var(--uhd-d-text-2);
}

/* 时长：大字当前值＋滑杆＋四档胶囊 */
.sub-dur__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.sub-dur__label {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-d-text-2);
}
.sub-dur__value {
  color: var(--uhd-d-text-3);
  font-size: var(--uhd-fs-body);
}
.sub-dur__value b {
  font-family: var(--uhd-font-sans);
  font-size: 26px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-d-text);
  margin-right: 2px;
}
.sub-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 26px;
  margin: 8px 0 2px;
  background: transparent;
  cursor: pointer;
}
.sub-slider::-webkit-slider-runnable-track {
  height: 4px;
  border-radius: 2px;
  background: linear-gradient(to right, #fff 0%, #fff var(--fill, 0%), #3a3a3f var(--fill, 0%), #3a3a3f 100%);
}
.sub-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 21px;
  height: 21px;
  margin-top: -8.5px;
  border-radius: 50%;
  background: #111;
  border: 2.5px solid #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
}
.sub-slider::-moz-range-track {
  height: 4px;
  border-radius: 2px;
  background: #3a3a3f;
}
.sub-slider::-moz-range-progress {
  height: 4px;
  border-radius: 2px;
  background: #fff;
}
.sub-slider::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #111;
  border: 2.5px solid #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
}
.sub-dur__caps {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.sub-cap {
  padding: 8px 16px;
  border-radius: 999px;
  border: 1px solid var(--uhd-d-border);
  background: transparent;
  color: var(--uhd-d-text-2);
  font-family: inherit;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
  transition: background var(--uhd-dur-ui) ease, color var(--uhd-dur-ui) ease;
}
.sub-cap--on {
  background: #2e2e33;
  border-color: transparent;
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
}

/* 支付口径 */
.sub-pay {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sub-methods {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.sub-method {
  padding: 8px 16px;
  border-radius: 999px;
  border: 1px solid var(--uhd-d-border);
  background: transparent;
  color: var(--uhd-d-text-2);
  font-family: inherit;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
.sub-method--on {
  background: #2e2e33;
  border-color: transparent;
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
  cursor: default;
}
.sub-pay__warn {
  color: var(--uhd-warn);
  font-size: var(--uhd-fs-caption);
}
.sub-inline-retry {
  border: none;
  background: transparent;
  color: var(--uhd-d-text);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
  padding: 0 2px;
}
/* 优惠码：胶囊输入＋应用钮同行同高 */
.sub-coupon__row {
  display: flex;
  gap: 10px;
  align-items: stretch;
}
.sub-coupon__box {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 0 16px;
  min-height: 46px;
  border-radius: 999px;
  background: #1a1a1c;
  border: 1px solid var(--uhd-d-border);
  color: var(--uhd-d-text-3);
}
.sub-coupon__input {
  flex: 1 1 auto;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--uhd-d-text);
  font-family: inherit;
  font-size: var(--uhd-fs-body);
}
.sub-coupon__input::placeholder {
  color: var(--uhd-d-text-3);
}
.sub-coupon__input:disabled {
  color: var(--uhd-d-text-2);
}
.sub-coupon__apply {
  flex: 0 0 auto;
  padding: 0 22px;
  min-height: 46px;
  border-radius: 999px;
  border: none;
  background: #2e2e33;
  color: var(--uhd-d-text);
  font-family: inherit;
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.sub-coupon__apply:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.sub-coupon__ok {
  margin: 10px 2px 0;
  color: var(--uhd-ok);
  font-size: var(--uhd-fs-caption);
}
.sub-coupon__err {
  margin: 10px 2px 0;
  color: var(--uhd-danger);
  font-size: var(--uhd-fs-caption);
}

/* 价格明细 */
.sub-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px;
  border-radius: var(--uhd-r-lg);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
}
.sub-sum-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-d-text-2);
}
.sub-sum-row b {
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
  font-variant-numeric: tabular-nums;
}
.sub-sum-row--cut b {
  color: var(--uhd-ok);
}

/* 吸底结算条：内容滚走它不动；左摘要两行，右彩虹大胶囊 */
.sub-settle {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 60;
  background: rgba(10, 10, 10, 0.88);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  border-top: 1px solid rgba(255, 255, 255, 0.09);
  padding-bottom: env(safe-area-inset-bottom);
}
.sub-settle__inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.sub-settle__info {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.sub-settle__name {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub-settle__price {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.sub-settle__price b {
  font-family: var(--uhd-font-display);
  font-size: 24px;
  font-weight: var(--uhd-fw-black);
  letter-spacing: -0.01em;
  color: var(--uhd-d-text);
}
.sub-settle__price s {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-3);
}
/* 结算主钮 UHD 口径（2026-11-13 补齐七彩＋玻璃）：由满面彩虹填充改彩虹边框 +
   深色毛玻璃底 + 玻璃栈（内高光 + 柔影 + backdrop），与全站主钮统一 */
.sub-settle__btn {
  flex: 0 0 auto;
  min-width: 132px;
  padding: 14px 30px;
  border: 1.5px solid transparent;
  border-radius: 999px;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  color: #fff;
  font-family: inherit;
  font-size: 16px;
  font-weight: var(--uhd-fw-bold);
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -10px rgba(0, 0, 0, 0.5);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  transition: transform var(--uhd-dur-ui) ease, opacity var(--uhd-dur-ui) ease;
}
.sub-settle__btn:active {
  transform: scale(0.97);
}
/* 禁用：彩虹退场、中性灰 */
.sub-settle__btn:disabled {
  background: var(--uhd-d-surface-raised);
  border-color: var(--uhd-d-border-2);
  color: var(--uhd-d-text-2);
  cursor: not-allowed;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

@media (min-width: 1024px) {
  .sub-head {
    padding-top: calc(28px + env(safe-area-inset-top));
  }
  .sub-body {
    padding-top: 22px;
  }
  .plan-price__row b {
    font-size: 34px;
  }
}
</style>
