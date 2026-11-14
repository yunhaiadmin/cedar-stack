<template>
  <div class="v4-scope">
    <div class="v4-page">
      <!-- 顶栏 -->
      <div class="v4-col-top">
        <h1>收藏家</h1>
        <span v-if="collector.loading" class="v4-skeleton" style="height:24px;width:110px;border-radius:14px"></span>
      </div>

      <!-- 顶层错误卡：失败时各区收起，只留这一张 -->
      <div v-if="collector.error" class="v4-sec">
        <div class="v4-err" role="alert">收藏家没加载出来：{{ collector.error }}<br><button class="v4-retry" :disabled="collector.loading" @click="retryCollector">{{ collector.loading ? '正在重试…' : '重试' }}</button></div>
      </div>

      <!-- 收藏家卡 -->
      <div v-if="!collector.error" class="v4-idcard">
        <span class="v4-idcard-mark" aria-hidden="true">收藏家</span>
        <div v-if="collector.loading" class="v4-skeleton" style="height:110px"></div>
        <template v-else>
          <div class="v4-id-name">{{ nickname }}</div>
          <!-- R41：口味数据没拿到（非加载态的静默失败）时给重试入口，调 loadCollector(true) 强制刷新 -->
          <div class="v4-id-title">{{ tasteTitle }}<button v-if="!tastesRaw" class="v4-retry" @click="retryCollector">重试</button></div>
          <div class="v4-id-stats">
            <div><b>{{ watchHoursText }}</b><span>累计观影</span></div>
          </div>
        </template>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { usePanelStore } from '@/stores/panel'
import { useUserStore } from '@/stores/user'
import type { CollectorData, TasteStamp } from '@/composables/usePanelApi'

const panel = usePanelStore()
const user = useUserStore()
const { collector } = storeToRefs(panel)

onMounted(() => {
  void panel.loadCollector()
})

/** 错误卡重试 */
function retryCollector() {
  void panel.loadCollector(true)
}

/* ---------- 数据解包 ---------- */
const data = computed((): CollectorData => (collector.value.data || {}) as CollectorData)

const nickname = computed(() => {
  const n = data.value.nickname as string | undefined
  if (n) return n
  const u = user.user as Record<string, unknown> | null
  return (u?.nickname as string) || (u?.username as string) || '探险家'
})

const watchHoursText = computed(() => {
  const h = data.value.watch_hours
  if (typeof h !== 'number') return '–'
  return h >= 1 ? `${Math.round(h)}小时` : `${Math.round(h * 60)}分钟`
})

/* ---------- 口味标题：取已集的前两个 ---------- */
const tastesRaw = computed((): Record<string, TasteStamp> | undefined => {
  const t = data.value.tastes as Record<string, TasteStamp> | undefined
  return t && typeof t === 'object' ? t : undefined
})
const tasteTitle = computed(() => {
  const t = tastesRaw.value
  if (!t) return '类型数据没拿到'
  const got = Object.values(t).filter((s) => s?.stamped).map((s) => s.name)
  if (got.length === 0) return '还没有点亮任何类型，去看片吧'
  return `类型：${got.slice(0, 2).join(' / ')}${got.length > 2 ? ` 等 ${got.length} 种` : ''}`
})

</script>

<style scoped>
/* 取自设计稿 .top / .idcard / .stats */
.v4-col-top {
  padding: 26px 20px 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.v4-col-top h1 {
  font-size: 20px;
  font-weight: 500;
  letter-spacing: 0;
  margin: 0;
}
.v4-idcard {
  margin: 12px 20px 0;
  background: var(--paper-card);
  border: 1.5px solid var(--ink);
  border-radius: 14px;
  padding: 18px;
  position: relative;
  overflow: hidden;
}
.v4-idcard-mark {
  position: absolute;
  right: 14px;
  top: 14px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ink2);
  letter-spacing: 0;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 2px 6px;
  pointer-events: none;
}
.v4-id-name {
  font-size: 22px;
  font-weight: 800;
  padding-right: 72px;
}
.v4-id-title {
  font-size: 12px;
  color: var(--ink2);
  margin-top: 2px;
}
.v4-id-stats {
  display: flex;
  margin-top: 14px;
  border-top: 1px dotted var(--line);
  padding-top: 12px;
}
.v4-id-stats div {
  flex: 1;
  min-width: 0;
  text-align: center;
}
.v4-id-stats b {
  font-size: 18px;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.v4-id-stats span {
  font-size: 11px;
  color: var(--ink2);
}
.v4-id-stats div + div {
  border-left: 1px dotted var(--line);
}
</style>
