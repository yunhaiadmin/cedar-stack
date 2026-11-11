<script setup lang="ts">
/**
 * 补给站 · 盲盒卡片（PR-2）：档位与概率全公布、抽盒、开盒结果、
 * 我的记录、我的卡包。
 *
 * 零感知口径：总开关关 / 该服未开启 / 没配档位 / 接口失败时整块
 * 不渲染，不给补给站留空位。数据走 /api/user/blindbox/*（与机器
 * 人面板同一套引擎与账）。抽中后把新余额 emit 给补给站刷新钱包。
 */
import { onMounted, ref } from 'vue'
import { useToast } from '@/composables/useToast'
import { toUserMessage } from '@/utils/toUserMessage'
import {
  drawBlindbox,
  fetchBlindboxMyCodes,
  fetchBlindboxMyDraws,
  fetchBlindboxTiers,
  type BlindboxCodeItem,
  type BlindboxDrawRecord,
  type BlindboxDrawResponse,
  type BlindboxPrizeKind,
  type BlindboxTiersResponse,
} from '@/api/blindbox'

const emit = defineEmits<{ drawn: [balance: number | null] }>()

const toast = useToast()

const payload = ref<BlindboxTiersResponse | null>(null)
const visible = ref(false)
const drawingKey = ref<string | null>(null)
const lastResult = ref<BlindboxDrawResponse | null>(null)
const lastTierName = ref('')

const showDraws = ref(false)
const myDraws = ref<BlindboxDrawRecord[]>([])
const drawsLoaded = ref(false)
const showCodes = ref(false)
const myCodes = ref<BlindboxCodeItem[]>([])
const codesLoaded = ref(false)

function prizeLabel(kind: BlindboxPrizeKind, params?: { amount?: number; days?: number }): string {
  const p = params ?? {}
  switch (kind) {
    case 'points': return `${p.amount ?? 0} 能量`
    case 'vitality': return `${p.amount ?? 0} 活力`
    case 'access_days': return `${p.days ?? 0} 天资格/订阅`
    case 'reg_code': return `注册码（${p.days ?? 0} 天）`
    case 'sub_code': return `订阅码（${p.days ?? 0} 天）`
    case 'whitelist': return '白名单'
    default: return kind
  }
}

function pct(frac: number): string {
  const text = (frac * 100).toFixed(2).replace(/\.?0+$/, '')
  return `${text}%`
}

function fmtTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function load() {
  try {
    const res = await fetchBlindboxTiers()
    payload.value = res
    visible.value = Boolean(res?.enabled && res.tiers?.length)
  } catch {
    // 未开启/未配置/失败都不渲染（零感知），不打扰补给站其它区块
    visible.value = false
  }
}

async function doDraw(tierKey: string, tierName: string) {
  if (drawingKey.value) return
  drawingKey.value = tierKey
  try {
    const res = await drawBlindbox(tierKey)
    lastResult.value = res
    lastTierName.value = tierName
    drawsLoaded.value = false
    codesLoaded.value = false
    if (showDraws.value) void loadDraws()
    if (showCodes.value) void loadCodes()
    emit('drawn', res.balance)
    toast.success(`开出：${prizeLabel(res.prize.kind, res.prize.params)}`)
  } catch (err) {
    toast.error(toUserMessage(err))
  } finally {
    drawingKey.value = null
  }
}

async function loadDraws() {
  try {
    const res = await fetchBlindboxMyDraws(20)
    myDraws.value = res.items
    drawsLoaded.value = true
  } catch {
    myDraws.value = []
    drawsLoaded.value = true
  }
}

async function loadCodes() {
  try {
    const res = await fetchBlindboxMyCodes()
    myCodes.value = res.items
    codesLoaded.value = true
  } catch {
    myCodes.value = []
    codesLoaded.value = true
  }
}

function toggleDraws() {
  showDraws.value = !showDraws.value
  if (showDraws.value && !drawsLoaded.value) void loadDraws()
}

function toggleCodes() {
  showCodes.value = !showCodes.value
  if (showCodes.value && !codesLoaded.value) void loadCodes()
}

async function copyCode(code: string) {
  try {
    await navigator.clipboard.writeText(code)
    toast.success('卡码已复制：' + code)
  } catch {
    toast.info('卡码：' + code + '（请手动复制）')
  }
}

function codeTypeLabel(codeType: number): string {
  return { 1: '注册码', 2: '订阅码', 3: '白名单码' }[codeType] ?? '卡码'
}

function statusLabel(status: string): string {
  if (status === 'delivered') return '已发放'
  if (status === 'delivery_failed') return '发放失败，请联系管理员'
  if (status === 'failed') return '失败（未扣能量）'
  return status
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section v-if="visible && payload" class="shop-sec bb-sec">
    <div class="v4-sec-title">盲盒</div>
    <div class="v4-sec-sub">
      概率全公布，开盒即到账
      <template v-if="payload.daily_spend_cap > 0">
        · 每日上限 {{ payload.daily_spend_cap }} 能量
      </template>
      <template v-if="payload.whitelist_stock_remaining !== null">
        · 白名单剩 {{ payload.whitelist_stock_remaining }} 个
      </template>
    </div>

    <div v-if="lastResult" class="bb-result" role="status">
      <div class="bb-result-head">🎁 {{ lastTierName }} · 开盒结果</div>
      <div class="bb-result-prize">
        {{ prizeLabel(lastResult.prize.kind, lastResult.prize.params) }}
      </div>
      <div v-if="lastResult.prize.fallback_from" class="bb-result-note">
        原奖库存/预算不足，已按规则兜底改发（不会吞奖）
      </div>
      <div v-if="lastResult.prize.code" class="bb-code-row">
        <code>{{ lastResult.prize.code }}</code>
        <button type="button" @click="copyCode(lastResult.prize.code)">复制</button>
      </div>
      <div class="bb-result-note">
        本盒 {{ lastResult.price }} 能量
        <template v-if="lastResult.balance !== null">
          · 剩余 {{ lastResult.balance }} 能量
        </template>
      </div>
    </div>

    <div class="bb-shelf">
      <div v-for="tier in payload.tiers" :key="tier.key" class="bb-card">
        <div class="bb-name">{{ tier.name }}</div>
        <div class="bb-price">{{ tier.price }} <small>能量</small></div>
        <div class="bb-rate">期望返还 {{ pct(tier.expected_return_rate) }}</div>
        <ul class="bb-prizes">
          <li v-for="(prize, i) in tier.prizes" :key="i">
            <span>{{ prizeLabel(prize.kind, prize.params) }}</span>
            <span class="bb-prob">{{ pct(prize.probability) }}</span>
          </li>
        </ul>
        <div class="bb-fallback">
          兜底：{{ prizeLabel(tier.fallback_prize.kind, tier.fallback_prize.params) }}
          （库存/预算不足时改发）
        </div>
        <button
          type="button"
          class="bb-buy"
          :disabled="drawingKey !== null"
          :aria-busy="drawingKey === tier.key"
          @click="doDraw(tier.key, tier.name)"
        >
          {{ drawingKey === tier.key ? '开盒中…' : `抽一盒 · ${tier.price} 能量` }}
        </button>
      </div>
    </div>

    <div class="bb-links">
      <button type="button" class="bb-link" @click="toggleCodes">
        🎒 我的卡包{{ showCodes ? '（收起）' : '' }}
      </button>
      <button type="button" class="bb-link" @click="toggleDraws">
        📜 我的记录{{ showDraws ? '（收起）' : '' }}
      </button>
    </div>

    <div v-if="showCodes" class="bb-list">
      <div v-if="!codesLoaded" class="bb-empty">加载中…</div>
      <div v-else-if="!myCodes.length" class="bb-empty">还没有从盲盒赢到卡码</div>
      <div v-for="c in myCodes" :key="c.id" class="bb-list-row">
        <span class="bb-list-main">
          {{ codeTypeLabel(c.code_type) }}
          <code>{{ c.code }}</code>
        </span>
        <span class="bb-list-side">
          {{ c.used ? '已使用' : '未使用' }}
          <button type="button" class="bb-mini" @click="copyCode(c.code)">复制</button>
        </span>
      </div>
      <div v-if="myCodes.length" class="bb-list-note">卡码在本页「核销中心」使用</div>
    </div>

    <div v-if="showDraws" class="bb-list">
      <div v-if="!drawsLoaded" class="bb-empty">加载中…</div>
      <div v-else-if="!myDraws.length" class="bb-empty">还没有抽过盒</div>
      <div v-for="r in myDraws" :key="r.id" class="bb-list-row">
        <span class="bb-list-main">
          {{ r.tier_name }} ·
          {{ prizeLabel(r.prize.kind, r.prize.params) }}
          <template v-if="r.prize.fallback_from">（兜底改发）</template>
        </span>
        <span class="bb-list-side">
          {{ statusLabel(r.status) }} · {{ fmtTime(r.created_at) }}
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 与补给站同一套纸色语言（.shop-sec / .buy 口径在本组件内自带，
   补给站的 scoped 样式不外溢） */
.bb-sec {
  padding: 20px 20px 0;
}

.bb-shelf {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

@media (max-width: 560px) {
  .bb-shelf {
    grid-template-columns: 1fr;
  }
}

.bb-card {
  border: 1.5px solid var(--ink);
  border-radius: var(--paper-r-lg, 12px);
  padding: 14px;
  background: var(--paper2);
  display: flex;
  flex-direction: column;
}

.bb-name {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.5px;
}

.bb-price {
  font-size: 22px;
  font-weight: 800;
  margin-top: 4px;
}

.bb-price small {
  font-size: 12px;
  font-weight: 600;
}

.bb-rate {
  font-size: 12px;
  opacity: 0.75;
  margin-top: 2px;
}

.bb-prizes {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  font-size: 13px;
}

.bb-prizes li {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 3px 0;
  border-bottom: 1px dashed rgba(0, 0, 0, 0.12);
}

.bb-prob {
  font-weight: 700;
  white-space: nowrap;
}

.bb-fallback {
  font-size: 12px;
  opacity: 0.7;
  margin-top: 8px;
}

.bb-buy {
  display: block;
  width: 100%;
  min-height: 44px;
  margin: 12px 0 2px;
  background: var(--ink);
  color: var(--paper);
  border: none;
  font-size: 14px;
  font-weight: 800;
  padding: 12px;
  border-radius: var(--paper-r-md, 10px);
  letter-spacing: 1px;
  transition: transform 0.12s;
  cursor: pointer;
}

.bb-buy:active {
  transform: scale(0.98);
}

.bb-buy:disabled {
  opacity: 0.55;
  cursor: default;
}

.bb-result {
  background: var(--ink);
  color: var(--paper2);
  border-radius: 14px;
  padding: 16px 18px;
  margin-bottom: 12px;
}

.bb-result-head {
  font-size: 13px;
  letter-spacing: 1px;
  opacity: 0.85;
}

.bb-result-prize {
  font-size: 20px;
  font-weight: 800;
  margin-top: 4px;
}

.bb-result-note {
  font-size: 12px;
  opacity: 0.8;
  margin-top: 6px;
}

.bb-code-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.bb-code-row code {
  font-size: 14px;
  letter-spacing: 1px;
  word-break: break-all;
}

.bb-code-row button,
.bb-mini {
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  border-radius: 8px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
}

.bb-links {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.bb-link {
  border: 1.5px solid var(--ink);
  background: transparent;
  color: var(--ink);
  border-radius: var(--paper-r-full, 999px);
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.bb-list {
  margin-top: 10px;
  border: 1.5px solid var(--ink);
  border-radius: var(--paper-r-lg, 12px);
  padding: 8px 14px;
  background: var(--paper2);
}

.bb-list-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  font-size: 13px;
  border-bottom: 1px dashed rgba(0, 0, 0, 0.12);
}

.bb-list-row:last-child {
  border-bottom: none;
}

.bb-list-main code {
  letter-spacing: 0.5px;
  word-break: break-all;
}

.bb-list-side {
  white-space: nowrap;
  opacity: 0.8;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.bb-list-note,
.bb-empty {
  font-size: 12px;
  opacity: 0.7;
  padding: 6px 0;
}
</style>
