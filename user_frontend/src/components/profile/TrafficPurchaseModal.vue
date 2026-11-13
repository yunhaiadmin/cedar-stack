<template>
  <div v-if="open" class="tq-mask" @click.self="close">
    <div class="tq-modal" role="dialog" aria-modal="true" aria-label="购买流量">
      <div class="tq-head">
        <h3 class="tq-title">购买流量</h3>
        <button type="button" class="tq-close" aria-label="关闭" @click="close">×</button>
      </div>

      <div class="tq-tabs" role="tablist">
        <button
          type="button"
          class="tq-tab"
          :class="{ 'tq-tab--on': tab === 'time' }"
          role="tab"
          @click="tab = 'time'"
        >
          时间换流量
        </button>
        <button
          type="button"
          class="tq-tab"
          :class="{ 'tq-tab--on': tab === 'balance' }"
          role="tab"
          @click="tab = 'balance'"
        >
          余额购买
        </button>
      </div>

      <!-- 时间换流量：真成交（扣订阅天数换抵扣额度，后端留痕幂等） -->
      <div v-if="tab === 'time'" class="tq-body">
        <p class="tq-now">
          当前流量：已用 {{ formatBytes(traffic?.used_bytes) }} / 共 {{ formatBytes(traffic?.quota_bytes) }}
          <span v-if="traffic && traffic.credit_bytes > 0" class="tq-now__credit">
            （含已兑换抵扣 {{ formatBytes(traffic.credit_bytes) }}）
          </span>
        </p>
        <div class="tq-opts">
          <button
            v-for="opt in options"
            :key="opt.days"
            type="button"
            class="tq-opt"
            :class="{ 'tq-opt--on': selectedDays === opt.days }"
            :disabled="submitting"
            @click="selectOption(opt.days)"
          >
            <span class="tq-opt__main">−{{ opt.days }} 天</span>
            <span class="tq-opt__sub">付费时长减少 {{ opt.days }} 天，抵扣 {{ formatBytes(opt.bytes) }} 已用流量</span>
          </button>
        </div>
        <p class="tq-warn">
          注意：选择后将立即扣除对应天数，用于兑换流量。此操作无法撤销，请确认后再提交！
        </p>
        <p v-if="error" class="tq-error" role="alert">{{ error }}</p>
        <button
          type="button"
          class="tq-confirm"
          :disabled="!selected || submitting"
          :aria-busy="submitting"
          @click="onConfirmClick"
        >
          <span v-if="submitting" class="tq-spinner" aria-hidden="true"></span>
          {{ submitting ? '提交中…' : confirming ? `确认扣除 ${selected?.days} 天兑换？` : '确认兑换' }}
        </button>
      </div>

      <!-- 余额购买：钱包未建，整页签置灰说明，绝不假接支付 -->
      <div v-else class="tq-body tq-body--disabled" aria-disabled="true">
        <p class="tq-now">当前流量：已用 {{ formatBytes(traffic?.used_bytes) }} / 共 {{ formatBytes(traffic?.quota_bytes) }}</p>
        <div class="tq-opts">
          <button v-for="g in [100, 200, 500, 1000]" :key="g" type="button" class="tq-opt" disabled>
            <span class="tq-opt__main">{{ g }}G</span>
            <span class="tq-opt__sub">用余额抵扣已用流量</span>
          </button>
        </div>
        <p class="tq-note">余额购买将在钱包功能上线后可用，敬请期待。</p>
        <button type="button" class="tq-confirm" disabled>钱包上线后可用</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 购流量弹窗（流量额度一期，形态照 UHDNow 两页签）：
 * - 时间换流量：真做通——选档 → 二次确认 → 提交（带幂等键，同一次
 *   打开内重试不重复扣天数）→ 成功后把最新流量汇总回抛父级刷新。
 * - 余额购买：钱包（余额）功能尚未建，整页签置灰＋说明，不接任何
 *   支付端点，不许假装能买。
 */
import { computed, ref, watch } from 'vue'
import {
  exchangeTimeForTraffic,
  formatBytes,
  type TrafficExchangeOption,
  type TrafficSummary,
} from '@/api/traffic'
import { useToast } from '@/composables/useToast'
import '../../styles/traffic-view.css'
import { toUserMessage } from '@/utils/toUserMessage'

const props = defineProps<{
  open: boolean
  traffic: TrafficSummary | null
  options: TrafficExchangeOption[]
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'exchanged', traffic: TrafficSummary): void
}>()

const toast = useToast()
const tab = ref<'time' | 'balance'>('time')
const selectedDays = ref<number | null>(null)
const confirming = ref(false)
const submitting = ref(false)
const error = ref('')
/** 本次打开弹窗的幂等键：打开时生成一次，选档/重试都不换，
 * 只有重新打开弹窗才换新——同一次确认绝不重复成交。 */
const idempotencyKey = ref('')

const selected = computed(
  () => props.options.find((o) => o.days === selectedDays.value) ?? null,
)

function newKey(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `tq-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  }
}

watch(
  () => props.open,
  (v) => {
    if (v) {
      tab.value = 'time'
      selectedDays.value = props.options[0]?.days ?? null
      confirming.value = false
      submitting.value = false
      error.value = ''
      idempotencyKey.value = newKey()
    }
  },
)

function selectOption(days: number) {
  selectedDays.value = days
  confirming.value = false
  error.value = ''
}

function close() {
  if (submitting.value) return // 提交在途不许关，防结果丢失
  emit('update:open', false)
}

async function onConfirmClick() {
  if (!selected.value || submitting.value) return
  if (!confirming.value) {
    confirming.value = true
    return
  }
  submitting.value = true
  error.value = ''
  try {
    const res = await exchangeTimeForTraffic(selected.value.days, idempotencyKey.value)
    toast.success(
      res.replayed
        ? '这次兑换已经生效过了，没有重复扣天数'
        : `已兑换：扣除 ${selected.value.days} 天，抵扣 ${formatBytes(selected.value.bytes)} 流量`,
    )
    emit('exchanged', res.traffic)
    emit('update:open', false)
  } catch (e) {
    confirming.value = false
    error.value = toUserMessage(e)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.tq-mask {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: rgba(10, 10, 14, 0.55);
  backdrop-filter: blur(6px) saturate(140%);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  animation: tq-fade-in 0.22s ease both;
}
@keyframes tq-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.tq-modal {
  width: 100%;
  max-width: 460px;
  max-height: 88vh;
  overflow-y: auto;
  background: var(--tq-bg, #ffffff);
  color: #16161a;
  border-radius: 22px 22px 0 0;
  padding: 20px 20px 28px;
  box-shadow: 0 -12px 44px rgba(0, 0, 0, 0.28);
  /* app 式底部弹层：自下而上滑入 */
  animation: tq-sheet-up 0.3s cubic-bezier(0.32, 0.72, 0.28, 1) both;
}
@keyframes tq-sheet-up {
  from { transform: translateY(48px); opacity: 0.4; }
  to { transform: translateY(0); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .tq-mask, .tq-modal { animation: none; }
  .tq-spinner { animation-duration: 1.6s; }
}
@media (min-width: 640px) {
  .tq-mask { align-items: center; }
  .tq-modal { border-radius: 22px; }
}
.tq-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tq-title { font-size: 19px; font-weight: 700; margin: 0; }
.tq-close {
  border: none;
  background: transparent;
  font-size: 24px;
  line-height: 1;
  color: inherit;
  opacity: 0.55;
  cursor: pointer;
  padding: 4px 8px;
}
.tq-tabs {
  display: flex;
  gap: 8px;
  margin: 16px 0 4px;
  background: rgba(120, 120, 140, 0.12);
  border-radius: 999px;
  padding: 4px;
}
.tq-tab {
  flex: 1;
  border: none;
  background: transparent;
  border-radius: 999px;
  padding: 8px 0;
  font-size: 14px;
  font-weight: 600;
  color: inherit;
  opacity: 0.65;
  cursor: pointer;
}
.tq-tab--on { background: #ffffff; opacity: 1; box-shadow: 0 1px 6px rgba(0, 0, 0, 0.14); }
.tq-body { padding-top: 12px; }
.tq-body--disabled { opacity: 0.55; }
.tq-now { font-size: 13px; opacity: 0.75; margin: 0 0 12px; }
.tq-now__credit { display: block; margin-top: 2px; }
.tq-opts { display: flex; flex-direction: column; gap: 10px; }
.tq-opt {
  text-align: left;
  border: 1.5px solid rgba(120, 120, 140, 0.35);
  background: transparent;
  border-radius: 14px;
  padding: 12px 14px;
  cursor: pointer;
  color: inherit;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tq-opt--on { border-color: #f2762e; box-shadow: 0 0 0 1px #f2762e inset; }
.tq-opt__main { font-size: 16px; font-weight: 700; }
.tq-opt__sub { font-size: 12.5px; opacity: 0.7; }
.tq-warn {
  margin: 14px 0 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: #c2410c;
  background: rgba(242, 118, 46, 0.1);
  border-radius: 10px;
  padding: 10px 12px;
}
.tq-note { font-size: 13px; opacity: 0.8; margin: 14px 0 0; }
.tq-error { color: #dc2626; font-size: 13px; margin: 12px 0 0; }
.tq-confirm {
  width: 100%;
  margin-top: 16px;
  border: none;
  border-radius: 999px;
  padding: 13px 0;
  font-size: 15px;
  font-weight: 700;
  color: #ffffff;
  cursor: pointer;
  background: linear-gradient(100deg, #2f8fe0, #c46c90 55%, #f2a45a);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: transform 0.12s ease, opacity 0.12s ease;
}
.tq-confirm:disabled { opacity: 0.45; cursor: not-allowed; }
/* 按压反馈态（app 口径：点下去立刻有反应） */
.tq-confirm:active:not(:disabled),
.tq-opt:active:not(:disabled),
.tq-tab:active,
.tq-close:active {
  transform: scale(0.97);
  opacity: 0.88;
}
.tq-opt, .tq-tab, .tq-close { transition: transform 0.12s ease, opacity 0.12s ease; }
.tq-spinner {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.45);
  border-top-color: #ffffff;
  animation: tq-spin 0.7s linear infinite;
  flex: none;
}
@keyframes tq-spin {
  to { transform: rotate(360deg); }
}
</style>
