<template>
  <div v-if="open" class="gc-mask" @click.self="close">
    <div class="gc-sheet" role="dialog" aria-modal="true" aria-label="生成礼品卡">
      <div class="gc-head">
        <h3 class="gc-title">生成礼品卡</h3>
        <button type="button" class="gc-close" aria-label="关闭" @click="close">
          <X :size="20" />
        </button>
      </div>

      <!-- 表单态：填面额 + 选有效期 -->
      <template v-if="!created">
        <label class="gc-label" for="gc-amount">礼品卡面额（美元）</label>
        <div class="gc-amount-row">
          <span class="gc-amount-sign" aria-hidden="true">$</span>
          <input
            id="gc-amount"
            v-model="amountText"
            class="gc-amount"
            type="text"
            inputmode="decimal"
            placeholder="0.00"
            autocomplete="off"
            :disabled="submitting"
            @keydown.enter.prevent="submit"
          />
        </div>
        <p class="gc-hint">可用余额 $ {{ balance }}</p>

        <span class="gc-label">有效期</span>
        <div class="gc-days" role="group" aria-label="有效期">
          <button
            v-for="d in dayOptions"
            :key="d"
            type="button"
            class="gc-day"
            :class="{ 'gc-day--on': validDays === d }"
            :aria-pressed="validDays === d"
            @click="validDays = d"
          >{{ d }} 天</button>
        </div>

        <p v-if="formError" class="gc-err" role="alert">{{ formError }}</p>
        <button
          type="button"
          class="btn-primary gc-confirm"
          :disabled="submitting"
          @click="submit"
        >{{ submitting ? '生成中…' : '确认生成' }}</button>
        <p class="gc-note">生成后金额会先从你的余额里扣下；对方没在有效期内用掉，面值会自动退回你的余额。</p>
      </template>

      <!-- 成功态：展示码 + 复制 -->
      <template v-else>
        <div class="gc-done">
          <span class="gc-done__icon" aria-hidden="true"><Check :size="22" /></span>
          <p class="gc-done__title">礼品卡已生成</p>
          <p class="gc-done__sub">面值 $ {{ created.amount }} · {{ created.valid_days }} 天内有效</p>
        </div>
        <div class="gc-code-row">
          <span class="gc-code">{{ created.code }}</span>
          <button type="button" class="gc-copy" @click="copyCode">
            <Copy :size="15" /> 复制
          </button>
        </div>
        <p class="gc-note">把这张码发给好友，对方在钱包页输入这张码，就能把面值存进自己的余额。</p>
        <button type="button" class="btn-primary gc-confirm" @click="close">完成</button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * GiftCodeSheet —— 把余额做成礼品卡的居中玻璃浮窗（钱包页 UHD 重做，
 * 2026-11-15；弹层形态 2026-11-15 批 1 改居中玻璃浮窗，与购流量弹窗
 * 同口径）。面额输入（带可用余额提示）+ 有效期胶囊（1/3/7 天，上限 7）
 * + 确认生成；成功后弹层内展示码 + 复制钮 + 用途说明。
 * 金额校验以后端为准（余额/小数位/有效期），前端只拦空与明显格式错，
 * 后端 detail 原样展示，不在前端假算。
 */
import { ref, watch } from 'vue'
import { Check, Copy, X } from 'lucide-vue-next'
import { walletApi, type GiftCodeCreateResult } from '@/api/wallet'
import '../../styles/traffic-view.css'
import { useToast } from '@/composables/useToast'
import { toUserMessage } from '@/utils/toUserMessage'

const props = defineProps<{
  open: boolean
  /** 当前可用余额（字符串，两位小数） */
  balance: string
}>()

const emit = defineEmits<{
  close: []
  created: [result: GiftCodeCreateResult]
}>()

const toast = useToast()

const dayOptions = [1, 3, 7]
const amountText = ref('')
const validDays = ref(7)
const formError = ref('')
const submitting = ref(false)
const created = ref<GiftCodeCreateResult | null>(null)

watch(
  () => props.open,
  (open) => {
    if (open) {
      amountText.value = ''
      validDays.value = 7
      formError.value = ''
      created.value = null
    }
  },
)

function close() {
  emit('close')
}

async function submit() {
  if (submitting.value) return
  const raw = amountText.value.trim()
  if (!raw) {
    formError.value = '先填面额'
    return
  }
  formError.value = ''
  submitting.value = true
  try {
    const res = await walletApi.createGiftCode({
      amount: raw,
      valid_days: validDays.value,
    })
    created.value = res
    emit('created', res)
  } catch (e) {
    formError.value = toUserMessage(e)
  } finally {
    submitting.value = false
  }
}

async function copyCode() {
  if (!created.value) return
  const text = created.value.code
  try {
    await navigator.clipboard.writeText(text)
    toast.success('兑换码已复制')
  } catch {
    // 剪贴板不可用时退回老办法（隐藏 textarea + execCommand）
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      toast.success('兑换码已复制')
    } catch {
      toast.error('复制失败，请长按兑换码手动复制')
    } finally {
      document.body.removeChild(ta)
    }
  }
}
</script>

<style scoped>
/* 居中玻璃浮窗（2026-11-15 弹层形态批 1，与购流量弹窗同一口径）：
   宽 92%、max 420px、四角圆角 26；玻璃五件＝半透底 --uhd-glass-d/l
   ＋slab 档 blur(24px) saturate(160%)＋边缘亮光＋内高光＋大柔影
   （--uhd-glass-slab-* 阴影束）；内层填充/描边走 --tq-* 弹窗令牌，
   深浅双态自动跟随。 */
.gc-mask {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: rgba(10, 10, 14, 0.55);
  backdrop-filter: blur(10px) saturate(150%);
  -webkit-backdrop-filter: blur(10px) saturate(150%);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: gc-fade-in 0.22s ease both;
}
@keyframes gc-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.gc-sheet {
  width: 92%;
  max-width: 420px;
  max-height: 88vh;
  overflow-y: auto;
  background: var(--uhd-glass-d);
  border: 1px solid var(--uhd-glass-border);
  border-radius: 26px;
  padding: 22px 22px 24px;
  color: var(--tq-fg);
  box-shadow: var(--uhd-glass-slab-shadow);
  backdrop-filter: var(--uhd-glass-slab-filter);
  -webkit-backdrop-filter: var(--uhd-glass-slab-filter);
  animation: gc-pop-in 0.28s cubic-bezier(0.32, 0.72, 0.28, 1) both;
}
html[data-theme='light'] .gc-sheet {
  background: var(--uhd-glass-l);
  border-color: var(--uhd-glass-border-l);
}
@keyframes gc-pop-in {
  from { transform: translateY(12px) scale(0.97); opacity: 0; }
  to { transform: translateY(0) scale(1); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .gc-mask, .gc-sheet { animation: none; }
}

.gc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.gc-title { margin: 0; font-size: 18px; font-weight: 750; }
.gc-close {
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

.gc-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--tq-dim);
  margin: 14px 0 8px;
}
.gc-label:first-of-type { margin-top: 0; }
.gc-amount-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 52px;
  padding: 0 16px;
  border-radius: 14px;
  background: var(--tq-card);
  border: 1px solid var(--tq-border);
}
.gc-amount-sign { font-size: 17px; font-weight: 700; color: var(--tq-dim); }
.gc-amount {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 17px;
  font-weight: 650;
  outline: none;
}
.gc-amount::placeholder { color: var(--tq-dim); }
.gc-hint { margin: 8px 0 0; font-size: 12.5px; color: var(--tq-dim); }

.gc-days { display: flex; gap: 8px; }
.gc-day {
  padding: 9px 18px;
  border-radius: 999px;
  border: 1px solid var(--tq-border);
  background: transparent;
  color: var(--tq-dim);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
/* 选中片＝微亮玻璃（与购流量弹窗分段选中片同口径，不用实心反色块） */
.gc-day--on {
  background: var(--tq-seg-on);
  border-color: var(--tq-border);
  color: inherit;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

.gc-err { margin: 12px 0 0; font-size: 13px; color: #f87171; }
html[data-theme='light'] .gc-err { color: #dc2626; }
.gc-confirm {
  width: 100%;
  height: 50px;
  margin-top: 20px;
  border-radius: 25px;
  font-size: 15.5px;
  font-weight: 750;
  cursor: pointer;
}
.gc-note { margin: 12px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--tq-dim); }

.gc-done { text-align: center; padding: 6px 0 2px; }
.gc-done__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: rgba(22, 163, 74, 0.15);
  border: 1px solid rgba(22, 163, 74, 0.5);
  color: #4ade80;
}
html[data-theme='light'] .gc-done__icon { color: #16a34a; }
.gc-done__title { margin: 10px 0 0; font-size: 16.5px; font-weight: 750; }
.gc-done__sub { margin: 4px 0 0; font-size: 13px; color: var(--tq-dim); }
.gc-code-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 14px;
  background: var(--tq-card);
  border: 1px dashed var(--tq-border);
}
.gc-code {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.04em;
  overflow-wrap: anywhere;
}
.gc-copy {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 8px 13px;
  border-radius: 999px;
  border: 1px solid var(--tq-border);
  background: var(--tq-seg);
  color: inherit;
  font-family: inherit;
  font-size: 13px;
  font-weight: 650;
  cursor: pointer;
}
.gc-copy:active { transform: scale(0.96); }
</style>
