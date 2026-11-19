<template>
  <div v-if="open" class="tp-mask" @click.self="onMaskClick">
    <div
      ref="sheetRef"
      class="tp-sheet"
      role="dialog"
      aria-modal="true"
      aria-label="账户充值"
      @focusin="onSheetFocusIn"
      @focusout="onSheetFocusOut"
    >
      <div class="tp-head">
        <h3 class="tp-title">账户充值</h3>
        <button type="button" class="tp-close" aria-label="关闭" @click="close">
          <X :size="20" />
        </button>
      </div>
      <p class="tp-sub">充值前请确认金额，充值后不支持退款。</p>

      <!-- 未开通态：诚实提示，不假接支付 -->
      <template v-if="!info?.enabled">
        <p class="tp-note tp-note--lead">在线充值暂未开通，可以先通过兑换码给余额入账。</p>
        <button type="button" class="btn-primary tp-confirm" @click="close">知道了</button>
      </template>

      <template v-else>
        <!-- 余额行（钱包余额由钱包页透传） -->
        <p class="tp-balance">余额 <b>${{ balance }}</b></p>

        <!-- 金额档位：点了把金额填进大输入框；「自定义」只聚焦输入框 -->
        <div class="tp-tiers" role="radiogroup" aria-label="充值金额">
          <button
            v-for="t in tiers"
            :key="t"
            type="button"
            class="tp-tier"
            :class="{ 'tp-tier--on': activeTier === t }"
            role="radio"
            :aria-checked="activeTier === t"
            @click="pickTier(t)"
          >${{ t }}</button>
          <button
            type="button"
            class="tp-tier"
            :class="{ 'tp-tier--on': activeTier === 'custom' }"
            role="radio"
            :aria-checked="activeTier === 'custom'"
            @click="focusCustom"
          >自定义</button>
        </div>

        <!-- 常驻大输入框：金额唯一来源（限额全按 info 下发） -->
        <div class="tp-amount-row">
          <span class="tp-amount-sign" aria-hidden="true">$</span>
          <input
            ref="amountInputRef"
            v-model="amountText"
            class="tp-amount"
            type="text"
            inputmode="decimal"
            placeholder="1.00"
            autocomplete="off"
            :disabled="submitting"
            aria-label="充值金额（美元）"
            @input="onAmountInput"
          />
        </div>

        <!-- 提示行三态：默认最低额 / 超限红字 / 有效金额折算行 -->
        <p v-if="hintState === 'over'" class="tp-hint tp-hint--over" role="alert">
          法币单笔充值不能超过 ${{ info.max_usd_epay }}
        </p>
        <p v-else-if="hintState === 'valid'" class="tp-hint">
          ≈ ¥{{ payableText }} · 含 {{ info.fee_rate }}% 手续费 · 汇率 {{ info.usd2cny }}
        </p>
        <p v-else class="tp-hint">最低充值 ${{ info.min_usd }}</p>

        <!-- 支付方式（按服务端下发列表渲染）：单选行＝品牌徽标 +
             名称 + radio 圆圈，整行可点；本期只有法币方式，
             数字货币不做、不留假入口 -->
        <span class="tp-label">支付方式</span>
        <div class="tp-methods" role="radiogroup" aria-label="支付方式">
          <button
            v-for="m in info.methods"
            :key="m"
            type="button"
            class="tp-method"
            :class="{ 'tp-method--on': method === m }"
            role="radio"
            :aria-checked="method === m"
            @click="method = m"
          >
            <span class="tp-method__badge" :class="`tp-method__badge--${m}`" aria-hidden="true">{{ methodBadge(m) }}</span>
            <span class="tp-method__name">{{ methodLabel(m) }}</span>
            <span class="tp-method__radio" aria-hidden="true"></span>
          </button>
        </div>

        <p v-if="formError" class="tp-err" role="alert">{{ formError }}</p>
        <button
          type="button"
          class="btn-primary tp-confirm"
          :disabled="!canSubmit"
          @click="submit"
        >
          <span v-if="submitting" class="tp-spinner" aria-hidden="true"></span>
          {{ submitting ? '正在跳转…' : confirmText }}
        </button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * TopupSheet —— 在线充值弹窗（钱包批 3，形态照友站 recharge-dialog
 * 实测口径）：标题「账户充值」+ 退款提示副文；余额行 + 常驻大金额
 * 输入框（金额唯一来源），档位 $10/$30/$50/$70 点了把金额填进输入
 * 框、「自定义」只聚焦输入框；提示行三态（最低额 / 超限红字 /
 * ≈¥折算行）；支付方式按服务端 info 下发列表渲染成单选行（本期
 * 只有法币方式，数字货币不做、不留假入口）；确认钮文案「确认支付
 * ¥X.XX」。金额校验、费率、汇率、限额全读 info（钱包信息接口的
 * topup 块），前端不写死；应付预览与后端同一公式（美元 × 生效
 * 汇率 × (1+费率%)，到分取整），入账以后端订单为准。下单成功整页
 * 跳转 pay_url（后端暂无充值订单状态查询接口，不做等待屏轮询）。
 */
import { computed, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { walletApi, type WalletTopupInfo } from '@/api/wallet'
import '../../styles/traffic-view.css'
import { toUserMessage } from '@/utils/toUserMessage'

const props = defineProps<{
  open: boolean
  info: WalletTopupInfo | null
  /** 当前钱包余额（字符串，两位小数，钱包页透传） */
  balance: string
}>()

const emit = defineEmits<{
  close: []
}>()

const tiers = [10, 30, 50, 70]
const amountText = ref('')
const amountInputRef = ref<HTMLInputElement | null>(null)
const method = ref('alipay')
const formError = ref('')
const submitting = ref(false)
const sheetRef = ref<HTMLElement | null>(null)
/** 焦点是否落在弹窗内的输入框上（iOS 遮罩误关守卫用，见 onMaskClick） */
const inputFocused = ref(false)

/** 当前金额（美分）；输入非法（空/非数字/超两位小数）时为 null */
const usdFen = computed<number | null>(() => {
  const raw = amountText.value.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) return null
  const v = Number(raw)
  if (!Number.isFinite(v) || v <= 0) return null
  return Math.round(v * 100)
})

/** 档位高亮：输入金额正好等于某档时亮该档，否则亮「自定义」 */
const activeTier = computed<number | 'custom'>(() => {
  if (usdFen.value !== null && tiers.includes(usdFen.value / 100)) return usdFen.value / 100
  return 'custom'
})

type HintState = 'default' | 'over' | 'valid'
const hintState = computed<HintState>(() => {
  const info = props.info
  if (!info || usdFen.value === null) return 'default'
  if (info.max_usd_fen > 0 && usdFen.value > info.max_usd_fen) return 'over'
  if (usdFen.value >= info.min_usd_fen && info.usd2cny) return 'valid'
  return 'default'
})

/** 应付人民币预览：与后端 compute_topup 同一公式（一次取整到分） */
const payableText = computed<string>(() => {
  const info = props.info
  if (!info?.usd2cny || usdFen.value === null) return ''
  const rate = Number(info.usd2cny)
  if (!(rate > 0)) return ''
  const cnyFen = Math.round((usdFen.value * rate * (100 + info.fee_rate)) / 100)
  return (cnyFen / 100).toFixed(2)
})

const canSubmit = computed(() =>
  !!props.info && usdFen.value !== null && hintState.value !== 'over'
  && usdFen.value >= props.info.min_usd_fen && !submitting.value)

/** 确认钮文案：金额有效时带应付人民币预览，无效时只留「确认支付」 */
const confirmText = computed(() =>
  canSubmit.value && payableText.value ? `确认支付 ¥${payableText.value}` : '确认支付')

watch(
  () => props.open,
  (open) => {
    if (open) {
      amountText.value = ''
      formError.value = ''
      inputFocused.value = false
      const methods = props.info?.methods ?? []
      method.value = methods.includes('alipay') ? 'alipay' : (methods[0] ?? 'alipay')
    }
  },
)

function methodLabel(m: string): string {
  return { alipay: '支付宝', wxpay: '微信支付', qqpay: 'QQ 钱包' }[m] ?? m
}

/** 品牌徽标字（仓内无品牌图标资源，用首字徽标与站内文字片口径一致） */
function methodBadge(m: string): string {
  return { alipay: '支', wxpay: '微', qqpay: 'Q' }[m] ?? m.slice(0, 1).toUpperCase()
}

/** 点档位：把金额填进大输入框 */
function pickTier(t: number) {
  amountText.value = String(t)
}

/** 点「自定义」：只聚焦大输入框（内容由用户自行改/清空） */
function focusCustom() {
  amountInputRef.value?.focus()
}

function close() {
  emit('close')
}

/** 金额输入清洗（支付输入口径）：只留数字与最多一个小数点、
 * 小数至多 2 位；usdFen 的正则校验仍作兜底。 */
function onAmountInput(e: Event) {
  const el = e.target as HTMLInputElement
  let v = el.value.replace(/[^\d.]/g, '')
  const dot = v.indexOf('.')
  if (dot >= 0) {
    v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, 2)
  }
  amountText.value = v
  if (el.value !== v) el.value = v
}

function onSheetFocusIn(e: FocusEvent) {
  if ((e.target as HTMLElement | null)?.tagName === 'INPUT') inputFocused.value = true
}

function onSheetFocusOut(e: FocusEvent) {
  const next = e.relatedTarget as HTMLElement | null
  inputFocused.value =
    !!next && next.tagName === 'INPUT' && (sheetRef.value?.contains(next) ?? false)
}

/** 遮罩点击关闭加聚焦守卫：iOS Safari 在 fixed 浮层里聚焦输入框时
 * 点位会错位，点击可能被打到遮罩上误关弹窗（关后键盘残留还会吞掉
 * 下一次点击）；焦点还在输入框里时遮罩点击不关，X 钮照常关。 */
function onMaskClick() {
  if (inputFocused.value) return
  close()
}

async function submit() {
  if (!canSubmit.value || usdFen.value === null) return
  formError.value = ''
  submitting.value = true
  try {
    const res = await walletApi.createTopupOrder({
      amount_usd: (usdFen.value / 100).toFixed(2),
      payment_method: method.value,
    })
    window.location.href = res.pay_url
  } catch (e) {
    formError.value = toUserMessage(e)
    submitting.value = false
  }
}
</script>

<style scoped>
/* 居中实色浮窗：与 GiftCodeSheet 同一形态/令牌（2026-11-18 弹窗实色令）。 */
.tp-mask {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: rgba(10, 10, 14, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: tp-fade-in 0.22s ease both;
}
@keyframes tp-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.tp-sheet {
  width: 92%;
  max-width: 420px;
  max-height: 88vh;
  overflow-y: auto;
  background: var(--uhd-d-surface-raised);
  border: 1px solid var(--uhd-glass-border);
  border-radius: 26px;
  padding: 22px 22px 24px;
  color: var(--tq-fg);
  box-shadow: var(--uhd-shadow-modal);
  animation: tp-pop-in 0.28s cubic-bezier(0.32, 0.72, 0.28, 1) both;
}
html[data-theme='light'] .tp-sheet {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-glass-border-l);
}
@keyframes tp-pop-in {
  from { transform: translateY(12px) scale(0.97); opacity: 0; }
  to { transform: translateY(0) scale(1); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .tp-mask, .tp-sheet { animation: none; }
  .tp-spinner { animation-duration: 1.6s; }
}

.tp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tp-title { margin: 0; font-size: 18px; font-weight: 750; }
.tp-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--tq-border);
  background: transparent;
  color: var(--tq-dim);
  cursor: pointer;
}
.tp-sub { margin: 8px 0 0; font-size: 13px; line-height: 1.6; color: var(--tq-dim); }

.tp-balance { margin: 16px 0 0; font-size: 13px; color: var(--tq-dim); }
.tp-balance b { color: var(--tq-fg); font-weight: 700; }

.tp-tiers { display: flex; gap: 8px; margin-top: 12px; }
.tp-tier {
  flex: 1;
  padding: 11px 0;
  border-radius: 999px;
  border: 1px solid var(--tq-border);
  background: transparent;
  color: var(--tq-dim);
  font-family: inherit;
  font-size: 14.5px;
  font-weight: 650;
  cursor: pointer;
  white-space: nowrap;
}
/* 选中片＝微亮玻璃（与礼品卡弹层同口径，不用实心反色块） */
.tp-tier--on {
  background: var(--tq-seg-on);
  border-color: var(--tq-border);
  color: inherit;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

.tp-amount-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 60px;
  padding: 0 18px;
  margin-top: 12px;
  border-radius: 14px;
  background: var(--tq-card);
  border: 1px solid var(--tq-border);
}
.tp-amount-sign { font-size: 22px; font-weight: 750; color: var(--tq-dim); }
.tp-amount {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 24px;
  font-weight: 700;
  outline: none;
}
.tp-amount::placeholder { color: var(--tq-dim); }

.tp-hint { margin: 12px 0 0; font-size: 13px; color: var(--tq-dim); }
.tp-hint--over { color: #f87171; }
html[data-theme='light'] .tp-hint--over { color: #dc2626; }

.tp-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--tq-dim);
  margin: 16px 0 8px;
}
/* 支付方式单选行：品牌首字徽标 + 名称 + radio 圆圈，整行可点 */
.tp-methods { display: flex; flex-direction: column; gap: 8px; }
.tp-method {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 11px 14px;
  border-radius: 14px;
  border: 1px solid var(--tq-border);
  background: var(--tq-card);
  color: inherit;
  font-family: inherit;
  font-size: 14.5px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}
.tp-method--on { background: var(--tq-seg-on); }
.tp-method__badge {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #8a8f98;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}
.tp-method__badge--alipay { background: #1677ff; }
.tp-method__badge--wxpay { background: #07c160; }
.tp-method__badge--qqpay { background: #12b7f5; }
.tp-method__name { flex: 1; min-width: 0; }
.tp-method__radio {
  position: relative;
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1.5px solid var(--tq-dim);
}
.tp-method__radio::after {
  content: "";
  position: absolute;
  inset: 3px;
  border-radius: 50%;
}
.tp-method--on .tp-method__radio { border-color: var(--tq-fg); }
.tp-method--on .tp-method__radio::after { background: var(--tq-fg); }

.tp-err { margin: 12px 0 0; font-size: 13px; color: #f87171; }
html[data-theme='light'] .tp-err { color: #dc2626; }
.tp-confirm {
  width: 100%;
  height: 50px;
  margin-top: 20px;
  border-radius: 25px;
  font-size: 15.5px;
  font-weight: 750;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.tp-spinner {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.45);
  border-top-color: #ffffff;
  animation: tp-spin 0.7s linear infinite;
  flex: none;
}
@keyframes tp-spin {
  to { transform: rotate(360deg); }
}
.tp-note { margin: 12px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--tq-dim); }
.tp-note--lead { margin-top: 14px; font-size: 13.5px; }
</style>
