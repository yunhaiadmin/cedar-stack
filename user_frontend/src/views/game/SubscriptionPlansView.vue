<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳；手机原样 -->
  <UhdAccountShellGate>
  <div class="sub-page">
    <header class="sub-head">
      <button type="button" class="sub-back" aria-label="返回" @click="goBack">
        <ChevronLeft :size="24" aria-hidden="true" />
      </button>
      <h1 class="sub-title">订阅服务</h1>
    </header>

    <main class="sub-body">
      <!-- 加载态（通用口径）：数据未到位前只许中性加载态——转圈＋骨架＋加载文案，
           不渲染任何结论性文案或半成品控件；骨架跟随页面主题，浅色页用浅色骨架 -->
      <div v-if="loading" class="sub-loading" role="status" aria-label="正在加载订阅">
        <span class="sub-spinner" aria-hidden="true"></span>
        <p class="sub-loading__text">正在加载订阅…</p>
        <div class="sub-skeleton" aria-hidden="true"></div>
        <div class="sub-skeleton sub-skeleton--short" aria-hidden="true"></div>
      </div>

      <div v-else-if="loadError" class="sub-state">
        <p>订阅加载失败，请稍后重试</p>
        <button type="button" class="sub-retry" @click="loadAll">重试</button>
      </div>

      <!-- 公益服：不卖套餐，给免费开放说明 -->
      <div v-else-if="isFreeRealm" class="sub-state">
        <p>当前为公益服，全片库免费开放，无需购买订阅</p>
        <p v-if="realmNote" class="sub-state__note">{{ realmNote }}</p>
      </div>

      <div v-else-if="!plansEnabled && !walletEnabled" class="sub-state">
        <p>订阅购买暂未开启，稍后再来看看</p>
        <button type="button" class="sub-retry" @click="loadAll">刷新看看</button>
      </div>

      <div v-else-if="plans.length === 0" class="sub-state">
        <p>套餐暂未上架，稍后再来看看</p>
        <button type="button" class="sub-retry" @click="loadAll">刷新看看</button>
      </div>

      <div v-else class="sub-layout">
        <!-- 线路分组切换（照友站 f039）：浅灰胶囊＋选中段白底轻阴影；
             数据驱动——套餐按线路（服）名分组，有几组显示几组，只有一组不显示 -->
        <div v-if="realmOptions.length > 1" class="sub-seg" role="tablist" aria-label="选择线路">
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

        <!-- 套餐纵向全宽卡片列表（照友站 f039/f036）：一档一卡；
             选中档白卡＋轻阴影，未选档无卡底；卡头单选圈＋档名＋右侧价格，
             分隔线下规格逐行（标签灰小字／值中黑） -->
        <section class="plan-list" aria-label="选择套餐">
          <article
            v-for="(plan, pi) in realmPlans"
            :key="plan.id"
            class="plan-card"
            :class="{ 'plan-card--on': plan.id === selectedPlanId }"
            @click="selectedPlanId = plan.id"
          >
            <div class="plan-card__head">
              <span class="plan-radio" :class="{ 'plan-radio--on': plan.id === selectedPlanId }" aria-hidden="true"></span>
              <span class="plan-card__name">{{ plan.name }}</span>
              <span v-if="isCurrent(plan)" class="plan-current">当前</span>
              <span class="plan-card__pricebox">
                <span class="plan-card__price">¥ {{ fmtPrice(monthlyOf(plan)) }}<small> / 月</small></span>
                <span class="plan-card__annual">年付 ¥ {{ fmtPrice(listPriceFor(plan, 12)) }}</span>
              </span>
            </div>
            <div class="plan-specs">
              <div v-for="row in compareRows" :key="row.label" class="plan-spec">
                <span class="plan-spec__label">{{ row.label }}</span>
                <span class="plan-spec__value">{{ row.cells[pi] }}</span>
              </div>
            </div>
          </article>
        </section>

        <!-- 订阅时长（照友站 f036）：页内平铺——滑杆 1–12 个月＋1/3/6/年付分段；
             3/6 个月无折扣，仅年付＝11×月价（现行计价口径不变） -->
        <section class="sub-dur" aria-label="订阅时长">
          <div class="sub-dur__head">
            <span class="sub-dur__label">订阅时长</span>
            <span class="sub-dur__value"><b>{{ months }}</b> 个月{{ months === 12 ? '（年付）' : '' }}</span>
          </div>
          <input
            type="range"
            min="1"
            max="12"
            step="1"
            :value="months"
            class="sub-slider"
            :style="{ '--fill': sliderFill }"
            aria-label="选择订阅时长（月）"
            @input="onMonthsSlider"
          />
          <div class="sub-dur__caps" role="group" aria-label="订阅时长快捷选择">
            <button
              v-for="cap in MONTH_CAPS"
              :key="cap.months"
              type="button"
              class="sub-cap"
              :class="{ 'sub-cap--on': months === cap.months }"
              :aria-pressed="months === cap.months"
              @click="months = cap.months"
            >{{ cap.label }}</button>
          </div>
          <p class="sub-dur__hint">年付按 11 个月计价，等于送 1 个月</p>
        </section>

        <!-- 优惠码（照友站 f036 页内形态）：接现有 coupon 试算口，与下单同一套口径 -->
        <div v-if="couponEnabled" class="sub-coupon">
          <div class="sub-coupon__row">
            <label class="sub-coupon__box">
              <Tag :size="16" aria-hidden="true" />
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
        </div>

        <!-- 结算明细（页内小字行）：周期／抵扣／余额；吸底栏只放结论三件套 -->
        <section v-if="selectedPlan" class="sub-summary" aria-label="结算明细">
          <div class="sum-row">
            <span>续费周期</span>
            <b>{{ cycleText }}</b>
          </div>
          <div v-if="couponCut > 0" class="sum-row sum-row--cut">
            <span>{{ couponCutLabel }}</span>
            <b>−{{ moneyText(couponCut) }}</b>
          </div>
          <div v-if="switchCredit > 0" class="sum-row sum-row--cut">
            <span>旧套餐已抵扣</span>
            <b>−{{ moneyText(switchCredit) }}</b>
          </div>
          <div class="sum-row">
            <span>钱包余额</span>
            <b>{{ moneyText(walletBalance) }}</b>
          </div>
          <p v-if="!walletEnabled" class="sum-note">钱包暂未开启，暂时无法购买</p>
          <p v-else-if="balanceShortfall > 0" class="sum-note" role="alert">
            钱包余额不足，还差 ¥ {{ fmtPrice(balanceShortfall) }}，充值后即可购买
          </p>
        </section>
      </div>
    </main>

    <!-- 吸底结算栏（照友站 f039/f036）：常驻底部——左已选套餐·时长＋总价，
         右彩虹主钮；点主钮仍先弹确认弹窗算明细账（业务口径不变） -->
    <footer v-if="!loading && selectedPlan && realmPlans.length" class="sub-sticky">
      <div class="sub-sticky__inner">
        <div class="sub-sticky__left">
          <span class="sub-sticky__name">{{ selectedPlan.name }} · {{ months }} 个月</span>
          <span class="sub-sticky__total">{{ moneyText(payableTotal) }}<s v-if="savedTotal > 0">{{ moneyText(listTotal) }}</s></span>
        </div>
        <button
          type="button"
          class="sub-sticky__cta"
          :disabled="ctaDisabled"
          @click="openConfirm"
        >{{ ctaText }}</button>
      </div>
    </footer>

    <!-- 确认弹窗（照友站）：先算明细账，确认才扣钱包余额 -->
    <UhdModal v-model="confirmOpen" :title="selectedPlan ? `订阅 ${selectedPlan.name}` : '订阅'" :width="420">
      <div v-if="selectedPlan" class="cfm">
        <div class="cfm-row">
          <span>流量限额</span>
          <b>{{ trafficText(selectedPlan) }}</b>
        </div>
        <div class="cfm-row">
          <span>切换类型</span>
          <b>{{ switchTypeText }}</b>
        </div>
        <div class="cfm-row">
          <span>当前账户余额</span>
          <b>{{ moneyText(walletBalance) }}</b>
        </div>
        <div class="cfm-row">
          <span>新套餐金额</span>
          <b>{{ moneyText(listTotal) }}</b>
        </div>
        <div v-if="couponCut > 0" class="cfm-row cfm-row--cut">
          <span>{{ couponCutLabel }}</span>
          <b>−{{ moneyText(couponCut) }}</b>
        </div>
        <div v-if="switchCredit > 0" class="cfm-row cfm-row--cut">
          <span>旧套餐已抵扣</span>
          <b>−{{ moneyText(switchCredit) }}</b>
        </div>
        <div class="cfm-row cfm-row--total">
          <span>本次需支付</span>
          <b>{{ moneyText(payableTotal) }}</b>
        </div>
        <div class="cfm-row">
          <span>购买后结余</span>
          <b :class="{ 'cfm-neg': balanceAfter < 0 }">{{ moneyText(balanceAfter) }}</b>
        </div>
        <p v-if="balanceAfter < 0" class="cfm-warn" role="alert">余额不足，还差 ¥ {{ fmtPrice(-balanceAfter) }}</p>
        <p v-if="confirmError" class="cfm-warn" role="alert">{{ confirmError }}</p>
      </div>
      <template #footer>
        <button type="button" class="cfm-btn cfm-btn--ghost" :disabled="orderBusy" @click="confirmOpen = false">返回修改</button>
        <button
          v-if="balanceAfter < 0"
          type="button"
          class="cfm-btn cfm-btn--main"
          @click="goRecharge"
        >前往充值</button>
        <button
          v-else
          type="button"
          class="cfm-btn cfm-btn--main"
          :disabled="orderBusy || !selectedPlan"
          :aria-busy="orderBusy"
          @click="confirmBuy"
        >{{ orderBusy ? '处理中…' : '确认支付' }}</button>
      </template>
    </UhdModal>
  </div>
  </UhdAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 订阅服务（/game/account/subscribe）——2026-11-14 照友站（UHDNow）
 * 实测形态重做（用户 22:03 亲令，四处口径全部按友站）：
 * - 时长：滑杆选 1–12 个月＋分段「1 个月/3 个月/6 个月/年付」双
 *   控件联动；3/6 个月无折扣，仅年付＝11×月价。我方套餐仍以
 *   (总价, 总天数) 存储，月价＝总价÷基准月数由后端下发
 *   （启程 ¥18/月、远行 ¥15/月、环游 ¥12.5/月），前端不自算口径；
 * - 套餐呈现：纵向全宽卡片列表（2026-11-15 照友站录屏 f039/f036 重做，
 *   取代三列对比表——手机上第三档曾被挤出屏要横滑）；选中档白卡、
 *   未选档无卡底；线路（服）分段切换，有几组显示几组、一组不显示；
 *   套餐名与价格用我方数据；
 * - 结算：吸底结算栏常驻（已选套餐·时长＋总价＋彩虹主钮），明细行
 *   （续费周期/抵扣/余额）与优惠码在页内；点主钮先弹确认
 *   弹窗算明细账（余额/新套餐金额/旧套餐已抵扣/本需支付/购买后
 *   结余），确认才扣钱包余额——本站购买走钱包单轨（友站同形），
 *   网关下单口与充值链路不在本页；
 * - 换套餐抵扣：计价口 _price_subscription_purchase 按「旧订阅
 *   实付金额×剩余时长/总时长」真抵扣，试算口同口径下发
 *   switch_credit，前后端逐分一致；
 * - 子账号行：本站套餐没有子账号维度的数据源，整行如实显示「—」，
 *   不编造数字。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronLeft, Tag } from 'lucide-vue-next'
import UhdAccountShellGate from '@/components/uhd/UhdAccountShellGate.vue'
import UhdModal from '@/components/uhd/UhdModal.vue'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import {
  couponApi,
  paymentApi,
  type CouponQuote,
  type SubscriptionPlan,
} from '@/api/economy'
import { formatBytes } from '@/api/traffic'
import { walletApi, newWalletKey } from '@/api/wallet'
import { subscriptionApi, type MySubscription } from '@/api'

const toast = useToast()
const router = useRouter()

const loading = ref(true)
const loadError = ref(false)
const plans = ref<SubscriptionPlan[]>([])
const plansEnabled = ref(true)
const walletEnabled = ref(false)
const isFreeRealm = ref(false)
const realmNote = ref('')
const deviceLimit = ref(0)
const downloadAllowed = ref(true)
const walletBalance = ref(0)
const mySubs = ref<MySubscription[]>([])
const couponEnabled = ref(true)
const orderBusy = ref(false)

/* ---------- 展示工具 ---------- */
function fmtPrice(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '–'
  return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2)
}
function moneyText(v: number | null | undefined): string {
  if (v == null) return '–'
  return `¥ ${fmtPrice(v)}`
}
function round2(n: number): number {
  return Math.round(n * 100) / 100
}

/* ---------- 线路（服）与套餐选择 ---------- */
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
const realmPlans = computed(() => {
  const realm = selectedRealm.value || realmOptions.value[0]?.value || '默认'
  return [...plans.value]
    .filter((p) => (p.realm_name || '默认') === realm)
    .sort((a, b) => a.duration_days - b.duration_days || a.price - b.price)
})
const selectedPlanId = ref<number | null>(null)
const selectedPlan = computed(
  () => realmPlans.value.find((p) => p.id === selectedPlanId.value) ?? null,
)

/* ---------- 当前订阅 ---------- */
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

/* ---------- 时长：滑杆＋分段联动（1–12 个月） ---------- */
const MONTH_CAPS = [
  { months: 1, label: '1 个月' },
  { months: 3, label: '3 个月' },
  { months: 6, label: '6 个月' },
  { months: 12, label: '年付' },
]
const months = ref(1)
/** 切套餐时时长回到该套餐的基准月数（它的现售卖时长） */
watch(selectedPlanId, () => {
  const p = selectedPlan.value
  if (p) months.value = Math.min(12, Math.max(1, p.base_months ?? 1))
})
const sliderFill = computed(() => `${((months.value - 1) / 11) * 100}%`)
function onMonthsSlider(e: Event) {
  const v = Number((e.target as HTMLInputElement).value)
  if (Number.isFinite(v)) months.value = Math.min(12, Math.max(1, Math.round(v)))
}

/* ---------- 按月计价（月价后端下发，前端只做同口径展示） ---------- */
function monthlyOf(plan: SubscriptionPlan): number {
  if (plan.monthly_price != null) return plan.monthly_price
  const base = plan.base_months ?? Math.max(1, Math.round(plan.duration_days * 12 / 365))
  return round2(plan.price / base)
}
function listPriceFor(plan: SubscriptionPlan, m: number): number {
  const mo = monthlyOf(plan)
  return round2(m === 12 ? mo * 11 : mo * m)
}
function trafficText(plan: SubscriptionPlan): string {
  const b = plan.entitlements?.monthly_traffic_bytes
  return b ? `${formatBytes(b)} / 月` : '—'
}

/* ---------- 对比表（行口径照友站，值全部来自真实数据） ---------- */
const compareRows = computed(() => {
  const list = realmPlans.value
  return [
    { label: '流量', cells: list.map((p) => trafficText(p)) },
    {
      label: '登录设备',
      cells: list.map(() => (deviceLimit.value > 0 ? `${deviceLimit.value} 台` : '不限')),
    },
    // 子账号是团队维度的能力、没有套餐级数据源：如实占位不编造
    { label: '子账号', cells: list.map(() => '—') },
    { label: '下载', cells: list.map(() => (downloadAllowed.value ? '支持' : '不支持')) },
    { label: '线路', cells: list.map((p) => p.realm_name || '标准线路') },
  ]
})

/* ---------- 试算（与下单同一口径，带 months） ---------- */
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
      months: months.value,
    })
    if (token !== quoteToken) return
    quote.value = q
  } catch (err) {
    if (token !== quoteToken) return
    quote.value = null
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
      months: months.value,
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

watch([selectedPlanId, months], () => {
  couponError.value = ''
  void refreshQuote()
})

const listTotal = computed<number | null>(() => {
  const plan = selectedPlan.value
  if (!plan) return null
  if (quote.value) return quote.value.list_price
  return listPriceFor(plan, months.value)
})
const couponCut = computed(() => Math.max(0, quote.value?.discount_amount ?? 0))
const couponCutLabel = computed(() => {
  if (appliedCoupon.value) return `优惠券 ${appliedCoupon.value}`
  if (quote.value?.exchange_discount_pct) return '折扣权益'
  return '优惠'
})
const switchCredit = computed(() => Math.max(0, quote.value?.switch_credit ?? 0))
const switchType = computed(() => quote.value?.switch_type ?? (activeSub.value ? 'switch' : 'new'))
const payableTotal = computed<number | null>(() => {
  if (quote.value?.payable_amount != null) return quote.value.payable_amount
  if (quote.value) return quote.value.paid_amount
  return listTotal.value
})
const savedTotal = computed(() =>
  listTotal.value != null && payableTotal.value != null
    ? Math.max(0, round2(listTotal.value - payableTotal.value))
    : 0,
)
const cycleText = computed(() => {
  if (months.value === 1) return '按月续费'
  if (months.value === 12) return '按年续费'
  return `每 ${months.value} 个月续费`
})
const balanceShortfall = computed(() => {
  if (payableTotal.value == null) return 0
  return Math.max(0, round2(payableTotal.value - walletBalance.value))
})
const balanceAfter = computed(() =>
  payableTotal.value == null ? 0 : round2(walletBalance.value - payableTotal.value),
)
const switchTypeText = computed(() => {
  if (switchType.value === 'renew') return '续费'
  if (switchType.value === 'switch') return '升级套餐'
  return '新开通'
})
const ctaText = computed(() => {
  const plan = selectedPlan.value
  if (!plan || !activeSub.value) return '开通'
  if (isCurrent(plan)) return '续费'
  if (plan.name.includes('家庭')) return '升级至家庭版'
  return '升级'
})
const ctaDisabled = computed(
  () => orderBusy.value || !selectedPlan.value || !walletEnabled.value,
)

/* ---------- 确认弹窗与下单（确认才扣钱包余额） ---------- */
const confirmOpen = ref(false)
const confirmError = ref('')
/** 本次弹窗会话的幂等键：开弹窗时生成，确认/重试共用，防重复扣款 */
const confirmKey = ref('')

function openConfirm() {
  if (ctaDisabled.value) return
  confirmError.value = ''
  confirmKey.value = newWalletKey()
  void refreshQuote()
  confirmOpen.value = true
}

function goRecharge() {
  confirmOpen.value = false
  void router.push('/wallet')
}

async function confirmBuy() {
  const plan = selectedPlan.value
  if (!plan || orderBusy.value) return
  orderBusy.value = true
  confirmError.value = ''
  try {
    const res = await walletApi.paySubscription({
      plan_id: plan.id,
      months: months.value,
      coupon_code: appliedCoupon.value ? appliedCoupon.value : undefined,
      idempotency_key: confirmKey.value,
    })
    toast.success(
      res.replayed
        ? '这次购买已经生效过了，没有重复扣款'
        : `已开通「${res.plan_name}」，到期 ${res.end_date ? res.end_date.slice(0, 10) : ''}`,
    )
    confirmOpen.value = false
    await router.push('/game/account/subscription-records')
  } catch (err) {
    confirmError.value = humanizeError(err)
  } finally {
    orderBusy.value = false
  }
}

function goBack() {
  if (window.history.length > 1) router.back()
  else void router.push('/game/account/subscription-records')
}

/* ---------- 加载 ---------- */
async function loadAll() {
  if (import.meta.env.VITE_FIXTURE === '1') {
    // QA 慢加载：?slowload 时先停在中性加载态，便于拍加载首帧（仅夹具环境）
    if (new URLSearchParams(location.search).has('slowload')) {
      await new Promise((r) => setTimeout(r, 1800))
    }
    plans.value = [
      { id: 1, name: '夹具甲', description: null, price: 10, duration_days: 30, monthly_price: 10, base_months: 1, features: null, entitlements: { monthly_traffic_bytes: 500 * 1024 ** 3 }, is_popular: false, realm_name: '线路甲' },
      { id: 2, name: '夹具乙', description: null, price: 30, duration_days: 30, monthly_price: 30, base_months: 1, features: null, entitlements: { monthly_traffic_bytes: 1500 * 1024 ** 3 }, is_popular: false, realm_name: '线路甲' },
      { id: 3, name: '夹具丙', description: null, price: 20, duration_days: 30, monthly_price: 20, base_months: 1, features: null, entitlements: null, is_popular: false, realm_name: '线路乙' },
    ]
    plansEnabled.value = true
    walletEnabled.value = true
    couponEnabled.value = true
    deviceLimit.value = 10
    downloadAllowed.value = true
    realmNote.value = '夹具说明，不是生产套餐'
    walletBalance.value = 0
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = false
  const [pls, subs, cpCfg, wal] = await Promise.allSettled([
    paymentApi.plans(),
    subscriptionApi.getMine(),
    couponApi.config(),
    walletApi.get(),
  ])
  if (pls.status === 'fulfilled') {
    plans.value = pls.value.plans || []
    plansEnabled.value = pls.value.enabled !== false
    walletEnabled.value = pls.value.wallet_purchase_enabled === true
    isFreeRealm.value = pls.value.is_free === true || pls.value.access_mode === 'free'
    realmNote.value = pls.value.access_note || ''
    deviceLimit.value = pls.value.device_limit ?? 0
    downloadAllowed.value = pls.value.download_allowed !== false
  } else {
    loadError.value = true
  }
  if (wal.status === 'fulfilled') {
    walletBalance.value = Number(wal.value.balance) || 0
    if (wal.value.wallet_enabled === false) walletEnabled.value = false
  } else {
    walletEnabled.value = false
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
/* ============ 订阅服务页（2026-11-15 照友站录屏 f033–f042 形态）
   纵向卡片列表＋页内时长/优惠码＋吸底结算栏；字号按友站小尺度：
   页标题 19 / 档名 19 / 价格 29 / 规格标签 13·值 14 / 分段 14 /
   吸底栏名 12.5·总价 19·主钮 16。桌面同结构居中收窄，不另做一套。 ============ */
.sub-page {
  min-height: 100dvh;
  background: var(--uhd-l-bg);
  color: var(--uhd-l-text);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
  --sub-card-bg: #ffffff;
  --sub-seg-bg: #f1f1f4;
  --sub-track: #e6e6ea;
  --sub-fill: #c6c6cd;
  --sub-shadow: 0 10px 34px rgba(17, 17, 20, 0.08);
}
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
  color: var(--uhd-l-text);
  cursor: pointer;
}
.sub-back:active { background: rgba(17, 17, 20, 0.06); }
.sub-title {
  flex: 1 1 auto;
  margin: 0;
  font-size: 19px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  color: var(--uhd-l-text);
}
.sub-body {
  max-width: 760px;
  margin: 0 auto;
  padding: 12px 16px 132px;
}
.sub-layout {
  display: flex;
  flex-direction: column;
  gap: 22px;
  min-width: 0;
}

/* 加载态：中性转圈＋浅色骨架（跟随主题，禁止整块纯黑） */
.sub-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding-top: 26px;
}
.sub-spinner {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 3px solid var(--sub-track);
  border-top-color: var(--uhd-l-text-3);
  animation: sub-spin 0.9s linear infinite;
}
@keyframes sub-spin { to { transform: rotate(360deg); } }
.sub-loading__text {
  margin: 0;
  font-size: 13px;
  color: var(--uhd-l-text-2);
}
.sub-skeleton {
  width: 100%;
  height: 168px;
  border-radius: 20px;
  background: linear-gradient(100deg, #ececef 40%, #f4f4f6 50%, #ececef 60%);
  background-size: 200% 100%;
  animation: sub-shimmer 1.4s infinite;
}
.sub-skeleton--short { height: 96px; }
@keyframes sub-shimmer {
  from { background-position: 120% 0; }
  to { background-position: -80% 0; }
}
.sub-state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 24px 20px;
  border-radius: 20px;
  background: var(--sub-card-bg);
  border: 1px solid var(--uhd-l-border);
  color: var(--uhd-l-text-2);
  font-size: 14px;
}
.sub-state p { margin: 0; }
.sub-state__note { color: var(--uhd-l-text-3); font-size: 12.5px; }
.sub-retry {
  padding: 8px 16px;
  border-radius: 999px;
  border: 1px solid var(--uhd-l-border);
  background: transparent;
  color: var(--uhd-l-text);
  font-size: 13px;
  cursor: pointer;
}
.sub-note { margin: -12px 0 0; color: var(--uhd-l-text-2); font-size: 13px; line-height: 1.55; }

/* 线路分段（友站 f039：浅灰胶囊高约 40（含 3px 内边距）、段高 34、选中白底轻阴影） */
.sub-seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 999px;
  background: var(--sub-seg-bg);
  max-width: 100%;
  overflow-x: auto;
}
.sub-seg__item {
  flex: 1 1 0;
  border: none;
  background: transparent;
  color: var(--uhd-l-text-2);
  border-radius: 999px;
  height: 34px;
  min-height: 34px; /* 压过全站移动端 button{min-height:44px}，照友站胶囊高 34 */
  padding: 0 14px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}
.sub-seg__item--on {
  background: #ffffff;
  color: var(--uhd-l-text);
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(17, 17, 20, 0.10);
}

/* 套餐卡片列表（友站 f039：选中白卡圆角 20/内边距 17/轻阴影；未选无卡底） */
.plan-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.plan-card {
  border-radius: 20px;
  padding: 16px 17px 14px;
  cursor: pointer;
}
.plan-card--on {
  background: var(--sub-card-bg);
  box-shadow: var(--sub-shadow);
}
.plan-card__head {
  display: flex;
  align-items: flex-start;
  gap: 9px;
}
.plan-radio {
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  margin-top: 3px;
  border-radius: 50%;
  border: 2px solid #c9c9cf;
  background: transparent;
  position: relative;
}
.plan-radio--on { border-color: var(--uhd-l-text); background: var(--uhd-l-text); }
.plan-radio--on::after {
  content: '';
  position: absolute;
  inset: 4px;
  border-radius: 50%;
  background: #ffffff;
}
.plan-card__name {
  flex: 0 1 auto;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--uhd-l-text);
  line-height: 1.3;
}
.plan-current {
  flex: 0 0 auto;
  margin-top: 3px;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--uhd-rainbow, linear-gradient(100deg, #2f8fe0, #c46c90 55%, #f2a45a));
  color: #fff;
}
.plan-card__pricebox {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  min-width: 0;
}
.plan-card__price {
  font-size: 29px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.03em;
  line-height: 1.05;
  color: var(--uhd-l-text);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.plan-card__price small {
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--uhd-l-text-3);
}
.plan-card__annual {
  font-size: 12.5px;
  color: var(--uhd-l-text-3);
  font-variant-numeric: tabular-nums;
}
.plan-specs {
  margin-top: 13px;
  padding-top: 12px;
  border-top: 1px solid var(--uhd-l-divider);
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.plan-spec {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.plan-spec__label {
  flex: 0 0 64px;
  font-size: 13px;
  color: var(--uhd-l-text-2);
}
.plan-spec__value {
  font-size: 14px;
  font-weight: 500;
  color: var(--uhd-l-text);
}

/* 时长（友站 f036：页内平铺；滑杆浅灰轨＋白拇指黑描边，禁止黑轨） */
.sub-dur { padding: 2px 2px 0; }
.sub-dur__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}
.sub-dur__label { font-size: 14px; color: var(--uhd-l-text-2); }
.sub-dur__value { font-size: 14px; color: var(--uhd-l-text-2); }
.sub-dur__value b { font-size: 18px; color: var(--uhd-l-text); }
.sub-slider {
  width: 100%;
  appearance: none;
  -webkit-appearance: none;
  height: 22px; /* 命中区 22，轨道只画中间 6px（全局 input 内边距必须清零，否则轨道被撑厚） */
  padding: 0;
  margin: 0;
  border: none;
  border-radius: 999px;
  background: linear-gradient(to right, var(--sub-fill) var(--fill, 0%), var(--sub-track) var(--fill, 0%)) center / 100% 6px no-repeat;
  outline: none;
  cursor: pointer;
}
.sub-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #ffffff;
  border: 2px solid var(--uhd-l-text);
  box-shadow: 0 2px 8px rgba(17, 17, 20, 0.18);
}
.sub-slider::-moz-range-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #ffffff;
  border: 2px solid var(--uhd-l-text);
  box-shadow: 0 2px 8px rgba(17, 17, 20, 0.18);
}
.sub-dur__caps {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.sub-cap {
  flex: 1;
  height: 38px;
  min-height: 38px; /* 同上：压过全站 44px 触控下限，照友站分段高 */
  border-radius: 999px;
  border: 1px solid var(--uhd-l-border);
  background: #ffffff;
  color: var(--uhd-l-text-2);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}
.sub-cap--on {
  background: var(--uhd-l-text);
  border-color: var(--uhd-l-text);
  color: #ffffff;
  font-weight: 600;
}
.sub-dur__hint {
  margin: 10px 0 0;
  font-size: 12.5px;
  color: var(--uhd-l-text-3);
}

/* 优惠码（友站 f036：圆角输入框＋应用钮同行） */
.sub-coupon__row { display: flex; gap: 8px; }
.sub-coupon__box {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  height: 46px;
  border-radius: 14px;
  border: 1px solid var(--uhd-l-border);
  background: #ffffff;
  color: var(--uhd-l-text-3);
  min-width: 0;
}
.sub-coupon__input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--uhd-l-text);
  font-size: 14px;
  outline: none;
}
.sub-coupon__apply {
  flex: 0 0 auto;
  padding: 0 18px;
  border-radius: 14px;
  border: 1px solid var(--uhd-l-border);
  background: #ffffff;
  color: var(--uhd-l-text);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.sub-coupon__apply:disabled { opacity: 0.45; cursor: not-allowed; }
.sub-coupon__ok { margin: 8px 0 0; font-size: 12.5px; color: var(--uhd-l-text-2); }
.sub-coupon__err { margin: 8px 0 0; font-size: 12.5px; color: #d92d20; }

/* 结算明细（页内小字） */
.sub-summary {
  border-top: 1px solid var(--uhd-l-divider);
  padding-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.sum-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: var(--uhd-l-text-2);
}
.sum-row b { color: var(--uhd-l-text); font-weight: 600; }
.sum-row--cut b { color: #e07b39; }
.sum-note { margin: 0; font-size: 12.5px; color: #e07b39; }

/* 吸底结算栏（友站 f039/f036：白/半透底＋顶发丝线；左名·时长＋总价，右彩虹胶囊钮高 46） */
.sub-sticky {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 40;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
  border-top: 1px solid var(--uhd-l-divider);
  padding-bottom: env(safe-area-inset-bottom);
}
.sub-sticky__inner {
  max-width: 760px;
  margin: 0 auto;
  min-height: 68px;
  padding: 10px 16px;
  display: flex;
  align-items: center;
  gap: 14px;
}
.sub-sticky__left {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.sub-sticky__name {
  font-size: 12.5px;
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub-sticky__total {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 19px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-l-text);
  font-variant-numeric: tabular-nums;
}
.sub-sticky__total s { font-size: 12.5px; font-weight: 500; color: var(--uhd-l-text-3); }
.sub-sticky__cta {
  flex: 0 0 auto;
  height: 46px;
  padding: 0 34px;
  border-radius: 999px;
  border: 1.5px solid transparent;
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.12s ease, opacity 0.12s ease;
}
.sub-sticky__cta:active:not(:disabled) { transform: scale(0.98); }
.sub-sticky__cta:disabled { opacity: 0.45; cursor: not-allowed; }

/* 确认弹窗明细 */
.cfm { display: flex; flex-direction: column; gap: 10px; }
.cfm-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13.5px;
  color: var(--uhd-l-text-2);
}
.cfm-row b { color: var(--uhd-l-text); font-weight: 600; }
.cfm-row--cut b { color: #e07b39; }
.cfm-row--total {
  border-top: 1px solid rgba(128, 128, 140, 0.25);
  padding-top: 10px;
  font-size: 15px;
  font-weight: 700;
}
.cfm-neg { color: #d92d20 !important; }
.cfm-warn { margin: 2px 0 0; font-size: 12.5px; color: #d92d20; }
.cfm-btn {
  padding: 11px 22px;
  border-radius: 999px;
  font-size: 13.5px;
  font-weight: 700;
  cursor: pointer;
  border: 1px solid rgba(128, 128, 140, 0.35);
  background: transparent;
  color: var(--uhd-l-text);
}
.cfm-btn--main {
  border: none;
  color: #fff;
  background: var(--uhd-rainbow, linear-gradient(100deg, #2f8fe0, #c46c90 55%, #f2a45a));
}
.cfm-btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* 深色主题：同一套结构换深色板，骨架/滑杆/吸底栏同步跟随，禁止纯黑块 */
html[data-theme='dark'] .sub-page {
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  --sub-card-bg: #17171a;
  --sub-seg-bg: #1d1d20;
  --sub-track: rgba(255, 255, 255, 0.16);
  --sub-fill: rgba(255, 255, 255, 0.38);
  --sub-shadow: none;
  --uhd-l-text: #ffffff;
  --uhd-l-text-2: #a7a7ad;
  --uhd-l-text-3: #8e8e96;
  --uhd-l-border: #2a2a2e;
  --uhd-l-divider: #26262a;
  --uhd-l-bg: #050505;
}
html[data-theme='dark'] .sub-skeleton {
  background: linear-gradient(100deg, #17171a 40%, #1f1f23 50%, #17171a 60%);
  background-size: 200% 100%;
}
html[data-theme='dark'] .sub-seg__item--on { background: #2e2e33; color: #ffffff; }
html[data-theme='dark'] .sub-cap,
html[data-theme='dark'] .sub-coupon__box,
html[data-theme='dark'] .sub-coupon__apply { background: #17171a; }
html[data-theme='dark'] .sub-cap--on { background: #ffffff; border-color: #ffffff; color: #111111; }
html[data-theme='dark'] .plan-radio--on::after { background: #111111; }
html[data-theme='dark'] .sub-sticky { background: rgba(13, 13, 15, 0.92); }
</style>
