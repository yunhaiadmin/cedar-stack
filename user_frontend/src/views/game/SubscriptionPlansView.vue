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
      <div v-if="loading" class="sub-skeleton" aria-hidden="true"></div>

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
        <div class="sub-main">
          <!-- 线路（服）分段切换：有几条显示几条，数据驱动 -->
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

          <!-- 套餐对比表：行＝价格/流量/登录设备/子账号/下载/线路，
               列＝套餐，当前列高亮＋实心单选点（照友站形态） -->
          <section class="cmp" aria-label="选择套餐">
            <table class="cmp-table">
              <thead>
                <tr>
                  <th class="cmp-corner" scope="col"><span class="cmp-sr">套餐</span></th>
                  <th
                    v-for="plan in realmPlans"
                    :key="plan.id"
                    scope="col"
                    class="cmp-colhead"
                    :class="{ 'cmp-colhead--on': plan.id === selectedPlanId }"
                    @click="selectedPlanId = plan.id"
                  >
                    <span class="cmp-plan">
                      <span class="plan-radio" :class="{ 'plan-radio--on': plan.id === selectedPlanId }" aria-hidden="true"></span>
                      <span class="cmp-plan__name">{{ plan.name }}</span>
                      <span v-if="isCurrent(plan)" class="plan-current">当前</span>
                    </span>
                    <span class="cmp-price">¥ {{ fmtPrice(monthlyOf(plan)) }}</span>
                    <span class="cmp-price__sub">/ 月 · 年付 ¥ {{ fmtPrice(listPriceFor(plan, 12)) }}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in compareRows" :key="row.label">
                  <th scope="row" class="cmp-rowhead">{{ row.label }}</th>
                  <td
                    v-for="(cell, i) in row.cells"
                    :key="realmPlans[i].id"
                    class="cmp-cell"
                    :class="{ 'cmp-cell--on': realmPlans[i].id === selectedPlanId }"
                    @click="selectedPlanId = realmPlans[i].id"
                  >{{ cell }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <!-- 订阅时长：滑杆 1–12 个月＋分段按钮双控件联动；
               3/6 个月无折扣，仅年付＝11×月价（照友站口径） -->
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
        </div>

        <!-- 右侧结算栏（桌面）：套餐·时长、总价＋约合、续费周期、
             优惠码、渐变主钮；手机端在主列下方整块呈现，无吸底条 -->
        <aside v-if="selectedPlan" class="sub-rail" aria-label="结算">
          <div class="rail-card">
            <div class="rail-row rail-row--head">
              <span>{{ selectedPlan.name }} · {{ months }} 个月</span>
            </div>
            <div class="rail-total">
              <b>{{ moneyText(payableTotal) }}</b>
              <s v-if="savedTotal > 0">{{ moneyText(listTotal) }}</s>
            </div>
            <p class="rail-approx">约合人民币 {{ moneyText(payableTotal) }}</p>
            <div class="rail-row">
              <span>续费周期</span>
              <b>{{ cycleText }}</b>
            </div>
            <div v-if="couponCut > 0" class="rail-row rail-row--cut">
              <span>{{ couponCutLabel }}</span>
              <b>−{{ moneyText(couponCut) }}</b>
            </div>
            <div v-if="switchCredit > 0" class="rail-row rail-row--cut">
              <span>旧套餐已抵扣</span>
              <b>−{{ moneyText(switchCredit) }}</b>
            </div>
            <div class="rail-row">
              <span>钱包余额</span>
              <b>{{ moneyText(walletBalance) }}</b>
            </div>

            <!-- 优惠码：接现有 coupon 试算口（与下单同一套口径） -->
            <div v-if="couponEnabled" class="sub-coupon">
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
            </div>

            <button
              type="button"
              class="rail-cta"
              :disabled="ctaDisabled"
              @click="openConfirm"
            >{{ ctaText }}</button>
            <p v-if="!walletEnabled" class="rail-note">钱包暂未开启，暂时无法购买</p>
            <p v-else-if="balanceShortfall > 0" class="rail-note" role="alert">
              钱包余额不足，还差 ¥ {{ fmtPrice(balanceShortfall) }}，充值后即可购买
            </p>
          </div>
        </aside>
      </div>
    </main>

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
 * - 套餐呈现：对比表（行＝价格/流量/登录设备/子账号/下载/线路）
 *   ＋线路（服）分段切换，有几条显示几条；套餐名与价格用我方数据；
 * - 结算：右侧结算栏（套餐·时长、大总价＋约合人民币、续费周期、
 *   优惠码、渐变主钮按状态变文案），无吸底条；点主钮先弹确认
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
/* ============ 订阅服务页（照友站形态：对比表＋右栏结算） ============ */
.sub-page {
  min-height: 100dvh;
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.sub-head {
  max-width: 1120px;
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
.sub-back:active { background: rgba(255, 255, 255, 0.08); }
.sub-title {
  flex: 1 1 auto;
  margin: 0;
  font-size: 20px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  color: var(--uhd-d-text);
}
.sub-body {
  max-width: 1120px;
  margin: 0 auto;
  padding: 14px 16px 64px;
}
.sub-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 26px;
  align-items: start;
}
@media (min-width: 1024px) {
  .sub-layout { grid-template-columns: minmax(0, 1fr) 330px; }
  .sub-rail { position: sticky; top: 24px; }
}
.sub-main {
  display: flex;
  flex-direction: column;
  gap: 26px;
  min-width: 0;
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
.sub-state p { margin: 0; }
.sub-state__note { color: var(--uhd-d-text-3); font-size: var(--uhd-fs-caption); }
.sub-retry {
  padding: 9px 18px;
  border-radius: 999px;
  border: 1px solid var(--uhd-d-border);
  background: transparent;
  color: var(--uhd-d-text);
  font-size: var(--uhd-fs-caption);
  cursor: pointer;
}
.sub-note { margin: 0; color: var(--uhd-d-text-3); font-size: var(--uhd-fs-caption); }

/* 线路分段 */
.sub-seg {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border-radius: 999px;
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  align-self: flex-start;
  max-width: 100%;
  overflow-x: auto;
}
.sub-seg__item {
  border: none;
  background: transparent;
  color: var(--uhd-d-text-2);
  border-radius: 999px;
  padding: 7px 16px;
  font-size: var(--uhd-fs-caption);
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.sub-seg__item--on {
  background: var(--uhd-d-text);
  color: var(--uhd-d-bg);
}

/* 对比表 */
.cmp {
  border-radius: var(--uhd-r-lg);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  overflow-x: auto;
}
.cmp-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  min-width: 430px;
}
.cmp-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
.cmp-corner { width: 76px; }
.cmp-colhead {
  padding: 16px 10px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--uhd-d-border);
  vertical-align: top;
  text-align: center;
}
.cmp-colhead--on,
.cmp-cell--on {
  background: rgba(255, 255, 255, 0.06);
}
.cmp-plan {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cmp-price {
  display: block;
  margin-top: 8px;
  font-size: 36px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.03em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.cmp-price__sub {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  color: var(--uhd-d-text-3);
}
.cmp-plan__name {
  font-size: var(--uhd-fs-title);
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-d-text);
}
.plan-radio {
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--uhd-d-text-3);
  background: transparent;
  position: relative;
}
.plan-radio--on { border-color: var(--uhd-d-text); }
.plan-radio--on::after {
  content: '';
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: var(--uhd-d-text);
}
.plan-current {
  font-size: var(--uhd-fs-micro);
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--uhd-rainbow, linear-gradient(100deg, #2f8fe0, #c46c90 55%, #f2a45a));
  color: #fff;
}
.cmp-rowhead {
  padding: 11px 12px;
  text-align: left;
  font-size: var(--uhd-fs-aux);
  font-weight: 500;
  color: var(--uhd-d-text-3);
  white-space: nowrap;
}
.cmp-cell {
  padding: 11px 10px;
  text-align: center;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
  cursor: pointer;
}
.cmp-table tbody tr + tr .cmp-cell,
.cmp-table tbody tr + tr .cmp-rowhead { border-top: 1px solid var(--uhd-d-border); }
.cmp-colhead--on, .cmp-cell--on {
  background: rgba(255, 255, 255, 0.07);
  color: var(--uhd-d-text);
}

/* 时长 */
.sub-dur {
  border-radius: var(--uhd-r-lg);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  padding: 18px;
}
.sub-dur__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}
.sub-dur__label { font-size: var(--uhd-fs-caption); color: var(--uhd-d-text-2); }
.sub-dur__value { font-size: var(--uhd-fs-caption); color: var(--uhd-d-text-2); }
.sub-dur__value b { font-size: var(--uhd-fs-h3); color: var(--uhd-d-text); }
.sub-slider {
  width: 100%;
  appearance: none;
  -webkit-appearance: none;
  height: 6px;
  border-radius: 999px;
  background: linear-gradient(to right, var(--uhd-d-text) var(--fill, 0%), rgba(255, 255, 255, 0.14) var(--fill, 0%));
  outline: none;
  cursor: pointer;
}
.sub-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  border: none;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.45);
}
.sub-slider::-moz-range-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  border: none;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.45);
}
.sub-dur__caps {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.sub-cap {
  flex: 1;
  padding: 9px 0;
  border-radius: 999px;
  border: 1px solid var(--uhd-d-border);
  background: transparent;
  color: var(--uhd-d-text-2);
  font-size: var(--uhd-fs-caption);
  font-weight: 600;
  cursor: pointer;
}
.sub-cap--on {
  background: var(--uhd-d-text);
  border-color: var(--uhd-d-text);
  color: var(--uhd-d-bg);
}
.sub-dur__hint {
  margin: 12px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-d-text-3);
}

/* 结算栏 */
.rail-card {
  border-radius: var(--uhd-r-lg);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  padding: 20px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rail-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
}
.rail-row b { color: var(--uhd-d-text); font-weight: 600; }
.rail-row--head { font-size: var(--uhd-fs-lead); font-weight: 700; color: var(--uhd-d-text); }
.rail-row--cut b { color: #f2a45a; }
.rail-total {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.rail-total b {
  font-size: 30px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-d-text);
}
.rail-total s { color: var(--uhd-d-text-3); font-size: var(--uhd-fs-caption); }
.rail-approx { margin: -6px 0 2px; font-size: var(--uhd-fs-aux); color: var(--uhd-d-text-3); }
.rail-cta {
  margin-top: 8px;
  width: 100%;
  padding: 14px 0;
  border: none;
  border-radius: 999px;
  font-size: var(--uhd-fs-lead);
  font-weight: 700;
  color: #fff;
  cursor: pointer;
  background: var(--uhd-rainbow, linear-gradient(100deg, #2f8fe0, #c46c90 55%, #f2a45a));
  transition: transform 0.12s ease, opacity 0.12s ease;
}
.rail-cta:active:not(:disabled) { transform: scale(0.98); }
.rail-cta:disabled { opacity: 0.45; cursor: not-allowed; }
.rail-note { margin: 0; font-size: var(--uhd-fs-aux); color: #f2a45a; }

/* 优惠码（栏内） */
.sub-coupon { margin-top: 4px; }
.sub-coupon__row { display: flex; gap: 8px; }
.sub-coupon__box {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border-radius: 12px;
  border: 1px solid var(--uhd-d-border);
  color: var(--uhd-d-text-3);
  min-width: 0;
}
.sub-coupon__input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--uhd-d-text);
  font-size: var(--uhd-fs-caption);
  padding: 10px 0;
  outline: none;
}
.sub-coupon__apply {
  flex: 0 0 auto;
  padding: 0 16px;
  border-radius: 12px;
  border: 1px solid var(--uhd-d-border);
  background: transparent;
  color: var(--uhd-d-text);
  font-size: var(--uhd-fs-caption);
  font-weight: 600;
  cursor: pointer;
}
.sub-coupon__apply:disabled { opacity: 0.45; cursor: not-allowed; }
.sub-coupon__ok { margin: 8px 0 0; font-size: var(--uhd-fs-aux); color: var(--uhd-d-text-2); }
.sub-coupon__err { margin: 8px 0 0; font-size: var(--uhd-fs-aux); color: #ff7a7a; }

/* 确认弹窗明细 */
.cfm { display: flex; flex-direction: column; gap: 10px; }
.cfm-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.cfm-row b { color: var(--uhd-l-text); font-weight: 600; }
.cfm-row--cut b { color: #e07b39; }
.cfm-row--total {
  border-top: 1px solid rgba(128, 128, 140, 0.25);
  padding-top: 10px;
  font-size: var(--uhd-fs-lead);
  font-weight: 700;
}
.cfm-neg { color: #d92d20 !important; }
.cfm-warn { margin: 2px 0 0; font-size: var(--uhd-fs-aux); color: #d92d20; }
.cfm-btn {
  padding: 11px 22px;
  border-radius: 999px;
  font-size: var(--uhd-fs-caption);
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
html[data-theme='light'] .sub-page {
  --uhd-d-bg: #f5f5f7;
  --uhd-d-text: #111111;
  --uhd-d-text-2: #3a3a3c;
  --uhd-d-text-3: #8e8e93;
  --uhd-d-surface: #ffffff;
  --uhd-d-border: #e6e6ea;
}
html[data-theme='light'] .cmp-colhead--on,
html[data-theme='light'] .cmp-cell--on {
  background: #ffffff;
}
</style>
