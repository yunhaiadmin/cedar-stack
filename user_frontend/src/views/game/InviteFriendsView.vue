<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳；
       手机端页头走统一顶栏子页模式（2026-11-18，见下方注释） -->
  <AxAccountShellGate>
  <div class="iv-page">
    <!-- 页头（2026-11-18 对齐友站子页头）：手机端由 App.vue 统一顶栏的
         子页模式承接（‹ 返回＋邀请好友＋胶囊同一行），本页原自带返回行
         已摘除；桌面壳内本就隐藏该行、顶栏维持站标，口径不变。 -->
    <main class="iv-body">
      <div v-if="loadError" class="iv-err">
        {{ loadError }}<button type="button" class="iv-retry" @click="load">重试</button>
      </div>
      <template v-else-if="!loading">
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
              @click="tab = 'welfare'; loadTrialCodes()"
            >
              <Gift :size="17" aria-hidden="true" />
              <span>福利邀请</span>
            </button>
          </div>

          <!-- 普通邀请：码面 + 战绩 + 返利规则 -->
          <template v-if="tab === 'normal'">
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
            <div class="iv-stats">
              <div class="iv-stat">
                <span class="iv-stat__label">已邀请</span>
                <span class="iv-stat__value">{{ invitedCount }} <small>位</small></span>
              </div>
              <div class="iv-stat">
                <span class="iv-stat__label">累计返利</span>
                <span class="iv-stat__value">${{ totalRebateText }}</span>
              </div>
            </div>
            <p v-if="pendingRebate > 0" class="iv-note">其中 ${{ pendingText }} 待入账，钱包开通后自动到账。</p>
            <p class="iv-rule">{{ rebateRuleText }}</p>
          </template>

          <!-- 福利邀请（2026-11-15 生成码机制，照友站「送好友一份试用」帧）：
               邀请人主动生成福利码（生成即占当月次数），好友凭分享链接
               注册后领取试用；子账号不可生成（后端 403，前端整区隐藏）。 -->
          <template v-else>
            <p v-if="isSubAccount" class="iv-empty">子账号不能送试用，请使用主账号操作。</p>
            <template v-else>
              <div class="iv-title-row iv-whead">
                <h2 class="iv-wtitle">送好友一份试用</h2>
                <button
                  type="button"
                  class="iv-icon-btn"
                  aria-label="刷新福利状态"
                  :disabled="trialLoading"
                  @click="loadTrialCodes"
                >
                  <RefreshCw :size="20" aria-hidden="true" />
                </button>
              </div>
              <p class="iv-wsub">好友通过链接注册后，即可领取所选试用。</p>

              <p class="iv-wcount">
                本月可用 <strong class="iv-wcount__num">{{ welfareRemaining }}</strong>
                <span class="iv-wcount__dim">/ {{ welfare?.monthly_limit ?? 0 }} 次</span>
                <span class="iv-wcount__split">已使用 {{ welfare?.used_codes_this_month ?? 0 }} · 待使用 {{ welfare?.pending_this_month ?? 0 }}</span>
              </p>

              <!-- 试用选卡：只有国际线路一张（国内线路不开，既定口径） -->
              <div class="iv-tcard">
                <span class="iv-tcard__bar" aria-hidden="true"></span>
                <div class="iv-tcard__main">
                  <p class="iv-tcard__name">{{ trialPlanName }}</p>
                  <p class="iv-tcard__desc">国际线路，面向全球访问</p>
                  <p class="iv-tcard__meta">{{ trialMetaText }}</p>
                </div>
              </div>

              <button
                type="button"
                class="iv-btn iv-btn--rainbow iv-btn--wide"
                :disabled="!canGenerate || generating"
                @click="doGenerateTrialCode"
              >
                <Gift :size="18" aria-hidden="true" />
                <span>{{ generating ? '生成中…' : '生成试用邀请码' }}</span>
              </button>
              <p v-if="welfareHintText" class="iv-note" :class="{ 'iv-note--ok': welfare?.eligible }">{{ welfareHintText }}</p>

              <!-- 刚生成的码：码面 + 分享链接，先给复制动作再入列表 -->
              <div v-if="lastGenerated" class="iv-gen">
                <p class="iv-gen__code">{{ lastGenerated.code }}</p>
                <div class="iv-actions iv-gen__actions">
                  <button type="button" class="iv-btn iv-btn--rainbow" @click="copyText(trialLink(lastGenerated), '试用链接已复制')">
                    <Share2 :size="18" aria-hidden="true" />
                    <span>复制试用链接</span>
                  </button>
                  <button type="button" class="iv-btn iv-btn--glass" @click="copyText(lastGenerated.code, '福利码已复制')">
                    <Copy :size="18" aria-hidden="true" />
                    <span>复制福利码</span>
                  </button>
                </div>
              </div>

              <hr class="iv-divider" />

              <h2 class="iv-wtitle">我的福利码</h2>
              <p v-if="!trialCodes.length" class="iv-none">生成邀请码，把试用分享给好友。</p>
              <ul v-else class="iv-rows">
                <li v-for="c in trialCodes" :key="c.id" class="iv-row">
                  <span class="iv-row__code">{{ c.code }}</span>
                  <span class="iv-badge" :class="{ 'iv-badge--pending': c.status === 'pending' }">
                    {{ c.status === 'used' ? '已使用' : c.status === 'expired' ? '已过期' : '待使用' }}
                  </span>
                  <span class="iv-row__date">{{ formatDate(c.created_at) }}</span>
                  <button
                    v-if="c.status === 'pending'"
                    type="button"
                    class="iv-icon-btn iv-icon-btn--sm"
                    aria-label="复制试用链接"
                    @click="copyText(trialLink(c), '试用链接已复制')"
                  >
                    <Copy :size="16" aria-hidden="true" />
                  </button>
                </li>
              </ul>
            </template>
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
                <span class="iv-row__amt">${{ moneyText(r.rebate_total) }}</span>
              </li>
            </ul>
          </template>

          <template v-else>
            <div class="iv-rec-sub">
              <span>累计返利 ${{ totalRebateText }}<template v-if="pendingRebate > 0">（待入账 ${{ pendingText }}）</template></span>
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
                <span class="iv-row__amt">${{ r.rebate_amount }}</span>
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
  </AxAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 邀请好友（/game/account/invite）——2026-11-14 照 参考站 邀请页截图重做。
 * 形态：大标题＋换码钮、普通/福利双页签、码面、彩虹玻璃双钮、战绩大数字、
 * 规则段（文案由后端配置生成，金额不写死）、记录双子页签。
 * 口径：普通邀请 = 好友付费按配置返利进钱包余额（默认分档：每位好友累计
 * 付费前段 5%、超出 1%，阈值读配置）；福利邀请 = 生成码机制（2026-11-15
 * 改版，照友站）：邀请人主动生成福利码（生成即占当月次数，受月次数与
 * 累计付费门槛约束，全读后端配置），好友凭分享链接注册后领取试用。
 * 注册不发可变现奖励，邀请注册也不再自动发试用。
 */
import { computed, onMounted, ref } from 'vue'
import { Copy, Gift, RefreshCw, Share2 } from 'lucide-vue-next'
import {
  inviteApi,
  type InvitationRecordRow,
  type InviteWelfareStatus,
  type MyInviteInfo,
  type RebateRow,
  type TrialCodeRow,
} from '@/api/economy'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import AxAccountShellGate from '@/components/ax/AxAccountShellGate.vue'
import './invite-friends.css'

const toast = useToast()
const userStore = useUserStore()

const loading = ref(true)
const loadError = ref('')
const info = ref<MyInviteInfo | null>(null)
const records = ref<InvitationRecordRow[]>([])
const rebates = ref<RebateRow[]>([])
const tab = ref<'normal' | 'welfare'>('normal')
const recTab = ref<'mine' | 'rebates'>('mine')
const confirmRegen = ref(false)
const regenerating = ref(false)

/** 福利码机制状态：资格以最近一次后端回包为准（my-code / trial-codes 同口径） */
const welfareState = ref<InviteWelfareStatus | null>(null)
const trialCodes = ref<TrialCodeRow[]>([])
const trialLoading = ref(false)
const generating = ref(false)
const lastGenerated = ref<TrialCodeRow | null>(null)
const isSubAccount = computed(() => userStore.user?.is_sub_account === true)

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

/** 返利规则文案：按后端模式/比例/阈值生成，数字一个都不写死。
 * 句式照友站（2026-11-18 对齐）：「充值」措辞 + 金额「N USD」后置式。 */
const rebateRuleText = computed(() => {
  const r = info.value?.config?.rebate
  if (!r) return ''
  if (r.percent <= 0) return '返利活动暂未开启，好友充值暂时不会产生返利。'
  if (r.mode === 'tiered') {
    return `好友注册并充值后返利自动计入余额：每位好友累计充值前 ${yuanText(r.tier_threshold_fen)} USD 返 ${r.percent}%，超出部分返 ${r.tier_percent}%。`
  }
  const cap = r.cap_fen > 0 ? `，累计返利上限 ${yuanText(r.cap_fen)} USD` : ''
  return `好友注册并充值后返利自动计入余额：按好友充值金额的 ${r.percent}% 返利${cap}。`
})

const welfare = computed(() => welfareState.value ?? info.value?.welfare ?? null)
const welfareRemaining = computed(() => welfare.value?.remaining_this_month ?? 0)

const welfareActive = computed(() => {
  const w = welfare.value
  return !!w && w.enabled && w.days > 0 && w.monthly_limit > 0
})

const canGenerate = computed(() => !!welfare.value?.eligible && !isSubAccount.value)

const trialPlanName = computed(() => welfare.value?.plan?.plan_name || '试用套餐')

/** 选卡第三行：未生成过时按友站帧示占位文案；生成过后示真实天数/流量 */
const trialMetaText = computed(() => {
  const w = welfare.value
  if (!w || !trialCodes.value.length) return '时长与流量在生成邀请码后显示'
  const days = trialCodes.value[0]?.days ?? w.days
  const bytes = w.plan?.monthly_traffic_bytes
  const traffic = bytes ? ` · ${Math.round(bytes / (1024 ** 3))} GB 流量` : ''
  return `${days} 天试用${traffic}`
})

/** 生成钮下的资格提示（照友站帧：门槛行 + 还差多少） */
const welfareHintText = computed(() => {
  const w = welfare.value
  if (!w || !welfareActive.value) return '福利邀请暂未开启，管理员开启后即可生成试用邀请码。'
  if (w.used_this_month >= w.monthly_limit) return '本月福利次数已用完，下个月再来送好友试用。'
  if (w.inviter_paid_fen < w.min_paid_fen) {
    return `累计成功充值须大于 $${yuanText(w.min_paid_fen)}，还差 $${yuanText(w.min_paid_fen - w.inviter_paid_fen)}。`
  }
  if (w.min_paid_fen > 0) return `累计成功充值须大于 $${yuanText(w.min_paid_fen)}，你已达标。`
  return '你已符合福利邀请资格。'
})

function trialLink(code: TrialCodeRow): string {
  return `${window.location.origin}${code.link_path || `/login?trial=${code.code}`}`
}

async function loadTrialCodes() {
  if (trialLoading.value) return
  trialLoading.value = true
  try {
    const res = await inviteApi.trialCodes({ limit: 50 })
    trialCodes.value = res.codes || []
    welfareState.value = res.welfare
  } catch {
    // 列表拉取失败不打断整页：资格区沿用 my-code 的快照，列表保持旧值
  } finally {
    trialLoading.value = false
  }
}

async function doGenerateTrialCode() {
  if (generating.value || !canGenerate.value) return
  generating.value = true
  try {
    const res = await inviteApi.generateTrialCode()
    lastGenerated.value = res.code
    welfareState.value = res.welfare
    trialCodes.value = [res.code, ...trialCodes.value.filter(c => c.id !== res.code.id)]
    toast.success('试用邀请码已生成，把链接发给好友吧')
  } catch {
    toast.error('生成失败，请稍后重试')
    void loadTrialCodes()
  } finally {
    generating.value = false
  }
}

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
    welfareState.value = codeRes.welfare
    records.value = recRes.records || []
    rebates.value = rebRes.rebates || []
    void loadTrialCodes()
  } catch {
    loadError.value = '邀请信息加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
