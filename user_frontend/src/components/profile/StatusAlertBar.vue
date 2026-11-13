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
 * - 活力低于可播下限（服务端 limit_threshold，缺省 3）：红色预警，
 *   写清暂时看不了片，并给恢复路（今天看片自动回 1 点）。
 * - 活力刚过下限 1 点内（默认 3–4）：黄色提醒，趁早补。
 * - 公益资格／付费会员 7 天内到期：黄色提醒到期日与续期入口（会员页
 *   现行续期／续费口径，不在这里新造逻辑）。
 * 任一数据拿不到就不出对应条，全部不触发时整块不渲染。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { TriangleAlert } from 'lucide-vue-next'
import type { MySubscription } from '@/api'
import type { VitalityStatus } from '@/api/economy'

interface WelfareState {
  is_welfare: boolean
  expires_at: string | null
}

const props = defineProps<{
  isFreeRealm: boolean
  vitality: VitalityStatus | null
  welfare: WelfareState | null
  activeSub: MySubscription | null
}>()

interface AlertItem {
  key: string
  level: 'danger' | 'warn'
  title: string
  text: string
  action?: { to: string; label: string }
}

/** 距到期日的日历天数（按本地日期对齐，今天到期 = 0）；解析不了返回无穷大（不出提醒） */
function daysUntil(iso: string): number {
  const end = new Date(iso)
  if (Number.isNaN(end.getTime())) return Number.POSITIVE_INFINITY
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  return Math.round((startOfDay(end) - startOfDay(new Date())) / 86400000)
}

const alerts = computed<AlertItem[]>(() => {
  const out: AlertItem[] = []
  // ── 活力（仅公益服；非公益服 vitality 恒为 null 自然不出） ──
  const vit = props.isFreeRealm ? props.vitality : null
  if (vit) {
    const limit = vit.limit_threshold || 3
    if (vit.vitality < limit) {
      out.push({
        key: 'vitality-danger',
        level: 'danger',
        title: '活力不足，暂时看不了片',
        text: `当前活力 ${vit.vitality} 点，低于 ${limit} 点就播不了。今天看片能自动回 1 点。`,
      })
    } else if (vit.vitality <= limit + 1) {
      out.push({
        key: 'vitality-warn',
        level: 'warn',
        title: '活力偏低',
        text: `当前活力 ${vit.vitality} 点，再掉就看不了片了。今天记得看片回 1 点。`,
      })
    }
  }
  // ── 到期（7 天内） ──
  if (props.isFreeRealm) {
    const w = props.welfare
    if (w?.is_welfare && w.expires_at) {
      const days = daysUntil(w.expires_at)
      if (days >= 0 && days <= 7) {
        out.push({
          key: 'welfare-expiry',
          level: 'warn',
          title: days === 0 ? '公益资格今天到期' : `公益资格还有 ${days} 天到期`,
          text: `资格到 ${w.expires_at.slice(0, 10)} 为止，断了就看不了片，趁现在续上。`,
          action: { to: '/game/account/member', label: '去续期' },
        })
      }
    }
  } else if (props.activeSub && props.activeSub.days_left <= 7) {
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
