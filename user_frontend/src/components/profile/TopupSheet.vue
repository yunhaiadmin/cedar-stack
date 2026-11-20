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
      <template v-if="phase === 'form'">
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
        <!-- 余额行（钱包余额由钱包页透传，居中灰字） -->
        <p class="tp-balance">余额 ${{ balance }}</p>

        <!-- 常驻金额行：居中成组（$ + 大号数字），金额唯一来源；
             无边框无底色，输入框就是这行本身（限额全按 info 下发） -->
        <div class="tp-amount-row">
          <span class="tp-amount-sign" aria-hidden="true">$</span>
          <input
            ref="amountInputRef"
            v-model="amountText"
            class="tp-amount"
            :class="{ 'tp-amount--dim': hintState !== 'valid' }"
            type="text"
            inputmode="decimal"
            placeholder="1.00"
            autocomplete="off"
            :style="{ width: amountWidthCh + 'ch' }"
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

        <!-- 金额档位：点了把金额填进输入框并收起键盘；无常驻选中态，
             仅金额有效且不等于任一档位时「自定义」呈浅灰填充 -->
        <div class="tp-tiers" role="radiogroup" aria-label="充值金额">
          <button
            v-for="t in tiers"
            :key="t"
            type="button"
            class="tp-tier"
            role="radio"
            :aria-checked="activeTier === t"
            @click="pickTier(t)"
          >${{ t }}</button>
          <button
            type="button"
            class="tp-tier"
            :class="{ 'tp-tier--on': customTierOn }"
            role="radio"
            :aria-checked="activeTier === 'custom'"
            @click="focusCustom"
          >自定义</button>
        </div>

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
            <span class="tp-method__radio" aria-hidden="true"></span>
            <span class="tp-method__badge" :class="`tp-method__badge--${m}`" aria-hidden="true">{{ methodBadge(m) }}</span>
            <span class="tp-method__name">{{ methodLabel(m) }}</span>
          </button>
        </div>

        <p v-if="formError" class="tp-err" role="alert">{{ formError }}</p>
        <button
          type="button"
          class="btn-primary tp-confirm"
          :class="{ 'tp-confirm--busy': submitting }"
          :disabled="!canSubmit"
          @click="submit"
        >
          <span v-if="submitting" class="tp-spinner" aria-hidden="true"></span>
          <template v-else>{{ confirmText }}</template>
        </button>
      </template>
      </template>
      <!-- 等待屏：与表单共用同一张卡片，同卡片内切换 -->
      <TopupWaiting
        v-else
        :phase="waitPhase"
        :fail-kind="failKind"
        :amount-cny="payCny"
        :credit-usd="creditUsd"
        :pay-url="payUrl"
        @close="close"
        @retry="retryTopup"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * TopupSheet —— 在线充值弹窗（钱包批 3，形态照友站 recharge-dialog
 * 录屏逐帧量测口径）：底部浮卡；标题「账户充值」+ 退款提示副文；
 * 余额行（居中）+ 常驻金额行（居中成组、打开即聚焦，金额唯一
 * 来源），档位 $10/$30/$50/$70 点了把金额填进输入框并收起键盘、
 * 「自定义」只聚焦输入框（无常驻选中态，仅金额有效且不等于任一
 * 档位时「自定义」浅灰填充）；提示行三态（最低额 / 超限红字 /
 * ≈¥折算行，居中）；支付方式按服务端 info 下发列表渲染成单选行
 * （列表上下细线、radio 在左；本期只有法币方式，数字货币不做、
 * 不留假入口）；确认钮文案「确认支付 ¥X.XX」、提交中只剩转圈。
 * 金额校验、费率、汇率、限额全读 info（钱包信息接口的 topup
 * 块），前端不写死；应付预览与后端同一公式（美元 × 生效汇率 ×
 * (1+费率%)，到分取整），入账以后端订单为准。下单成功后在同卡片内切等待屏
 * （TopupWaiting）：新窗口开支付页（iOS Safari 下 window.open 须在点击手势
 * 同步调用，故先同步开空白页再写地址）、二维码扫码同付、每 3 秒轮询订单状态
 * 到账/失败/10 分钟超时；paid 即停轮询并通知钱包页刷新余额。
 */
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { walletApi, type WalletTopupInfo } from '@/api/wallet'
import TopupWaiting from '@/components/profile/TopupWaiting.vue'
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
  /** 到账：带入账美元串，钱包页据此刷新余额与流水 */
  paid: [creditUsd: string]
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

/** 等待屏状态机：form 表单 / waiting 等付款 / paid 到账 / failed 失败 */
type WaitPhase = 'form' | 'waiting' | 'paid' | 'failed'
type WaitFailKind = 'failed' | 'expired' | 'timeout'
const phase = ref<WaitPhase>('form')
const failKind = ref<WaitFailKind>('failed')
/** 传给 TopupWaiting 的 phase（表单态不渲染它，'form' 收窄掉） */
const waitPhase = computed<'waiting' | 'paid' | 'failed'>(() =>
  phase.value === 'paid' ? 'paid' : phase.value === 'failed' ? 'failed' : 'waiting')
/** 本次订单：单号 / 应付人民币（下单返回权威值）/ 支付地址 / 到账美元 */
const orderNo = ref('')
const payCny = ref('')
const payUrl = ref('')
const creditUsd = ref('')
/** 轮询：3 秒一轮、10 分钟墙钟截止；组件已卸载/弹窗已关即不启动 */
const POLL_MS = 3000
const POLL_TIMEOUT_MS = 10 * 60 * 1000
const pollTimer = ref<number | null>(null)
let pollDeadline = 0
let alive = true

function stopPolling() {
  if (pollTimer.value !== null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
  document.removeEventListener('visibilitychange', onVisibilityChange)
}

function onVisibilityChange() {
  if (phase.value !== 'waiting') return
  if (document.hidden) {
    // 切后台只停轮询、不停墙钟截止（10 分钟是网关付款窗口，按墙钟算）
    if (pollTimer.value !== null) {
      window.clearInterval(pollTimer.value)
      pollTimer.value = null
    }
  } else if (pollTimer.value === null) {
    void pollOnce()
    pollTimer.value = window.setInterval((): void => {
      void pollOnce()
    }, POLL_MS)
  }
}

async function pollOnce() {
  if (!orderNo.value || phase.value !== 'waiting') return
  if (Date.now() > pollDeadline) {
    stopPolling()
    failKind.value = 'timeout'
    phase.value = 'failed'
    return
  }
  let order
  try {
    order = await walletApi.getTopupOrder(orderNo.value)
  } catch (e) {
    // 404＝订单找不到了（不该发生，发生了就诚实报错不再等）
    const status = (e as { response?: { status?: number } })?.response?.status
    if (status === 404) {
      stopPolling()
      failKind.value = 'failed'
      phase.value = 'failed'
    }
    // 其余瞬时失败（断网/抖动）等下一轮，不报错、不停轮询
    return
  }
  if (order.status === 'paid') {
    stopPolling()
    creditUsd.value = order.credit_usd
    phase.value = 'paid'
    emit('paid', order.credit_usd)
  } else if (order.status === 'failed' || order.status === 'expired') {
    stopPolling()
    failKind.value = order.status
    phase.value = 'failed'
  }
  // pending：继续等
}

function startPolling() {
  stopPolling()
  if (!alive || !props.open || !orderNo.value) return
  pollDeadline = Date.now() + POLL_TIMEOUT_MS
  pollTimer.value = window.setInterval((): void => {
    void pollOnce()
  }, POLL_MS)
  document.addEventListener('visibilitychange', onVisibilityChange)
}

onUnmounted(() => {
  alive = false
  stopPolling()
})

/** 当前金额（美分）；输入非法（空/非数字/超两位小数）时为 null */
const usdFen = computed<number | null>(() => {
  const raw = amountText.value.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) return null
  const v = Number(raw)
  if (!Number.isFinite(v) || v <= 0) return null
  return Math.round(v * 100)
})

/** 输入框宽度（ch）：不用 size 属性（浏览器换算的字符宽带内部余量，
 * 盒子会被撑宽、数字与 $ 之间空出一截）；按内容长度用 ch 定宽紧贴文字。
 * 空时 4ch 容纳 placeholder「1.00」不被裁 */
const amountWidthCh = computed(() =>
  amountText.value.length === 0 ? 4 : Math.max(1, amountText.value.length))

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

/** 「自定义」胶囊填充态（友站帧口径）：金额有效且不等于任一档位
 * 时才浅灰填充；金额无效（如 0）不填充，数字档位永不填充 */
const customTierOn = computed(() => hintState.value === 'valid' && activeTier.value === 'custom')

watch(
  () => props.open,
  (open) => {
    if (open) {
      amountText.value = ''
      formError.value = ''
      inputFocused.value = false
      // 重开即全新一单：阶段回表单、订单状态清掉、轮询停掉
      stopPolling()
      phase.value = 'form'
      failKind.value = 'failed'
      orderNo.value = ''
      payCny.value = ''
      payUrl.value = ''
      creditUsd.value = ''
      const methods = props.info?.methods ?? []
      method.value = methods.includes('alipay') ? 'alipay' : (methods[0] ?? 'alipay')
      // 打开即聚焦金额框、键盘随起（友站帧口径）
      if (props.info?.enabled) {
        void nextTick(() => amountInputRef.value?.focus())
      }
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

/** 点档位：把金额填进输入框并收起键盘（友站帧口径） */
function pickTier(t: number) {
  amountText.value = String(t)
  amountInputRef.value?.blur()
}

/** 点「自定义」：只聚焦大输入框（内容由用户自行改/清空） */
function focusCustom() {
  amountInputRef.value?.focus()
}

function close() {
  stopPolling()
  emit('close')
}

/** 「重新充值」：回表单重下一单，金额保留（用户只用再点一次确认） */
function retryTopup() {
  phase.value = 'form'
  failKind.value = 'failed'
  orderNo.value = ''
  payCny.value = ''
  payUrl.value = ''
  creditUsd.value = ''
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
 * 下一次点击）；焦点还在输入框里时遮罩点击不关，X 钮照常关。
 * 提交中也不许遮罩关（等下单结果出来再切等待屏）。 */
function onMaskClick() {
  if (inputFocused.value || submitting.value) return
  close()
}

async function submit() {
  if (!canSubmit.value || usdFen.value === null) return
  formError.value = ''
  submitting.value = true
  // iOS Safari：window.open 必须在点击手势里同步调用才不被拦截。
  // 先同步开一个空白页，下单成功后再把支付地址写进去；失败则关掉。
  // （不加 noopener：加了会拿不到窗口句柄、写不进地址。）
  const payWin = window.open('about:blank', '_blank')
  try {
    const res = await walletApi.createTopupOrder({
      amount_usd: (usdFen.value / 100).toFixed(2),
      payment_method: method.value,
    })
    if (payWin && !payWin.closed) payWin.location.href = res.pay_url
    submitting.value = false
    // 组件已卸载（提交中用户关了弹窗）就不切等待屏：支付页已开，
    // 回来走 /wallet?topup=1 的既有落点
    if (!alive || !props.open) return
    payCny.value = res.amount_cny
    payUrl.value = res.pay_url
    orderNo.value = res.order_no
    phase.value = 'waiting'
    startPolling()
  } catch (e) {
    if (payWin && !payWin.closed) payWin.close()
    formError.value = toUserMessage(e)
    submitting.value = false
  }
}
</script>

<style scoped>
/* 底部实色浮卡（2026-11-19 友站录屏逐帧量测口径：左右/底部
   间距 10、圆角 16、遮罩黑 50%；深色底走我方令牌对应面）。 */
.tp-mask {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 0 10px 10px;
  animation: tp-fade-in 0.22s ease both;
}
@keyframes tp-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.tp-sheet {
  width: 100%;
  max-width: 420px;
  max-height: 88vh;
  overflow-y: auto;
  background: var(--ax-d-surface-raised);
  border: 1px solid var(--ax-glass-border);
  border-radius: 16px;
  padding: 22px 22px calc(16px + env(safe-area-inset-bottom, 0px));
  color: var(--tq-fg);
  box-shadow: var(--ax-shadow-modal);
  animation: tp-pop-in 0.28s cubic-bezier(0.32, 0.72, 0.28, 1) both;
}
html[data-theme='light'] .tp-sheet {
  background: var(--ax-l-surface);
  border-color: var(--ax-glass-border-l);
}
@keyframes tp-pop-in {
  from { transform: translateY(24px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
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
.tp-title { margin: 0; font-size: 24px; font-weight: 800; }
.tp-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: var(--tq-dim);
  cursor: pointer;
}
.tp-sub { margin: 8px 0 0; font-size: 13.5px; line-height: 1.6; color: var(--tq-dim); }

.tp-balance { margin: 14px 0 0; font-size: 12px; text-align: center; color: var(--tq-dim); }

/* 金额行：居中成组、无边框无底色（友站帧口径）；$ 灰 20/700，
   数字 36/800，无效（0/低于最低/超限）时数字变灰 */
.tp-amount-row {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 5px;
  margin-top: 10px;
}
.tp-amount-sign { font-size: 20px; font-weight: 700; color: var(--tq-dim); }
.tp-amount {
  min-width: 0;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  /* mobile.css 移动端有 input{font-size:16px !important} 防 iOS 聚焦缩放，
     该规则只要求 ≥16px，本处 36px 本就满足，但会被它压小，故带 !important 覆盖 */
  font-size: 36px !important;
  font-weight: 800;
  text-align: center;
  outline: none;
}
.tp-amount--dim { color: var(--tq-dim); }
.tp-amount::placeholder { color: var(--tq-dim); }

.tp-hint { margin: 8px 0 0; font-size: 12px; text-align: center; color: var(--tq-dim); }
.tp-hint--over { color: #f87171; }
html[data-theme='light'] .tp-hint--over { color: #dc2626; }

/* 档位胶囊：整行高 ≈43、白底细边全圆角；无常驻选中态，仅
   「自定义」在金额有效且不等于任一档位时浅灰填充（友站帧口径） */
.tp-tiers { display: flex; gap: 8px; margin-top: 16px; }
.tp-tier {
  flex: 1;
  padding: 11px 0;
  border-radius: 999px;
  border: 1px solid var(--tq-border);
  background: var(--tq-card);
  color: inherit;
  font-family: inherit;
  font-size: 15px;
  font-weight: 650;
  cursor: pointer;
  white-space: nowrap;
}
html[data-theme='light'] .tp-tier { background: #ffffff; }
.tp-tier--on { background: var(--tq-seg-on); }
html[data-theme='light'] .tp-tier--on { background: rgba(17, 17, 20, 0.07); }

.tp-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--tq-dim);
  margin: 16px 0 8px;
}
/* 支付方式单选行：列表上下各一条细线，行高 54；左 radio 圆 +
   品牌首字徽标 + 名称，整行可点；选中只看 radio、不变底色字重 */
.tp-methods {
  border-top: 1px solid var(--tq-border);
  border-bottom: 1px solid var(--tq-border);
}
.tp-method {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 54px;
  padding: 0 4px;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 15.5px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
}
.tp-method__badge {
  flex: none;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #8a8f98;
  color: #fff;
  font-size: 12px;
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
  font-weight: 700;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
/* 提交中：只剩居中转圈、整钮变淡禁用（友站帧口径） */
.tp-confirm--busy { opacity: 0.55; }
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
