<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳
       （壳内隐藏移动返回顶栏）；手机原样 -->
  <UhdAccountShellGate>
  <div class="iv-page">
    <header class="iv-topbar">
      <RouterLink to="/game/account/menu" class="iv-back" aria-label="返回我的">
        <ChevronLeft :size="22" aria-hidden="true" />
        <span>邀请好友</span>
      </RouterLink>
      <!-- 「网页观影」钮已删（2026-11-14 顶栏统一单）：由 App.vue 统一
           顶栏的常驻胶囊承接，本页不再自带一份（原 42px，与全站 54px 不一致） -->
    </header>

    <main class="iv-body">
      <div v-if="loadError" class="iv-err">
        {{ loadError }}<button type="button" class="iv-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="iv-skeleton" aria-hidden="true"></div>

      <template v-else>
        <p v-if="!inviteEnabled" class="iv-empty">邀请功能暂时关闭，过段时间再来看看。</p>

        <template v-else>
          <!-- 大标题 + 换码钮（照友站截图：标题右侧刷新图标 = 换一个邀请码） -->
          <div class="iv-title-row">
            <h1 class="iv-title">邀请好友</h1>
            <button
              type="button"
              class="iv-icon-btn"
              aria-label="换一个邀请码"
              :disabled="regenerating"
              @click="confirmRegen = true"
            >
              <RefreshCw :size="22" aria-hidden="true" />
            </button>
          </div>

          <!-- 分段页签：普通邀请 / 福利邀请（礼物图标） -->
          <div class="iv-seg" role="tablist" aria-label="邀请方式">
            <button
              type="button"
              role="tab"
              class="iv-seg__item"
              :class="{ 'iv-seg__item--on': tab === 'normal' }"
              :aria-selected="tab === 'normal'"
              @click="tab = 'normal'"
            >普通邀请</button>
            <button
              type="button"
              role="tab"
              class="iv-seg__item"
              :class="{ 'iv-seg__item--on': tab === 'welfare' }"
              :aria-selected="tab === 'welfare'"
              @click="tab = 'welfare'"
            >
              <Gift :size="17" aria-hidden="true" />
              <span>福利邀请</span>
            </button>
          </div>

          <!-- 码面：两页签共用同一套码/链接（福利邀请不另发码，差别在权益） -->
          <p class="iv-code-label">{{ displayName }} 的邀请码</p>
          <p class="iv-code-big">{{ myCode }}</p>
          <div class="iv-actions">
            <button type="button" class="iv-btn iv-btn--rainbow" @click="copyText(inviteLink, '邀请链接已复制')">
              <Share2 :size="18" aria-hidden="true" />
              <span>复制邀请链接</span>
            </button>
            <button type="button" class="iv-btn iv-btn--glass" @click="copyText(myCode, '邀请码已复制')">
              <Copy :size="18" aria-hidden="true" />
              <span>复制邀请码</span>
            </button>
          </div>

          <!-- 普通邀请：战绩 + 返利规则 -->
          <template v-if="tab === 'normal'">
            <div class="iv-stats">
              <div class="iv-stat">
                <span class="iv-stat__label">已邀请</span>
                <span class="iv-stat__value">{{ invitedCount }} <small>位</small></span>
              </div>
              <div class="iv-stat">
                <span class="iv-stat__label">累计返利</span>
                <span class="iv-stat__value">¥{{ totalRebateText }}</span>
              </div>
            </div>
            <p v-if="pendingRebate > 0" class="iv-note">其中 ¥{{ pendingText }} 待入账，钱包开通后自动到账。</p>
            <p class="iv-rule">{{ rebateRuleText }}</p>
          </template>

          <!-- 福利邀请：送好友试用，资格/次数/规则全读后端配置 -->
          <template v-else>
            <div class="iv-stats">
              <div class="iv-stat">
                <span class="iv-stat__label">本月可送</span>
                <span class="iv-stat__value">{{ welfareRemaining }} <small>次</small></span>
              </div>
              <div class="iv-stat">
                <span class="iv-stat__label">好友可得试用</span>
                <span class="iv-stat__value">{{ trialDays }} <small>天</small></span>
              </div>
            </div>
            <p class="iv-note" :class="{ 'iv-note--ok': welfare?.eligible }">{{ welfareStatusText }}</p>
            <p class="iv-rule">{{ welfareRuleText }}</p>
          </template>

          <hr class="iv-divider" />

          <!-- 记录：子页签 我的邀请 / 返利明细 -->
          <div class="iv-rec-head">
            <h2 class="iv-rec-title">记录</h2>
            <div class="iv-seg iv-seg--small" role="tablist" aria-label="记录类型">
              <button
                type="button"
                role="tab"
                class="iv-seg__item"
                :class="{ 'iv-seg__item--on': recTab === 'mine' }"
                :aria-selected="recTab === 'mine'"
                @click="recTab = 'mine'"
              >我的邀请</button>
              <button
                type="button"
                role="tab"
                class="iv-seg__item"
                :class="{ 'iv-seg__item--on': recTab === 'rebates' }"
                :aria-selected="recTab === 'rebates'"
                @click="recTab = 'rebates'"
              >返利明细</button>
            </div>
          </div>

          <template v-if="recTab === 'mine'">
            <div class="iv-rec-sub">
              <span>共邀请 {{ invitedCount }} 位好友</span>
              <button type="button" class="iv-icon-btn iv-icon-btn--sm" aria-label="刷新记录" @click="loadRecords">
                <RefreshCw :size="18" aria-hidden="true" />
              </button>
            </div>
            <p v-if="!records.length" class="iv-none">还没有邀请记录</p>
            <ul v-else class="iv-rows">
              <li v-for="r in records" :key="r.id" class="iv-row">
                <span class="iv-row__name">{{ r.invitee_username }}</span>
                <span v-if="r.paid" class="iv-badge">已付费</span>
                <span class="iv-row__date">{{ formatDate(r.created_at) }}</span>
                <span class="iv-row__amt">¥{{ moneyText(r.rebate_total) }}</span>
              </li>
            </ul>
          </template>

          <template v-else>
            <div class="iv-rec-sub">
              <span>累计返利 ¥{{ totalRebateText }}<template v-if="pendingRebate > 0">（待入账 ¥{{ pendingText }}）</template></span>
              <button type="button" class="iv-icon-btn iv-icon-btn--sm" aria-label="刷新返利明细" @click="loadRecords">
                <RefreshCw :size="18" aria-hidden="true" />
              </button>
            </div>
            <p v-if="!rebates.length" class="iv-none">还没有返利记录，好友付费后返利会出现在这里。</p>
            <ul v-else class="iv-rows">
              <li v-for="r in rebates" :key="r.id" class="iv-row">
                <span class="iv-row__name">{{ r.invitee_username }}</span>
                <span class="iv-badge" :class="{ 'iv-badge--pending': r.status === 'pending' }">
                  {{ r.status === 'credited' ? '已入账' : '待入账' }}
                </span>
                <span class="iv-row__date">{{ formatDate(r.created_at) }}</span>
                <span class="iv-row__amt">¥{{ r.rebate_amount }}</span>
              </li>
            </ul>
          </template>
        </template>
      </template>
    </main>

    <!-- 换码二次确认 -->
    <div v-if="confirmRegen" class="iv-modal-mask" @click.self="confirmRegen = false">
      <div class="iv-modal" role="dialog" aria-modal="true" aria-label="换一个邀请码">
        <h3 class="iv-modal__title">换一个邀请码？</h3>
        <p class="iv-modal__text">
          旧邀请码和旧邀请链接会立刻失效，之后用旧码注册的好友不会再算到你名下；
          已经邀请成功的好友关系和已产生的返利不受影响。
        </p>
        <div class="iv-modal__actions">
          <button type="button" class="iv-btn iv-btn--glass" @click="confirmRegen = false">取消</button>
          <button type="button" class="iv-btn iv-btn--rainbow" :disabled="regenerating" @click="doRegenerate">
            确认换码
          </button>
        </div>
      </div>
    </div>
  </div>
  </UhdAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 邀请好友（/game/account/invite）——2026-11-14 照 UHDNow 邀请页截图重做。
 * 形态：大标题＋换码钮、普通/福利双页签、码面、彩虹玻璃双钮、战绩大数字、
 * 规则段（文案由后端配置生成，金额不写死）、记录双子页签。
 * 口径：普通邀请 = 好友付费按配置返利进钱包余额（默认分档：每位好友累计
 * 付费前段 5%、超出 1%，阈值读配置）；福利邀请 = 好友注册得试用订阅，
 * 邀请人受月次数与累计付费门槛约束（全读后端配置）。注册不发可变现奖励。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronLeft, Copy, Gift, RefreshCw, Share2 } from 'lucide-vue-next'
import {
  inviteApi,
  type InvitationRecordRow,
  type MyInviteInfo,
  type RebateRow,
} from '@/api/economy'
import { useToast } from '@/composables/useToast'
import UhdAccountShellGate from '@/components/uhd/UhdAccountShellGate.vue'
import './invite-friends.css'

const toast = useToast()

const loading = ref(true)
const loadError = ref('')
const info = ref<MyInviteInfo | null>(null)
const records = ref<InvitationRecordRow[]>([])
const rebates = ref<RebateRow[]>([])
const tab = ref<'normal' | 'welfare'>('normal')
const recTab = ref<'mine' | 'rebates'>('mine')
const confirmRegen = ref(false)
const regenerating = ref(false)

const inviteEnabled = computed(() => info.value?.config?.enabled !== false)
const myCode = computed(() => info.value?.code || '')
const displayName = computed(() => info.value?.username || '我')
const invitedCount = computed(() => info.value?.invited_count ?? records.value.length)
const inviteLink = computed(() =>
  myCode.value ? `${window.location.origin}/login?invite=${myCode.value}` : '',
)

const creditedRebate = computed(() => Number(info.value?.rebate_totals?.credited || 0))
const pendingRebate = computed(() => Number(info.value?.rebate_totals?.pending || 0))
const totalRebateText = computed(() => (creditedRebate.value + pendingRebate.value).toFixed(2))
const pendingText = computed(() => pendingRebate.value.toFixed(2))

function yuanText(fen: number): string {
  const yuan = fen / 100
  return Number.isInteger(yuan) ? String(yuan) : yuan.toFixed(2)
}

function moneyText(v: number): string {
  return (Number(v) || 0).toFixed(2)
}

/** 返利规则文案：按后端模式/比例/阈值生成，数字一个都不写死 */
const rebateRuleText = computed(() => {
  const r = info.value?.config?.rebate
  if (!r) return ''
  if (r.percent <= 0) return '返利活动暂未开启，好友付费暂时不会产生返利。'
  if (r.mode === 'tiered') {
    return `好友注册并付费后返利自动计入余额：每位好友累计付费前 ¥${yuanText(r.tier_threshold_fen)} 返 ${r.percent}%，超出部分返 ${r.tier_percent}%。`
  }
  const cap = r.cap_fen > 0 ? `，累计返利上限 ¥${yuanText(r.cap_fen)}` : ''
  return `好友注册并付费后返利自动计入余额：按好友付费金额的 ${r.percent}% 返利${cap}。`
})

const welfare = computed(() => info.value?.welfare ?? null)
const trialDays = computed(() => welfare.value?.days ?? 0)
const welfareRemaining = computed(() => {
  const w = welfare.value
  if (!w) return 0
  return Math.max(0, w.monthly_limit - w.used_this_month)
})

const welfareActive = computed(() => {
  const w = welfare.value
  return !!w && w.enabled && w.days > 0 && w.monthly_limit > 0
})

const welfareStatusText = computed(() => {
  const w = welfare.value
  if (!w || !welfareActive.value) return '福利邀请暂未开启，管理员开启后，好友注册即可得试用。'
  if (w.eligible) return `你已符合福利邀请资格，本月已送出 ${w.used_this_month} 次。`
  if (w.used_this_month >= w.monthly_limit) return '本月福利次数已用完，下个月再来送好友试用。'
  if (w.inviter_paid_fen < w.min_paid_fen) {
    return `还差 ¥${yuanText(w.min_paid_fen - w.inviter_paid_fen)} 达到福利邀请资格（累计付费满 ¥${yuanText(w.min_paid_fen)}）。`
  }
  return ''
})

const welfareRuleText = computed(() => {
  const w = welfare.value
  if (!w) return ''
  return `好友通过你的邀请注册成功，立刻得 ${w.days} 天试用订阅。每位邀请人每月可送 ${w.monthly_limit} 次，邀请人累计付费满 ¥${yuanText(w.min_paid_fen)} 即可参与。试用是体验权益，不能转让或变现。`
})

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

async function copyText(text: string, okMsg: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    toast.success(okMsg)
    return
  } catch {
    // 剪贴板 API 不可用（非安全上下文等）时退回 execCommand 兜底
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    toast.success(okMsg)
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

async function doRegenerate() {
  if (regenerating.value) return
  regenerating.value = true
  try {
    info.value = await inviteApi.regenerate()
    confirmRegen.value = false
    toast.success('邀请码已更新，旧码已失效')
  } catch {
    toast.error('换码失败，请稍后重试')
  } finally {
    regenerating.value = false
  }
}

async function loadRecords() {
  try {
    const [recRes, rebRes] = await Promise.all([
      inviteApi.records({ limit: 100 }),
      inviteApi.rebates({ limit: 100 }),
    ])
    records.value = recRes.records || []
    rebates.value = rebRes.rebates || []
  } catch {
    toast.error('记录刷新失败，请稍后重试')
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [codeRes, recRes, rebRes] = await Promise.all([
      inviteApi.myCode(),
      inviteApi.records({ limit: 100 }),
      inviteApi.rebates({ limit: 100 }),
    ])
    info.value = codeRes
    records.value = recRes.records || []
    rebates.value = rebRes.rebates || []
  } catch {
    loadError.value = '邀请信息加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
