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
        <!-- 金额档位：$10 / $30 / $50 / $70 / 自定义（单选一排） -->
        <div class="tp-tiers" role="radiogroup" aria-label="充值金额">
          <button
            v-for="t in tiers"
            :key="t"
            type="button"
            class="tp-tier"
            :class="{ 'tp-tier--on': tier === t }"
            role="radio"
            :aria-checked="tier === t"
            @click="tier = t"
          >${{ t }}</button>
          <button
            type="button"
            class="tp-tier"
            :class="{ 'tp-tier--on': tier === 'custom' }"
            role="radio"
            :aria-checked="tier === 'custom'"
            @click="tier = 'custom'"
          >自定义</button>
        </div>

        <!-- 自定义金额：大号 $ 输入框（限额全按 info 下发） -->
        <div v-if="tier === 'custom'" class="tp-amount-row">
          <span class="tp-amount-sign" aria-hidden="true">$</span>
          <input
            v-model="customText"
            class="tp-amount"
            type="text"
            inputmode="decimal"
            placeholder="1.00"
            autocomplete="off"
            :disabled="submitting"
            aria-label="自定义金额（美元）"
            @input="onCustomInput"
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

        <!-- 支付通道：本期只有法币支付（数字货币不留假入口） -->
        <div class="tp-seg" role="group" aria-label="支付通道">
          <span class="tp-seg-on">法币支付</span>
        </div>

        <!-- 支付方式（按服务端下发列表渲染） -->
        <span class="tp-label">支付方式</span>
        <div class="tp-chips" role="group" aria-label="支付方式">
          <button
            v-for="m in info.methods"
            :key="m"
            type="button"
            class="tp-chip"
            :class="{ 'tp-chip--on': method === m }"
            :aria-pressed="method === m"
            @click="method = m"
          >{{ methodLabel(m) }}</button>
        </div>

        <p v-if="formError" class="tp-err" role="alert">{{ formError }}</p>
        <button
          type="button"
          class="btn-primary tp-confirm"
          :disabled="!canSubmit"
          @click="submit"
        >{{ submitting ? '正在跳转…' : '确认充值' }}</button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * TopupSheet —— 在线充值弹窗（钱包批 3，形态照友站 recharge-dialog
 * 实测口径）：标题「账户充值」+ 退款提示副文；金额档位 $10/$30/$50/
 * $70/自定义 单选；提示行三态（最低额 / 超限红字 / ≈¥折算行）；
 * 支付通道只有法币（数字货币不做、不留假入口）；支付方式按服务端
 * info 下发列表渲染。金额校验、费率、汇率、限额全读 info（钱包信息
 * 接口的 topup 块），前端不写死；应付预览与后端同一公式
 * （美元 × 生效汇率 × (1+费率%)，到分取整），入账以后端订单为准。
 */
import { computed, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { walletApi, type WalletTopupInfo } from '@/api/wallet'
import '../../styles/traffic-view.css'
import { toUserMessage } from '@/utils/toUserMessage'

const props = defineProps<{
  open: boolean
  info: WalletTopupInfo | null
}>()

const emit = defineEmits<{
  close: []
}>()

const tiers = [10, 30, 50, 70]
const tier = ref<number | 'custom'>(10)
const customText = ref('')
const method = ref('alipay')
const formError = ref('')
const submitting = ref(false)
const sheetRef = ref<HTMLElement | null>(null)
/** 焦点是否落在弹窗内的输入框上（iOS 遮罩误关守卫用，见 onMaskClick） */
const inputFocused = ref(false)

/** 当前选定金额（美分）；自定义输入非法（空/非数字/超两位小数）时为 null */
const usdFen = computed<number | null>(() => {
  if (tier.value !== 'custom') return tier.value * 100
  const raw = customText.value.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) return null
  const v = Number(raw)
  if (!Number.isFinite(v) || v <= 0) return null
  return Math.round(v * 100)
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

watch(
  () => props.open,
  (open) => {
    if (open) {
      tier.value = 10
      customText.value = ''
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

function close() {
  emit('close')
}

/** 自定义金额输入清洗（支付输入口径）：只留数字与最多一个小数点、
 * 小数至多 2 位；usdFen 的正则校验仍作兜底。 */
function onCustomInput(e: Event) {
  const el = e.target as HTMLInputElement
  let v = el.value.replace(/[^\d.]/g, '')
  const dot = v.indexOf('.')
  if (dot >= 0) {
    v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, 2)
  }
  customText.value = v
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

.tp-tiers { display: flex; gap: 8px; margin-top: 18px; }
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

.tp-seg {
  display: flex;
  margin-top: 18px;
  padding: 4px;
  border-radius: 999px;
  background: var(--tq-card);
  border: 1px solid var(--tq-border);
}
.tp-seg-on {
  flex: 1;
  text-align: center;
  padding: 9px 0;
  border-radius: 999px;
  background: var(--tq-seg-on);
  font-size: 14px;
  font-weight: 650;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

.tp-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--tq-dim);
  margin: 16px 0 8px;
}
.tp-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.tp-chip {
  padding: 9px 16px;
  border-radius: 999px;
  border: 1px solid var(--tq-border);
  background: transparent;
  color: var(--tq-dim);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.tp-chip--on {
  background: var(--tq-seg-on);
  border-color: var(--tq-border);
  color: inherit;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

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
}
.tp-note { margin: 12px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--tq-dim); }
.tp-note--lead { margin-top: 14px; font-size: 13.5px; }
</style>
