<template>
  <div class="sr-page">
    <header class="sr-topbar">
      <RouterLink to="/game/account/menu" class="sr-back" aria-label="返回我的">
        <ChevronLeft :size="20" aria-hidden="true" />
        <span>订阅记录</span>
      </RouterLink>
    </header>

    <main class="sr-body">
      <div v-if="loadError" class="sr-err">
        {{ loadError }}<button type="button" class="sr-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="sr-skeleton" aria-hidden="true"></div>
      <p v-else-if="!records.length" class="sr-empty">
        还没有订阅记录。购买订阅、或用兑换码开通之后，每一笔都会记在这里，方便你随时对账。
      </p>
      <ul v-else class="sr-list">
        <li v-for="(r, i) in records" :key="i" class="sr-row">
          <div class="sr-row__main">
            <p class="sr-name">{{ r.name }}</p>
            <p class="sr-meta">{{ metaLine(r) }}</p>
          </div>
          <span class="sr-amount">{{ r.amount != null ? `¥${r.amount.toFixed(2)}` : '—' }}</span>
        </li>
      </ul>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 订阅记录（/game/account/subscription-records）——2026-11-13 对齐友站
 * 「我的 · 订阅与财务」新增：购买订单与无订单发放（兑换码/管理员授予）
 * 的合并时间线。订单行展示下单时的 item_name 快照原文（后端保证），
 * 套餐后来改名不影响历史对账。
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronLeft } from 'lucide-vue-next'
import { subscriptionApi, type SubscriptionRecordRow } from '@/api'
import './subscription-records.css'

const records = ref<SubscriptionRecordRow[]>([])
const loading = ref(true)
const loadError = ref('')

const STATUS_TEXT: Record<string, string> = {
  pending: '待支付',
  paid: '已支付',
  refunded: '已退款',
  closed: '已关闭',
  active: '生效中',
  expired: '已过期',
  cancelled: '已终止',
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function daysText(r: SubscriptionRecordRow): string {
  if (r.days == null) return ''
  if (r.days >= 36500) return '永久'
  return `${r.days} 天`
}

function metaLine(r: SubscriptionRecordRow): string {
  return [formatTime(r.time), daysText(r), STATUS_TEXT[r.status] || r.status]
    .filter(Boolean)
    .join(' · ')
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await subscriptionApi.getRecords()
    records.value = res.records || []
  } catch {
    loadError.value = '订阅记录加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
