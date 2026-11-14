<template>
  <div v-if="alerts.length" class="sa-stack">
    <div v-for="a in alerts" :key="a.key" class="sa-bar" :class="a.level">
      <TriangleAlert class="sa-ico" :size="17" />
      <div class="sa-main">
        <div class="sa-title">{{ a.title }}</div>
        <p class="sa-text">{{ a.text }}</p>
        <div v-if="a.action" class="sa-actions">
          <RouterLink class="sa-btn" :to="a.action.to">{{ a.action.label }}</RouterLink>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 「我的」页状态预警条：只吃父页（PersonalCenterView）已经加载好的账号
 * 数据，不自己发请求。触发口径：
 * - 付费会员 7 天内到期：黄色提醒到期日与续费入口（会员页现行续费
 *   口径，不在这里新造逻辑）。
 * 活力预警与公益资格到期提醒已随活力/公益资格整族于 2026-11-14 删除。
 * 数据拿不到就不出条，不触发时整块不渲染。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { TriangleAlert } from 'lucide-vue-next'
import type { MySubscription } from '@/api'

const props = defineProps<{
  isFreeRealm: boolean
  activeSub: MySubscription | null
}>()

interface AlertItem {
  key: string
  level: 'danger' | 'warn'
  title: string
  text: string
  action?: { to: string; label: string }
}

const alerts = computed<AlertItem[]>(() => {
  const out: AlertItem[] = []
  // ── 订阅到期（7 天内） ──
  if (!props.isFreeRealm && props.activeSub && props.activeSub.days_left <= 7) {
    // 公益服下 legacy 订阅记录不作数（与会员页同款设门）：只在付费服提醒
    const sub = props.activeSub
    out.push({
      key: 'sub-expiry',
      level: 'warn',
      title: `会员还有 ${sub.days_left} 天到期`,
      text: `${sub.plan_name} 到 ${sub.end_date.slice(0, 10)} 为止，续上可无缝接续。`,
      action: { to: '/game/account/member', label: '去续费' },
    })
  }
  return out
})
</script>

<style scoped>
.sa-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
}
.sa-bar {
  display: flex;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--paper-r-lg, 12px);
  background: var(--paper-card);
}
.sa-bar.danger {
  background: var(--paper-danger-soft);
  border-color: var(--paper-danger-soft);
  border-left: 3px solid var(--red);
}
.sa-bar.warn {
  background: var(--paper-warning-soft);
  border-color: var(--paper-warning-soft);
  border-left: 3px solid var(--paper-warning);
}
.sa-ico { flex: none; margin-top: 1px; }
.sa-bar.danger .sa-ico { color: var(--red); }
.sa-bar.warn .sa-ico { color: var(--paper-warning); }
.sa-main { flex: 1; min-width: 0; }
.sa-title { font-size: 14px; font-weight: 800; }
.sa-bar.danger .sa-title { color: var(--red-ink); }
.sa-text {
  margin: 3px 0 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--ink2);
}
.sa-actions { margin-top: 8px; }
.sa-btn {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  border: 1.5px solid var(--au-primary, #016a71);
  border-radius: var(--paper-r-full, 999px);
  color: var(--au-primary, #016a71);
  font-size: 12.5px;
  font-weight: 700;
  text-decoration: none;
}
</style>
