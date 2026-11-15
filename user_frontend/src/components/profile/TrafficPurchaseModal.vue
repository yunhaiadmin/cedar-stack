<template>
  <div v-if="open" class="tq-mask" @click.self="close">
    <div class="tq-modal" role="dialog" aria-modal="true" aria-label="购买流量包">
      <div class="tq-head">
        <span class="tq-head__icon" aria-hidden="true">
          <TrendingUp :size="20" />
        </span>
        <div class="tq-head__text">
          <h3 class="tq-title">购买流量包</h3>
          <p class="tq-sub">临时补充流量</p>
        </div>
        <button type="button" class="tq-close" aria-label="关闭" @click="close">×</button>
      </div>

      <!-- 当前流量卡：剩余百分比 + 已用/共（GB 两位小数，照友站帧口径） -->
      <div class="tq-card">
        <div class="tq-card__row">
          <span class="tq-card__label">当前流量</span>
          <span class="tq-card__pct">{{ remainPct }}% 剩余</span>
        </div>
        <p class="tq-card__used">
          已用 {{ formatGbFixed(traffic?.used_bytes) }} GB / 共 {{ formatGbFixed(traffic?.quota_bytes) }} GB
        </p>
      </div>

      <div class="tq-tabs" role="tablist">
        <button
          type="button"
          class="tq-tab"
          :class="{ 'tq-tab--on': tab === 'balance' }"
          role="tab"
          @click="tab = 'balance'"
        >
          余额购买
        </button>
        <button
          type="button"
          class="tq-tab"
          :class="{ 'tq-tab--on': tab === 'time' }"
          role="tab"
          @click="tab = 'time'"
        >
          时间换流量
        </button>
      </div>

      <!-- 余额购买：自由输入 GB（最低 100）→ 预览（抵扣量/应付/余额
           是否够）→ 预览区内两步确认成交，仍走 buyBalanceTraffic
           幂等链；credit 只冲已用，超出部分同周期内继续抵扣。 -->
      <div v-if="tab === 'balance'" class="tq-body">
        <div class="tq-balrow">
          <span class="tq-balrow__label">当前余额</span>
          <span class="tq-balrow__amt">$ {{ walletBalanceText }}</span>
        </div>
        <p class="tq-label">抵扣已用流量（最低 100 GB）</p>
        <div class="tq-inputrow">
          <div class="tq-input">
            <input
              v-model="gbInput"
              class="tq-input__field"
              type="number"
              inputmode="numeric"
              min="100"
              step="1"
              aria-label="购买流量 GB 数"
              @input="onGbInput"
            />
            <span class="tq-input__suffix">GB</span>
          </div>
          <button
            type="button"
            class="tq-preview-btn"
            :disabled="balanceSubmitting"
            @click="showPreview"
          >
            预览
          </button>
        </div>
        <div class="tq-chips">
          <button
            v-for="g in quickGb"
            :key="g"
            type="button"
            class="tq-chip"
            :class="{ 'tq-chip--on': inputGb === g }"
            @click="pickChip(g)"
          >
            {{ g }}G
          </button>
        </div>
        <p v-if="inputError" class="tq-error" role="alert">{{ inputError }}</p>
        <div v-if="preview" class="tq-preview">
          <p class="tq-preview__line">
            本次抵扣 <b>{{ preview.gb }} GB</b> 流量，应付 <b>$ {{ preview.price }}</b>
          </p>
          <p class="tq-preview__note">将抵扣已用流量，超出部分在本周期内继续抵扣</p>
          <p v-if="previewShortfall > 0" class="tq-error" role="alert">
            钱包余额不足，还差 $ {{ previewShortfall.toFixed(2) }}，请先去钱包充值
          </p>
          <button
            type="button"
            class="tq-confirm"
            :disabled="balanceSubmitting || previewShortfall > 0"
            :aria-busy="balanceSubmitting"
            @click="onBalanceConfirmClick"
          >
            <span v-if="balanceSubmitting" class="tq-spinner" aria-hidden="true"></span>
            {{ balanceSubmitting ? '提交中…' : balanceConfirming ? `确认支付 $ ${preview.price} 购买 ${preview.gb}G？` : '确认购买' }}
          </button>
        </div>
        <p v-if="balanceError" class="tq-error" role="alert">{{ balanceError }}</p>
      </div>

      <!-- 时间换流量（包型）：包参数全部后端下发（默认半月包
           15 天抵 50%、整月包 30 天抵 100%）；点包选中后出确认区，
           两步确认才扣时长，不许一击成交。 -->
      <div v-else class="tq-body">
        <div class="tq-opts">
          <button
            v-for="pkg in packages"
            :key="pkg.key"
            type="button"
            class="tq-pkg"
            :class="{ 'tq-pkg--on': selectedPkgKey === pkg.key }"
            :disabled="submitting"
            @click="selectPackage(pkg.key)"
          >
            <span class="tq-pkg__icon" aria-hidden="true">
              <HardDrive :size="20" />
            </span>
            <span class="tq-pkg__main">
              <span class="tq-pkg__name">{{ pkg.name }}</span>
              <span class="tq-pkg__desc">
                从已用记录中扣除 {{ pkg.quota_percent }}% 当月配额，消耗 {{ pkg.days }} 天有效时长
              </span>
              <span class="tq-pkg__warn">将缩短套餐到期时间 {{ pkg.days }} 天</span>
            </span>
            <span class="tq-pkg__side">
              <span class="tq-pkg__days">-{{ pkg.days }}天</span>
              <span class="tq-pkg__cost">消耗时长</span>
            </span>
          </button>
        </div>
        <p class="tq-hint">
          <Info :size="14" class="tq-hint__icon" aria-hidden="true" />
          <span>流量不扩充总配额，而是从已用记录中扣除，让剩余量恢复。同时会消耗等量套餐有效时长。</span>
        </p>
        <p v-if="error" class="tq-error" role="alert">{{ error }}</p>
        <button
          v-if="selectedPkg"
          type="button"
          class="tq-confirm"
          :disabled="submitting"
          :aria-busy="submitting"
          @click="onConfirmClick"
        >
          <span v-if="submitting" class="tq-spinner" aria-hidden="true"></span>
          {{ submitting ? '提交中…' : confirming ? `再点一次确认：扣除 ${selectedPkg.days} 天兑换${selectedPkg.name}` : `确认兑换 · ${selectedPkg.name}（-${selectedPkg.days} 天）` }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 购流量弹窗（2026-11-15 照 UHDNow 录屏逐帧对齐）：
 * - 头部：琥珀图标 +「购买流量包 / 临时补充流量」；当前流量卡
 *   （剩余 % + 已用/共 GB 两位小数）；分段页签默认「余额购买」。
 * - 余额购买：自由输入 GB（最低 100）+ 快捷 chips → 预览（本次
 *   抵扣量、按配置单价现算应付、余额不足明示差额并禁用确认）
 *   → 预览区两步确认，成交走 buyBalanceTraffic（幂等键同前）。
 * - 时间换流量（包型）：包表由后端规则下发（默认半月包 15 天
 *   抵 50% 当月配额、整月包 30 天抵 100%），前端不写死天数/比
 *   例；点包选中出确认区，两步确认后才扣时长。
 * - 外观跟随用户主题：底色/字色/卡片填充全部走 traffic-view.css
 *   的 --tq-* 令牌（深色玻璃 / 浅色纸面），组件内不写死颜色。
 */
import { computed, ref, watch } from 'vue'
import { HardDrive, Info, TrendingUp } from 'lucide-vue-next'
import {
  buyBalanceTraffic,
  exchangeTimeForTraffic,
  formatBytes,
  formatGbFixed,
  type TrafficExchangeRule,
  type TrafficOverview,
  type TrafficSummary,
} from '@/api/traffic'
import { useToast } from '@/composables/useToast'
import '../../styles/traffic-view.css'
import { toUserMessage } from '@/utils/toUserMessage'

const props = defineProps<{
  open: boolean
  traffic: TrafficOverview | null
  rule: TrafficExchangeRule | null
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'exchanged', traffic: TrafficSummary): void
}>()

const toast = useToast()
const tab = ref<'balance' | 'time'>('balance')

/** 当前流量卡：剩余百分比（整数，照帧「100% 剩余」） */
const remainPct = computed(() => {
  const t = props.traffic
  if (!t?.quota_bytes) return 0
  const pct = Math.round((1 - t.used_pct / 100) * 100)
  return Math.min(100, Math.max(0, pct))
})

/* ---------------- 时间换流量（包型） ---------------- */
const packages = computed(() => props.rule?.packages ?? [])
const selectedPkgKey = ref<string | null>(null)
const selectedPkg = computed(
  () => packages.value.find((p) => p.key === selectedPkgKey.value) ?? null,
)
const confirming = ref(false)
const submitting = ref(false)
const error = ref('')

/* ---------------- 余额购买 ---------------- */
/** 最低购买量（GB）：与后端下限同口径，单价/价格全走后端下发 */
const MIN_GB = 100
const quickGb = [100, 200, 500, 1000]
const gbInput = ref('100')
const inputError = ref('')
const preview = ref<{ gb: number; price: string } | null>(null)
const balanceConfirming = ref(false)
const balanceSubmitting = ref(false)
const balanceError = ref('')
/** 余额购流量单价（$/100GB，后端下发美分/100GB，字段名为历史命名；未下发时按默认 $0.30） */
const pricePer100Gb = computed(() => {
  const fen = props.traffic?.balance_price_per_100gb_fen ?? 0
  return fen > 0 ? fen / 100 : 0.3
})
const walletBalanceText = computed(() => props.traffic?.wallet_balance ?? '0.00')
const inputGb = computed(() => {
  const n = Number(gbInput.value)
  return Number.isInteger(n) && n > 0 ? n : null
})
const previewShortfall = computed(() => {
  if (!preview.value) return 0
  const price = Number(preview.value.price)
  const bal = Number(walletBalanceText.value) || 0
  return Math.max(0, Math.round((price - bal) * 100) / 100)
})

/** 本次打开弹窗的幂等键：打开时生成一次，选包/重试都不换，
 * 只有重新打开弹窗才换新——同一次确认绝不重复成交。 */
const idempotencyKey = ref('')

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
      tab.value = 'balance'
      selectedPkgKey.value = null
      confirming.value = false
      submitting.value = false
      error.value = ''
      gbInput.value = '100'
      inputError.value = ''
      preview.value = null
      balanceConfirming.value = false
      balanceSubmitting.value = false
      balanceError.value = ''
      idempotencyKey.value = newKey()
    }
  },
)

function selectPackage(key: string) {
  selectedPkgKey.value = key
  confirming.value = false
  error.value = ''
}

function invalidatePreview() {
  preview.value = null
  balanceConfirming.value = false
  balanceError.value = ''
}

function onGbInput() {
  inputError.value = ''
  invalidatePreview()
}

function pickChip(g: number) {
  gbInput.value = String(g)
  inputError.value = ''
  invalidatePreview()
}

function showPreview() {
  balanceError.value = ''
  const gb = inputGb.value
  if (gb === null || gb < MIN_GB) {
    inputError.value = `请输入不小于 ${MIN_GB} 的整数 GB`
    preview.value = null
    return
  }
  inputError.value = ''
  preview.value = {
    gb,
    price: ((gb / 100) * pricePer100Gb.value).toFixed(2),
  }
  balanceConfirming.value = false
}

function close() {
  if (submitting.value || balanceSubmitting.value) return // 提交在途不许关，防结果丢失
  emit('update:open', false)
}

async function onConfirmClick() {
  if (!selectedPkg.value || submitting.value) return
  if (!confirming.value) {
    confirming.value = true
    return
  }
  submitting.value = true
  error.value = ''
  try {
    const res = await exchangeTimeForTraffic(selectedPkg.value.key, idempotencyKey.value)
    toast.success(
      res.replayed
        ? '这次兑换已经生效过了，没有重复扣时长'
        : `已兑换${selectedPkg.value.name}：扣除 ${res.log.days_deducted} 天，抵扣 ${formatBytes(res.log.bytes_credited)} 流量`,
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

/** 余额购流量成交：预览区两步确认 → 同一幂等键提交，成功回抛
 * 最新流量汇总给父级刷新；余额不足由差额明示＋按钮禁用＋后端
 * 双重拦截，失败文案走后端 detail。 */
async function onBalanceConfirmClick() {
  if (!preview.value || balanceSubmitting.value || previewShortfall.value > 0) return
  if (!balanceConfirming.value) {
    balanceConfirming.value = true
    return
  }
  balanceSubmitting.value = true
  balanceError.value = ''
  try {
    const res = await buyBalanceTraffic(preview.value.gb, idempotencyKey.value)
    toast.success(
      res.replayed
        ? '这次购买已经生效过了，没有重复扣款'
        : `已购买 ${preview.value.gb}G 流量，扣款 $${res.paid_amount}，余额剩 $${res.balance_after}`,
    )
    emit('exchanged', res.traffic)
    emit('update:open', false)
  } catch (e) {
    balanceConfirming.value = false
    balanceError.value = toUserMessage(e)
  } finally {
    balanceSubmitting.value = false
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
  background: var(--tq-bg);
  color: var(--tq-fg);
  border: 1px solid var(--tq-border);
  border-bottom: none;
  border-radius: 22px 22px 0 0;
  padding: 20px 20px 28px;
  box-shadow: var(--tq-shadow);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
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
  .tq-modal { border-radius: 22px; border-bottom: 1px solid var(--tq-border); }
}

/* 头部：40px 琥珀图标 + 标题 17 粗 / 副标题 12 灰 + 右上 × */
.tq-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.tq-head__icon {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--tq-icon-bg);
  color: var(--tq-icon-fg);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tq-head__text { flex: 1; min-width: 0; }
.tq-title { font-size: 17px; font-weight: 700; margin: 0; line-height: 1.25; }
.tq-sub { font-size: 12px; color: var(--tq-dim); margin: 2px 0 0; }
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

/* 当前流量卡（圆角 16、淡填充） */
.tq-card {
  margin-top: 16px;
  background: var(--tq-card);
  border-radius: 16px;
  padding: 14px 16px;
}
.tq-card__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.tq-card__label { font-size: 13.5px; color: var(--tq-dim); }
.tq-card__pct { font-size: 15px; font-weight: 700; }
.tq-card__used { font-size: 12.5px; color: var(--tq-dim); margin: 8px 0 0; }

/* 分段页签（胶囊容器） */
.tq-tabs {
  display: flex;
  gap: 4px;
  margin: 16px 0 4px;
  background: var(--tq-seg);
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
.tq-tab--on { background: var(--tq-seg-on); opacity: 1; box-shadow: 0 1px 6px rgba(0, 0, 0, 0.14); }
.tq-body { padding-top: 12px; }

/* 余额购买 */
.tq-balrow {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.tq-balrow__label { font-size: 14px; }
.tq-balrow__amt { font-size: 15px; font-weight: 700; color: var(--tq-green); }
.tq-label { font-size: 13px; color: var(--tq-dim); margin: 16px 0 8px; }
.tq-inputrow { display: flex; gap: 10px; }
.tq-input {
  flex: 1;
  display: flex;
  align-items: center;
  background: var(--tq-card);
  border: 1px solid var(--tq-border);
  border-radius: 12px;
  padding: 0 14px;
  height: 46px;
  min-width: 0;
}
.tq-input__field {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-size: 16px;
  font-weight: 600;
  outline: none;
}
.tq-input__suffix { font-size: 13px; color: var(--tq-dim); flex: none; }
/* 预览钮：彩虹边 + 玻璃（与站内 .btn-primary 同一套配方） */
.tq-preview-btn {
  flex: none;
  height: 46px;
  padding: 0 22px;
  border-radius: 999px;
  border: 1.5px solid transparent;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  color: #ffffff;
  font-size: 14.5px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22);
}
html[data-theme='light'] .tq-preview-btn {
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
}
.tq-preview-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tq-chips { display: flex; gap: 8px; margin-top: 12px; }
.tq-chip {
  flex: 1;
  border: 1px solid var(--tq-border);
  background: transparent;
  border-radius: 999px;
  padding: 7px 0;
  font-size: 13px;
  font-weight: 500;
  color: inherit;
  cursor: pointer;
}
.tq-chip--on { background: var(--tq-seg-on); border-color: transparent; font-weight: 700; }
.tq-preview {
  margin-top: 16px;
  background: var(--tq-card);
  border-radius: 16px;
  padding: 14px 16px;
}
.tq-preview__line { font-size: 14px; margin: 0; }
.tq-preview__note { font-size: 12.5px; color: var(--tq-dim); margin: 6px 0 0; line-height: 1.5; }

/* 时间换流量包卡（圆角 16、淡填充；选中态橙边） */
.tq-opts { display: flex; flex-direction: column; gap: 10px; }
.tq-pkg {
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: left;
  width: 100%;
  border: 1.5px solid transparent;
  background: var(--tq-card);
  border-radius: 16px;
  padding: 16px;
  cursor: pointer;
  color: inherit;
}
.tq-pkg--on { border-color: #f2762e; }
.tq-pkg__icon {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--tq-seg);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tq-pkg__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tq-pkg__name { font-size: 15.5px; font-weight: 700; }
.tq-pkg__desc { font-size: 12.5px; color: var(--tq-dim); line-height: 1.5; }
.tq-pkg__warn { font-size: 12.5px; color: var(--tq-orange); }
.tq-pkg__side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.tq-pkg__days { font-size: 20px; font-weight: 800; letter-spacing: -0.01em; }
.tq-pkg__cost { font-size: 11px; color: var(--tq-dim); }

/* 琥珀提示条 */
.tq-hint {
  display: flex;
  gap: 6px;
  margin: 14px 0 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--tq-hint-fg);
  background: var(--tq-hint-bg);
  border-radius: 12px;
  padding: 10px 12px;
}
.tq-hint__icon { flex: none; margin-top: 2px; }

.tq-error { color: #dc2626; font-size: 13px; margin: 12px 0 0; }
html:not([data-theme='light']) .tq-error { color: #f87171; }

/* 确认主按钮：彩虹边 + 玻璃（与站内 .btn-primary 同一套配方） */
.tq-confirm {
  width: 100%;
  margin-top: 16px;
  border: 1.5px solid transparent;
  border-radius: 999px;
  padding: 13px 0;
  font-size: 15px;
  font-weight: 700;
  color: #ffffff;
  cursor: pointer;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -12px rgba(0, 0, 0, 0.55);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: transform 0.12s ease, opacity 0.12s ease;
}
html[data-theme='light'] .tq-confirm {
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-shadow-card-l);
}
.tq-preview .tq-confirm { margin-top: 12px; }
.tq-confirm:disabled { opacity: 0.45; cursor: not-allowed; }
/* 按压反馈态（app 口径：点下去立刻有反应） */
.tq-confirm:active:not(:disabled),
.tq-pkg:active:not(:disabled),
.tq-tab:active,
.tq-chip:active,
.tq-preview-btn:active:not(:disabled),
.tq-close:active {
  transform: scale(0.97);
  opacity: 0.88;
}
.tq-pkg, .tq-tab, .tq-chip, .tq-preview-btn, .tq-close {
  transition: transform 0.12s ease, opacity 0.12s ease;
}
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
