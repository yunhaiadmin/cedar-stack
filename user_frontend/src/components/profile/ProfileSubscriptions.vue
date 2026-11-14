<script setup lang="ts">
import { computed } from 'vue'
import { TriangleAlert } from 'lucide-vue-next'
import EmptyState from '@/components/ui/EmptyState.vue'
import { useRouter } from 'vue-router'

export interface SubscriptionItem {
  id: number | string
  plan_name: string
  realm_name?: string
  start_date: string
  end_date: string
  status: string
  days_left?: number
  auto_renew?: boolean
}

/** 查看状态（取自账号卡 view_permission；资格与凭证合并后只展示状态、无购买动作） */
export interface ViewUnlockInfo {
  granted: boolean
  /** 已解锁的到期时间（ISO，未解锁/永久为 null） */
  expiresAt: string | null
  /** 放行来源：welfare＝随公益资格开通 / unlock＝存量凭证在效 / subscription＝付费订阅 */
  source: 'welfare' | 'unlock' | 'subscription' | null
}

const props = defineProps<{
  activeSub?: SubscriptionItem | null
  subscriptions: SubscriptionItem[]
  expiringSoon?: boolean
  isFreeRealm?: boolean
  realmNote?: string
  cancellingSub?: number | string | null
  togglingRenew?: number | string | null
  /** 公益资格是否生效（取自登录态 is_welfare） */
  welfareIsWelfare?: boolean
  /** 公益资格到期日（ISO；由福利状态口返回，续期成功后以服务端返回为准） */
  welfareExpiresAt?: string | null
  /** 福利状态口是否已成功返回（未返回时不显示到期／不过期后缀，避免闪现） */
  welfareStatusLoaded?: boolean
  /** 当前能量（取自账号卡 view_permission.points_balance，未取到为 null） */
  pointsBalance?: number | null
  /** 是否已绑 TG（null = 未知，按钮不拦、以服务端为准） */
  tgBound?: boolean | null
  viewUnlock?: ViewUnlockInfo | null
  /** 正在续期的公益天数档（7 或 30），无则 null */
  renewingWelfare?: number | null
}>()

const emit = defineEmits<{
  (e: 'cancel', sub: any): void
  (e: 'toggle-renew', sub: any): void
  (e: 'renew-welfare', days: 7 | 30): void
}>()

const router = useRouter()

/** 查看状态：unknown（无数据不展示）/ welfare（随资格开通）/ locked（未开通）/ permanent（存量永久）/ valid（存量在效）/ expired（存量已到期） */
const viewState = computed(() => {
  const v = props.viewUnlock
  if (!v) return 'unknown'
  if (v.source === 'welfare') return 'welfare'
  if (!v.granted) return 'locked'
  if (v.expiresAt === null) return 'permanent'
  return new Date(v.expiresAt).getTime() > Date.now() ? 'valid' : 'expired'
})

/** 查看到期日（yyyy-mm-dd）与续费价目（模板里直接取 prop 会被判可能为空，收到 computed 里收窄） */
const viewExpiresDate = computed(() => {
  const v = props.viewUnlock
  if (!v || v.expiresAt === null) return null
  return v.expiresAt.slice(0, 10)
})
/** 公益到期日（yyyy-mm-dd）：状态口未成功返回时为 null，不展示 */
const welfareExpiresDate = computed(() => {
  if (!props.welfareStatusLoaded || !props.welfareExpiresAt) return null
  return props.welfareExpiresAt.slice(0, 10)
})
</script>

<template>
  <!-- 平面分组（UHD 办事面口径）：无外层卡、无卡中卡；页题由布局 H1 承担，
       本组件不再自带「我的订阅」标题（与页题重复）。行间以 1px 发丝线分隔。 -->
  <section class="subs">
    <!-- 公益服下付费会员部分一律不渲染：转公益时 legacy 订阅记录保留至到期（权益不变），
         但 activeSub 仍为真时会抢在 v-else-if 的公益服块之前渲染，造成公益服下仍显示付费卡；
         此处以 isFreeRealm 设门，公益服下走下面的 sub-free 块（公益资格／查看凭证）。
         isFreeRealm 未传／非真值时按非公益服处理（fail-closed，信号缺失不误藏）。 -->
    <div v-if="activeSub && !isFreeRealm" class="sub-group">
      <div class="sub-row">
        <div class="sub-info">
          <div class="sub-plan">{{ activeSub.plan_name }}</div>
          <div class="sub-end">{{ activeSub.end_date.slice(0, 10) }} 到期 · 剩余 {{ activeSub.days_left }} 天</div>
          <p v-if="expiringSoon" class="sub-warn">
            <TriangleAlert :size="13" />
            即将到期，去订阅页续费后可无缝接续
          </p>
        </div>
        <span class="badge" :class="expiringSoon ? 'warn' : 'ok'">
          {{ expiringSoon ? '即将到期' : '生效中' }}
        </span>
      </div>
      <!-- r48-⑬：自动续订开关 —— 到期前 24 小时用能量按当前档位续订一期，
           能量不足则不续并站内信提醒；人民币自动扣款不开通
           公益服下与会员行一并隐藏（同上设门原因） -->
      <div class="sub-row renew-row">
        <div class="renew-info">
          <b>到期自动续订</b>
          <small>到期前 24 小时自动续订一期 · 余额不足则不续并提醒你</small>
        </div>
        <button
          type="button"
          class="renew-switch"
          :class="{ on: activeSub.auto_renew }"
          :disabled="togglingRenew === activeSub.id"
          :aria-pressed="!!activeSub.auto_renew"
          :aria-label="activeSub.auto_renew ? '关闭自动续订' : '开启自动续订'"
          @click="emit('toggle-renew', activeSub)"
        >
          <span class="knob" aria-hidden="true"></span>
        </button>
      </div>
    </div>
    <div v-else-if="isFreeRealm" class="sub-free">
      <span class="badge free">公益服 · 免费开放</span>
      <p class="pane-empty">{{ realmNote }}</p>
      <!-- 自助续期：公益天数走 /redeem；查看已随资格合并，只展示状态 -->
      <div class="renew-block">
        <div class="renew-block-title">公益资格</div>
        <p class="renew-line">{{ welfareIsWelfare ? '生效中' : '未开通' }}<template v-if="welfareExpiresDate"> · 到期 {{ welfareExpiresDate }}</template><template v-else-if="welfareStatusLoaded && welfareIsWelfare"> · 不过期</template><template v-if="pointsBalance != null"> · 当前余额 {{ pointsBalance }}</template></p>
        <p v-if="tgBound === false" class="renew-hint">续期需先绑定 Telegram，绑定后回来续期</p>
        <div class="renew-btns">
          <button
            v-for="days in ([7, 30] as const)"
            :key="days"
            type="button"
            class="btn btn-primary renew-btn"
            :disabled="renewingWelfare != null || tgBound === false"
            @click="emit('renew-welfare', days)"
          >{{ renewingWelfare === days ? '续期中…' : `续期 ${days} 天` }}</button>
          <button
            v-if="tgBound === false"
            type="button"
            class="btn renew-btn"
            @click="router.push('/tg-bind')"
          >去绑定 Telegram</button>
        </div>
      </div>
      <div v-if="viewUnlock" class="renew-block">
        <div class="renew-block-title">查看凭证</div>
        <p v-if="viewState === 'welfare'" class="renew-line">已随资格开通</p>
        <p v-else-if="viewState === 'permanent'" class="renew-line">已永久解锁（存量凭证）</p>
        <p v-else-if="viewState === 'valid'" class="renew-line">已解锁至 {{ viewExpiresDate }}（存量凭证）</p>
        <p v-else class="renew-line">{{ viewState === 'expired' ? '存量凭证已到期' : '未开通' }} · 续上公益资格即可看</p>
      </div>
    </div>
    <EmptyState
      v-else
      size="compact"
      title="还没有会员"
      description="开通后解锁全片库"
    >
      <template #action>
        <!-- P1-2：改用全局 .btn-primary（styles/index.css:160，琥珀实心 + --paper-on-primary 文，
             与旧 ProfileView .btn.primary 语义一致；.btn 基类自带 font-weight:600） -->
        <button class="btn btn-primary" @click="router.push('/game/account/subscribe')">查看会员方案</button>
      </template>
    </EmptyState>

    <!-- 历史账单／取消续订入口 —— 公益服下与会员行一并隐藏（含 active 行的“终止”按钮） -->
    <div v-if="subscriptions.length > 1 && !isFreeRealm" class="sub-history-block">
      <h3 class="subs-h">历史订阅</h3>
      <ul class="sub-history">
        <li v-for="s in subscriptions.slice(0, 4)" :key="s.id" class="sub-history-item">
          <div class="sub-history-top">
            <span class="sub-history-name">{{ s.plan_name }}<em v-if="s.realm_name" class="sub-realm">（{{ s.realm_name }}）</em></span>
            <span class="badge" :class="s.status === 'active' ? 'ok' : 'off'">
              {{ s.status === 'active' ? '生效中' : '已结束' }}
            </span>
            <button
              v-if="s.status === 'active'"
              type="button"
              class="text-btn danger"
              :disabled="cancellingSub === s.id"
              @click="emit('cancel', s)"
            >{{ cancellingSub === s.id ? '终止中…' : '终止' }}</button>
          </div>
          <div class="sub-history-date">{{ s.start_date.slice(0, 10) }} ~ {{ s.end_date.slice(0, 10) }}</div>
        </li>
        <!-- R32：历史记录只显示最近 4 条，达到上限时诚实告知（SupplyLogView 先例） -->
        <li v-if="subscriptions.length > 4" class="sub-history-foot">· 仅显示最近 4 条，还有 {{ subscriptions.length - 4 }} 条 ·</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
/* 平面分组（UHD 办事面口径，2026-11-13 会员页重做）：无外层卡、无卡中卡，
   行间 1px 发丝线（--line）分隔；令牌只用 .uhd-account 已覆盖的
   --ink/--ink2/--line 一套，不再混用旧纸色盒子底（--paper-card-2 等）。 */
.subs { min-width: 0; }
.sub-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 14px 0;
}
.sub-row + .sub-row { border-top: 1px solid var(--line); }
/* 信息列可缩：先让文字列让位，徽章永不被挤压换行 */
.sub-info { min-width: 0; }
/* P3-13：等宽数字（倒计时不抖动） */
.sub-plan { font-size: 16px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.sub-end { margin-top: 2px; font-size: 13px; color: var(--ink2); font-variant-numeric: tabular-nums; }
.sub-warn { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--paper-warning); margin: 6px 0 0; }
/* 状态徽章：定宽不缩、文字不换行（旧版无此二条，被信息列挤成竖排溢出卡外） */
.badge {
  flex: none;
  white-space: nowrap;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.5;
}
.badge.ok { background: var(--paper-success-soft); color: var(--paper-success); }
.badge.warn { background: var(--paper-warning-soft); color: var(--paper-warning); }
.badge.free { background: var(--paper-primary-soft); color: var(--paper-primary); }
.badge.off { background: var(--paper2); color: var(--ink2); }
/* 分组标题（说明书 2.6：分组以 H2 领起，本页内组用 24px/800 同口径） */
.subs-h {
  margin: 30px 0 2px;
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--ink);
}
.sub-history { list-style: none; margin: 0; padding: 0; }
.sub-history-item { padding: 12px 0; border-top: 1px solid var(--line); font-size: 14px; }
.sub-history-item:first-child { border-top: none; }
.sub-history-top { display: flex; align-items: center; gap: 8px; }
.sub-history-name { flex: 1; min-width: 0; font-weight: 600; color: var(--ink); }
.sub-history-date { margin-top: 3px; font-size: 13px; color: var(--ink2); font-variant-numeric: tabular-nums; }
/* R32：历史记录截断脚注（SupplyLogView .rc-foot 先例） */
.sub-history-foot { padding: 8px 0 0; border-top: 1px solid var(--line); text-align: center; font-size: 11px; color: var(--ink2); letter-spacing: 1px; }
.text-btn.danger {
  flex: none;
  white-space: nowrap;
  padding: 0 2px;
  font-size: 13px;
  color: var(--paper-danger);
  background: none;
  border: none;
  cursor: pointer;
}
/* r48-⑬：自动续订开关行（平面行，无盒子底） */
.renew-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.renew-info b { font-size: 14px; color: var(--ink); }
.renew-info small { font-size: 12px; color: var(--ink2); line-height: 1.5; }
/* 开关本体：轨道 48×28 胶囊＋24 圆钮。
   min-height 必须与 height 同值写死——mobile.css 全局给所有 button 垫了
   min-height:44px（触控规范），旧版开关 height:24 被垫成 44×44 正圆饼、
   钮钉在左上，就是生产截图里的「蓝色圆饼」；类选择器特异性高于元素
   选择器，此处声明即可压过，不动全局规则。 */
.renew-switch {
  flex: none;
  width: 48px;
  height: 28px;
  min-height: 28px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: var(--line);
  cursor: pointer;
  position: relative;
  -webkit-appearance: none;
  appearance: none;
  transition: background 0.2s ease;
}
.renew-switch .knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  transition: transform 0.2s ease;
}
.renew-switch.on { background: var(--paper-primary); }
.renew-switch.on .knob { transform: translateX(20px); }
.renew-switch:disabled { opacity: 0.6; cursor: wait; }
.renew-switch:focus-visible { outline: 2px solid var(--paper-primary); outline-offset: 2px; }
/* 公益服状态块：同平面口径——徽章行＋说明，下以发丝线分两组 */
.sub-free { display: flex; flex-direction: column; gap: 0.375rem; align-items: flex-start; }
.renew-block {
  align-self: stretch;
  margin-top: 0.75rem;
  padding: 0.875rem 0 0.25rem;
  border-top: 1px solid var(--line);
}
.renew-block-title { font-size: 0.875rem; font-weight: 600; color: var(--ink); }
.renew-line { margin: 4px 0 0; font-size: 0.8125rem; color: var(--ink2); font-variant-numeric: tabular-nums; }
.renew-hint { margin: 6px 0 0; font-size: 12px; color: var(--paper-warning); }
.renew-btns { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.renew-btn:disabled { opacity: 0.6; cursor: wait; }
.pane-empty {
  margin: 0;
  font-size: 0.8125rem;
  line-height: 1.6;
  color: var(--ink2);
}
.sub-realm {
  font-style: normal;
  font-size: 0.8125rem;
  color: var(--ink2);
}

/* R27：减少动态偏好下开关过渡停掉（本地守卫） */
@media (prefers-reduced-motion: reduce) {
  .renew-switch, .renew-switch .knob { transition: none; }
}
</style>
