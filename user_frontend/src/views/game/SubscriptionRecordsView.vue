<template>
  <div class="sr-page">
    <header class="sr-topbar">
      <RouterLink to="/game/account/menu" class="sr-back" aria-label="返回我的">
        <ChevronLeft :size="22" aria-hidden="true" />
        <span>订阅记录</span>
      </RouterLink>
      <RouterLink to="/library" class="sr-watch">
        <Play :size="15" class="sr-watch__icon" aria-hidden="true" />
        <span>网页观影</span>
      </RouterLink>
    </header>

    <main class="sr-body">
      <div class="sr-head">
        <h1 class="sr-title">订阅记录</h1>
        <button
          type="button"
          class="sr-refresh"
          aria-label="刷新记录"
          :disabled="loading"
          @click="load"
        >
          <RotateCw :size="19" aria-hidden="true" />
        </button>
      </div>
      <RouterLink to="/game/account/subscribe" class="sr-plans-btn">查看套餐</RouterLink>

      <div class="sr-divider" aria-hidden="true"></div>

      <div v-if="loadError" class="sr-err">
        {{ loadError }}<button type="button" class="sr-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="sr-skeleton" aria-hidden="true"></div>

      <p v-else-if="!records.length" class="sr-empty">
        还没有订阅记录。购买订阅、或用兑换码开通之后，每一笔都会记在这里，方便你随时对账。
      </p>

      <template v-else>
        <article v-for="(r, i) in pageRecords" :key="i" class="sr-entry">
          <div class="sr-entry__head">
            <span class="sr-entry__name">{{ r.name }}</span>
            <span class="sr-tag">订阅</span>
          </div>
          <p class="sr-entry__summary">{{ summaryLine(r) }}</p>
          <div class="sr-kv">
            <span class="sr-kv__label">时间</span>
            <span class="sr-kv__value sr-kv__value--time">{{ formatFull(r.time) }}</span>
          </div>
          <div class="sr-kv">
            <span class="sr-kv__label">到期</span>
            <span class="sr-kv__value">{{ formatMinute(r.expires_at) }}</span>
          </div>
        </article>

        <div class="sr-pager">
          <div class="sr-pager__info">
            <span>共 {{ records.length }} 条 · 第 {{ pageFrom }} 至 {{ pageTo }} 条</span>
            <span class="sr-pager__size-label">每页</span>
            <span class="sr-pager__size-wrap">
              <select v-model.number="pageSize" class="sr-pager__size" aria-label="每页条数">
                <option :value="10">10 条</option>
                <option :value="20">20 条</option>
                <option :value="50">50 条</option>
              </select>
              <ChevronDown :size="14" class="sr-pager__size-chev" aria-hidden="true" />
            </span>
          </div>
          <div class="sr-pager__ctrl">
            <button
              type="button" class="sr-pager__btn" aria-label="第一页"
              :disabled="page <= 1" @click="page = 1"
            >
              <ChevronsLeft :size="17" aria-hidden="true" />
            </button>
            <button
              type="button" class="sr-pager__btn" aria-label="上一页"
              :disabled="page <= 1" @click="page--"
            >
              <ChevronLeft :size="17" aria-hidden="true" />
            </button>
            <template v-for="(p, i) in pageWindow" :key="i">
              <span v-if="p === '…'" class="sr-pager__gap">…</span>
              <button
                v-else
                type="button"
                class="sr-pager__num"
                :class="{ 'sr-pager__num--cur': p === page }"
                :aria-current="p === page ? 'page' : undefined"
                @click="page = p"
              >{{ p }}</button>
            </template>
            <button
              type="button" class="sr-pager__btn" aria-label="下一页"
              :disabled="page >= totalPages" @click="page++"
            >
              <ChevronRight :size="17" aria-hidden="true" />
            </button>
            <button
              type="button" class="sr-pager__btn" aria-label="最后一页"
              :disabled="page >= totalPages" @click="page = totalPages"
            >
              <ChevronsRight :size="17" aria-hidden="true" />
            </button>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 订阅记录（/game/account/subscription-records）——2026-11-13 对齐友站
 * 「我的 · 订阅与财务」新增，2026-11-14 按用户发来的友站原图重做版式：
 * 大标题＋刷新、查看套餐胶囊、条目无卡片盒（套餐名＋「订阅」绿标签、
 * 支出行、时间/到期键值行）、底部分页。数据是购买订单与无订单发放
 * （兑换码/管理员授予）的合并时间线（后端 GET /api/user/subscriptions/records）；
 * 订单行展示下单时的 item_name 快照原文，套餐后来改名不影响历史对账。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Play,
  RotateCw,
} from 'lucide-vue-next'
import { subscriptionApi, type SubscriptionRecordRow } from '@/api'
import './subscription-records.css'

const records = ref<SubscriptionRecordRow[]>([])
const loading = ref(true)
const loadError = ref('')
const page = ref(1)
const pageSize = ref(10)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(records.value.length / pageSize.value)),
)
const pageRecords = computed(() =>
  records.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value),
)
const pageFrom = computed(() =>
  records.value.length ? (page.value - 1) * pageSize.value + 1 : 0,
)
const pageTo = computed(() =>
  Math.min(records.value.length, page.value * pageSize.value),
)
/** 页码窗口：总数不大时全列，多了收成 1 … 前后各一 … 末页 */
const pageWindow = computed<(number | '…')[]>(() => {
  const total = totalPages.value
  const cur = page.value
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const keep = [...new Set([1, 2, cur - 1, cur, cur + 1, total - 1, total])]
    .filter((n) => n >= 1 && n <= total)
    .sort((a, b) => a - b)
  const out: (number | '…')[] = []
  keep.forEach((n, i) => {
    if (i && n - keep[i - 1] > 1) out.push('…')
    out.push(n)
  })
  return out
})

watch(pageSize, () => {
  page.value = 1
})
watch(totalPages, (t) => {
  if (page.value > t) page.value = t
})

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** 时间：2026/11/13 01:27:38（斜杠式、到秒，与友站原图同格式） */
function formatFull(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 到期：2027/02/13 01:27（到分钟） */
function formatMinute(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 时长人话：90 天 → 3 个月；365 天 → 1 年；其余原样 N 天 */
function durationLabel(days: number | null): string | null {
  if (!days) return null
  if (days >= 36500) return '永久'
  if (days % 365 === 0) return `${days / 365} 年`
  if (days % 30 === 0) return `${days / 30} 个月`
  return `${days} 天`
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待支付',
  paid: '已支付',
  refunded: '已退款',
  closed: '已关闭',
  active: '生效中',
  expired: '已过期',
  cancelled: '已终止',
}

/** 第二行：支出/状态＋时长。金额与时长都是后端真实数据，不编造 */
function summaryLine(r: SubscriptionRecordRow): string {
  const dur = durationLabel(r.days)
  const money = r.amount != null ? `¥${r.amount.toFixed(2)}` : null
  const parts: string[] = []
  if (r.kind === 'order') {
    if (r.status === 'paid') parts.push(money ? `支出 ${money}` : '已支付')
    else if (r.status === 'pending') parts.push(money ? `待支付 ${money}` : '待支付')
    else if (r.status === 'refunded') parts.push(money ? `已退款 ${money}` : '已退款')
    else if (r.status === 'closed') parts.push('已关闭', ...(money ? [money] : []))
    else parts.push(STATUS_LABEL[r.status] || r.status, ...(money ? [money] : []))
  } else {
    parts.push('订阅开通')
    if (r.status === 'expired') parts.push('已过期')
    if (r.status === 'cancelled') parts.push('已终止')
  }
  if (dur) parts.push(dur)
  return parts.join(' · ')
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await subscriptionApi.getRecords()
    records.value = res.records || []
    page.value = 1
  } catch {
    loadError.value = '订阅记录加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
