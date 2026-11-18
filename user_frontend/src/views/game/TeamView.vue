<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳；手机原样 -->
  <UhdAccountShellGate>
  <div class="tm-page">
    <div class="tm-inner">
      <header class="tm-head">
        <h1 class="tm-title">团队管理</h1>
      </header>

      <div v-if="loadError" class="tm-err">
        {{ loadError }}<button type="button" class="tm-retry" @click="loadDetail">重试</button>
      </div>
      <div v-else-if="loading" class="tm-loading" role="status" aria-live="polite">
        <span class="tm-spin" aria-hidden="true"></span>
        <p class="tm-loading__text">正在加载团队…</p>
      </div>

      <template v-else-if="detail">
        <!-- 介绍卡：标题＋副文＋三列数据＋客服审核提示＋彩虹创建钮（照友站 §8.2） -->
        <section class="tm-card tm-intro">
          <div class="tm-intro__head">
            <span class="tm-intro__icon" aria-hidden="true">
              <ShieldCheck :size="26" />
            </span>
            <div class="tm-intro__text">
              <h2 class="tm-intro__title">为团队分配独立账号</h2>
              <p class="tm-intro__desc">选择线路和总流量，成员配额会按套餐规则自动计算。</p>
            </div>
          </div>

          <div class="tm-stats">
            <template v-if="team">
              <div class="tm-stat">
                <p class="tm-stat__label">线路</p>
                <p class="tm-stat__value">{{ teamLineName }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">套餐总流量</p>
                <p class="tm-stat__value">{{ formatGb(team.plan_traffic_gb) }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">成员配额</p>
                <p class="tm-stat__value">{{ team.member_count }} / {{ team.plan_member_limit ?? '—' }}</p>
              </div>
            </template>
            <template v-else>
              <div class="tm-stat">
                <p class="tm-stat__label">Global 最低起订</p>
                <p class="tm-stat__value">{{ formatGb(config.team_global_min_traffic_gb) }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">Premium 最低起订</p>
                <p class="tm-stat__value">{{ formatGb(config.team_premium_min_traffic_gb) }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">成员配额</p>
                <p class="tm-stat__value">自动计算</p>
              </div>
            </template>
          </div>

          <p v-if="team && team.status === 'pending_activation'" class="tm-pending" role="alert">
            套餐已购买，播放权限待开通——请联系客服开通播放权限后，成员才能正常使用。
          </p>
          <p v-else-if="team && team.status === 'active'" class="tm-active-line">
            团队套餐已开通，有效期至 {{ formatTeamDate(team.plan_expires_at) }}。
          </p>

          <div class="tm-review">
            <div class="tm-review__text">
              <p class="tm-review__title">支付前请先联系客服审核</p>
              <p class="tm-review__desc">
                结算金额可随时预览；为保障服务安全，请先联系客服审核、审核通过后再支付。
                购买成功后还需联系客服开通播放权限，否则无法使用。
              </p>
            </div>
            <UhdButton
              v-if="ctaMode === 'buy'"
              variant="primary"
              :dark="themeResolved === 'dark'"
              @click="openBuy"
            >
              <Plus :size="16" aria-hidden="true" />{{ team ? '升级 / 续订团队套餐' : '创建团队套餐' }}
            </UhdButton>
            <p v-else-if="ctaMode === 'sub'" class="tm-cta-note">
              子账号不能创建团队套餐，请用主账号操作。
            </p>
            <p v-else-if="ctaMode === 'closed'" class="tm-cta-note">
              团队购买暂未开放。
            </p>
          </div>
        </section>

        <!-- 团队信息条（有团队才显示）：名称/状态/团长＋改名（团长） -->
        <section v-if="team" class="tm-card tm-teambar">
          <div class="tm-teambar__main">
            <p class="tm-teambar__name">{{ team.name || '未命名团队' }}</p>
            <p class="tm-teambar__sub">
              团长 @{{ team.owner_username || '—' }} · {{ teamLineName }} · 总流量 {{ formatGb(team.plan_traffic_gb) }}
            </p>
          </div>
          <span class="tm-chip" :class="{ 'tm-chip--ok': team.status === 'active' }">
            {{ team.status === 'active' ? '已开通' : '待开通播放权限' }}
          </span>
          <button
            v-if="canManage"
            type="button"
            class="tm-mini-btn"
            @click="openRename"
          >改名</button>
        </section>

        <!-- 成员账号区（常驻）：搜索/筛选/成员表；无团队时空态＋控件禁用（照友站 §8.2） -->
        <section class="tm-members">
          <div class="tm-members__head">
            <h2 class="tm-sec-title">成员账号</h2>
            <button
              v-if="canManage"
              type="button"
              class="tm-add-btn"
              :disabled="!team"
              @click="openMember"
            >
              <UserPlus :size="16" aria-hidden="true" />添加成员
            </button>
          </div>
          <p v-if="team && canManage && atLimit" class="tm-limit-note">
            成员已达上限（{{ team.plan_member_limit }} 个子账号），移除一个后可以再添加。
          </p>
          <p v-if="myRole === 'member'" class="tm-limit-note">
            你由团长 @{{ team?.owner_username || '—' }} 创建，订阅与流量按团队套餐计算；如需退出请联系团长。
          </p>

          <div class="tm-toolbar">
            <label class="tm-tool">
              <select v-model="searchField" class="tm-select" :disabled="!team" aria-label="搜索方式">
                <option value="name">按名称</option>
              </select>
            </label>
            <label class="tm-tool tm-tool--grow">
              <input
                v-model="qDraft"
                type="text"
                class="tm-input"
                placeholder="输入成员名称"
                aria-label="输入成员名称"
                :disabled="!team"
                @keyup.enter="applySearch"
              />
            </label>
            <label class="tm-tool">
              <select v-model="statusFilter" class="tm-select" :disabled="!team" aria-label="全部状态" @change="applySearch">
                <option value="all">全部状态</option>
                <option value="active">正常</option>
                <option value="disabled">已禁用</option>
              </select>
            </label>
            <button type="button" class="tm-search-btn" :disabled="!team" @click="applySearch">
              <Search :size="15" aria-hidden="true" />搜索
            </button>
          </div>

          <div class="tm-card tm-table-card">
            <div v-if="team && membersLoading" class="tm-table-loading">
              <span class="tm-spin tm-spin--sm" aria-hidden="true"></span>正在加载…
            </div>
            <UhdEmptyState
              v-else-if="!team"
              title="请先开通团队套餐"
            />
            <UhdEmptyState
              v-else-if="!members.length"
              title="还没有成员"
              desc="点右上角「添加成员」，用用户名和密码创建第一个成员账号。"
            />
            <div v-else class="tm-table-wrap">
              <table class="tm-table">
                <thead>
                  <tr>
                    <th>成员</th>
                    <th>有效期</th>
                    <th>流量</th>
                    <th>使用情况</th>
                    <th>重置日</th>
                    <th v-if="canManage">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in members" :key="row.id">
                    <td>
                      <span class="tm-member-name">{{ row.username || '—' }}</span>
                      <span v-if="!row.is_active" class="tm-chip tm-chip--off">已禁用</span>
                      <span v-if="row.public_id" class="tm-member-id">{{ row.public_id }}</span>
                    </td>
                    <td>{{ formatTeamDate(row.expires_at) }}</td>
                    <td>{{ formatBytes(row.traffic_quota_bytes) }}</td>
                    <td>
                      <span class="tm-usage-text">
                        {{ formatBytes(row.traffic_used_bytes) }} / {{ formatBytes(row.traffic_quota_bytes) }}
                        <template v-if="row.traffic_used_pct != null">（{{ row.traffic_used_pct }}%）</template>
                      </span>
                      <span class="tm-usage-bar" aria-hidden="true">
                        <span class="tm-usage-bar__fill" :style="{ width: `${Math.min(100, row.traffic_used_pct ?? 0)}%` }"></span>
                      </span>
                    </td>
                    <td>{{ formatTeamDate(row.reset_date) }}</td>
                    <td v-if="canManage">
                      <button type="button" class="tm-remove-btn" @click="askRemove(row)">移除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="team && membersTotal > pageSize" class="tm-pager">
              <button type="button" class="tm-mini-btn" :disabled="page <= 1 || membersLoading" @click="goPage(page - 1)">上一页</button>
              <span class="tm-pager__info">第 {{ page }} 页 · 共 {{ membersTotal }} 人</span>
              <button type="button" class="tm-mini-btn" :disabled="page * pageSize >= membersTotal || membersLoading" @click="goPage(page + 1)">下一页</button>
            </div>
          </div>
        </section>
      </template>
    </div>

    <!-- 创建 / 升级弹窗（两步：配置 → 结算预览，照友站 §8.3） -->
    <UhdModal v-model="buyOpen" title="开通/升级团队方案" :width="720">
      <!-- 预览加载态（友站文案原样） -->
      <div v-if="buyStep === 'loading'" class="tm-buy-loading" role="status" aria-live="polite">
        <span class="tm-spin" aria-hidden="true"></span>
        <p>结算金额计算中…</p>
      </div>

      <!-- 第 1 步：配置 -->
      <div v-else-if="buyStep === 1" class="tm-buy">
        <p v-if="buyError" class="tm-buy-error" role="alert">{{ buyError }}</p>
        <div class="tm-buy__grid">
          <div class="tm-buy__form">
            <p class="tm-field-label">线路类型</p>
            <div class="tm-line-btns" role="group" aria-label="线路类型">
              <button
                type="button"
                class="tm-line-btn"
                :class="{ 'tm-line-btn--on': form.line === 'global' }"
                :aria-pressed="form.line === 'global'"
                @click="setLine('global')"
              >Global 全球线路</button>
              <button
                type="button"
                class="tm-line-btn"
                :class="{ 'tm-line-btn--on': form.line === 'premium' }"
                :aria-pressed="form.line === 'premium'"
                @click="setLine('premium')"
              >Premium 高级线路</button>
            </div>

            <p class="tm-field-label">同时播放路数</p>
            <input
              v-model.number="form.streams"
              type="number"
              class="tm-input"
              min="0"
              step="1"
              aria-label="同时播放路数"
            />

            <p class="tm-field-label">订购流量</p>
            <div class="tm-traffic-row">
              <input
                v-model.number="form.traffic_gb"
                type="number"
                class="tm-input"
                :min="lineMin"
                :max="config.team_max_traffic_gb"
                step="100"
                aria-label="订购流量（GB）"
              />
              <span class="tm-traffic-conv">{{ formatGb(form.traffic_gb || 0) }}</span>
            </div>
            <input
              v-model.number="form.traffic_gb"
              type="range"
              class="tm-slider"
              :min="lineMin"
              :max="config.team_max_traffic_gb"
              step="100"
              aria-label="订购流量滑块"
            />
            <div class="tm-slider-caps">
              <span>最低{{ lineMin.toLocaleString() }}GB</span>
              <span>最高{{ config.team_max_traffic_gb.toLocaleString() }}GB</span>
            </div>

            <p class="tm-field-label">订购时长</p>
            <input
              v-model.number="form.months"
              type="range"
              class="tm-slider"
              min="1"
              max="12"
              step="1"
              aria-label="订购时长（月）"
            />
            <div class="tm-slider-caps">
              <span>1 个月</span>
              <span>12 个月</span>
            </div>
          </div>

          <aside class="tm-buy__summary" aria-label="配置摘要">
            <div class="tm-sum-row"><span>类型</span><b>{{ TEAM_LINE_NAMES[form.line] }}</b></div>
            <div class="tm-sum-row"><span>流量</span><b>{{ (form.traffic_gb || 0).toLocaleString() }} GB</b></div>
            <div class="tm-sum-row"><span>时长</span><b>{{ form.months }} 个月</b></div>
            <div class="tm-sum-row"><span>子账户数量</span><b>{{ subCountPreview }}</b></div>
          </aside>
        </div>
      </div>

      <!-- 第 2 步：结算预览 -->
      <div v-else-if="buyStep === 2 && preview" class="tm-settle">
        <button type="button" class="tm-back-config" @click="buyStep = 1">
          <ChevronLeft :size="16" aria-hidden="true" />返回配置
        </button>
        <p class="tm-settle__sub">请确认以下结算信息无误</p>

        <p class="tm-field-label">团队名称</p>
        <input
          v-model="teamName"
          type="text"
          class="tm-input"
          maxlength="100"
          placeholder="请输入团队名称（可选）"
          aria-label="团队名称（可选）"
        />

        <div class="tm-settle__rows">
          <div class="tm-sum-row"><span>类型</span><b>{{ TEAM_LINE_NAMES[preview.line] }}</b></div>
          <div class="tm-sum-row"><span>流量</span><b>{{ preview.traffic_gb.toLocaleString() }} GB</b></div>
          <div class="tm-sum-row"><span>时长</span><b>{{ preview.months }} 个月</b></div>
          <div class="tm-sum-row"><span>子账户数量</span><b>{{ preview.sub_account_limit }}</b></div>
        </div>

        <div class="tm-settle__rows">
          <div class="tm-sum-row"><span>套餐费用</span><b>{{ formatFen(preview.list_price_fen) }}</b></div>
          <div class="tm-sum-row tm-sum-row--cut">
            <span>旧套餐抵扣<small class="tm-sum-note">当前套餐剩余价值抵扣</small></span>
            <b>−{{ formatFen(preview.switch_credit_fen) }}</b>
          </div>
          <div class="tm-sum-row tm-sum-row--total"><span>总计</span><b>{{ formatFen(preview.total_fen) }}</b></div>
          <div class="tm-sum-row"><span>钱包余额</span><b>{{ formatFen(preview.balance_fen) }}</b></div>
        </div>

        <p v-if="!preview.balance_sufficient" class="tm-buy-error" role="alert">
          钱包余额不足，还差 {{ formatFen(preview.total_fen - preview.balance_fen) }}，充值后即可购买。
        </p>
        <p v-if="buyError" class="tm-buy-error" role="alert">{{ buyError }}</p>
      </div>

      <template #footer>
        <template v-if="buyStep === 1">
          <button type="button" class="tm-btn tm-btn--ghost" @click="buyOpen = false">取消</button>
          <UhdButton variant="primary" :dark="themeResolved === 'dark'" @click="doPreview">预览结算金额</UhdButton>
        </template>
        <template v-else-if="buyStep === 2">
          <button type="button" class="tm-btn tm-btn--ghost" :disabled="buying" @click="buyStep = 1">取消</button>
          <UhdButton variant="primary" :dark="themeResolved === 'dark'" :disabled="buying" @click="doPurchase">
            {{ buying ? '处理中…' : '确认并支付' }}
          </UhdButton>
        </template>
      </template>
    </UhdModal>

    <!-- 添加成员弹窗 -->
    <UhdModal v-model="memberOpen" title="添加成员" :width="420">
      <form class="tm-member-form" @submit.prevent="doCreateMember">
        <p class="tm-field-label">成员用户名</p>
        <input
          v-model="memberForm.username"
          type="text"
          class="tm-input"
          autocomplete="off"
          placeholder="3–32 位字母、数字或下划线"
          aria-label="成员用户名"
        />
        <p class="tm-field-label">成员密码</p>
        <input
          v-model="memberForm.password"
          type="password"
          class="tm-input"
          autocomplete="new-password"
          placeholder="至少 6 位"
          aria-label="成员密码"
        />
        <p class="tm-member-note">
          成员账号由你创建、用上面的用户名密码登录，订阅与流量按团队套餐计算；移除成员会停用该账号。
        </p>
        <p v-if="memberError" class="tm-buy-error" role="alert">{{ memberError }}</p>
      </form>
      <template #footer>
        <button type="button" class="tm-btn tm-btn--ghost" :disabled="memberBusy" @click="memberOpen = false">取消</button>
        <UhdButton variant="primary" :dark="themeResolved === 'dark'" :disabled="memberBusy" @click="doCreateMember">
          {{ memberBusy ? '创建中…' : '创建成员' }}
        </UhdButton>
      </template>
    </UhdModal>

    <!-- 移除成员确认 -->
    <UhdModal v-model="removeOpen" title="移除成员" :width="400">
      <p class="tm-confirm-text">
        确认移除成员 @{{ removeTarget?.username }}？移除后该账号将被停用、无法再登录。
      </p>
      <p v-if="removeError" class="tm-buy-error" role="alert">{{ removeError }}</p>
      <template #footer>
        <button type="button" class="tm-btn tm-btn--ghost" :disabled="removeBusy" @click="removeOpen = false">取消</button>
        <button type="button" class="tm-btn tm-btn--danger" :disabled="removeBusy" @click="doRemoveMember">
          {{ removeBusy ? '处理中…' : '确认移除' }}
        </button>
      </template>
    </UhdModal>

    <!-- 改名弹窗 -->
    <UhdModal v-model="renameOpen" title="修改团队名" :width="400">
      <div class="tm-rename">
        <input
          v-model="renameValue"
          type="text"
          class="tm-input"
          maxlength="100"
          placeholder="请输入团队名称"
          aria-label="团队名称"
          @keyup.enter="doRename"
        />
      </div>
      <p v-if="renameError" class="tm-buy-error" role="alert">{{ renameError }}</p>
      <template #footer>
        <button type="button" class="tm-btn tm-btn--ghost" :disabled="renameBusy" @click="renameOpen = false">取消</button>
        <UhdButton variant="primary" :dark="themeResolved === 'dark'" :disabled="renameBusy" @click="doRename">
          {{ renameBusy ? '保存中…' : '保存' }}
        </UhdButton>
      </template>
    </UhdModal>
  </div>
  </UhdAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 团队管理（/game/account/team，2026-11-18 重建批 2）。
 *
 * 形态照友站团队页走查（说明书 §8）：介绍卡（起订量/成员配额自动
 * 计算）＋彩虹创建钮 → 成员账号区（搜索/状态筛选/六列表；无团队
 * 时空态「请先开通团队套餐」且控件禁用）→ 创建两步弹窗（选线路/
 * 流量/时长 → 结算预览：套餐费/旧套餐抵扣/总计 → 确认并支付）。
 * 数据走批 1 后端 /api/user/teams：价格全由 preview 返回，前端不
 * 自算价；子账户数摘要＝流量/除数（配置下发）仅作实时展示。
 *
 * 友站走查未到达的有团后管理动作（添加成员/改名/移除）按接口
 * 形状＋最合理行为推导：成员＝团长创建的子账号（用户名+密码，
 * 与家庭子账号同校验口径）；移除＝二次确认，文案明示账号停用；
 * 成员本人无自助退出接口，页面只给「联系团长」说明。
 */
import { computed, onMounted, ref } from 'vue'
import './team-view.css'
import { ChevronLeft, Plus, Search, ShieldCheck, UserPlus } from 'lucide-vue-next'
import UhdAccountShellGate from '@/components/uhd/UhdAccountShellGate.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdEmptyState from '@/components/uhd/UhdEmptyState.vue'
import UhdModal from '@/components/uhd/UhdModal.vue'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import { formatBytes } from '@/api/traffic'
import { newWalletKey } from '@/api/wallet'
import {
  teamsApi,
  TEAM_LINE_NAMES,
  formatFen,
  formatGb,
  formatTeamDate,
  teamErrorMessage,
  type TeamDetail,
  type TeamLine,
  type TeamMemberRow,
  type TeamPreview,
} from '@/api/team'

const toast = useToast()
const userStore = useUserStore()
const { resolved: themeResolved } = useTheme()

/* ---------------- 详情与成员列表 ---------------- */

const loading = ref(true)
const loadError = ref('')
const detail = ref<TeamDetail | null>(null)

const config = computed(() => detail.value!.config)
const team = computed(() => detail.value?.team ?? null)
const myRole = computed(() => detail.value?.my_role ?? null)
const canManage = computed(() => myRole.value === 'owner')
const isSubAccount = computed(() => !!userStore.user?.is_sub_account)
const atLimit = computed(() => {
  const t = team.value
  return !!t && t.plan_member_limit != null && t.member_count >= t.plan_member_limit
})
const teamLineName = computed(() => {
  const line = team.value?.plan_line
  return line === 'global' || line === 'premium' ? TEAM_LINE_NAMES[line] : '—'
})

/** 介绍卡主钮形态：买（团长/普通用户）/ 子账号提示 / 未开放 / 成员不显示 */
const ctaMode = computed<'buy' | 'sub' | 'closed' | 'none'>(() => {
  if (myRole.value === 'member') return 'none'
  if (!team.value && isSubAccount.value) return 'sub'
  if (!config.value.team_purchase_enabled) return 'closed'
  return 'buy'
})

const members = ref<TeamMemberRow[]>([])
const membersTotal = ref(0)
const membersLoading = ref(false)
const page = ref(1)
const pageSize = 20
const qDraft = ref('')
const qApplied = ref('')
const searchField = ref('name')
const statusFilter = ref('all')

async function loadDetail() {
  loading.value = true
  loadError.value = ''
  try {
    detail.value = await teamsApi.detail()
    if (detail.value.team) {
      page.value = 1
      await loadMembers()
    }
  } catch (err) {
    loadError.value = teamErrorMessage(err, '团队信息加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function loadMembers() {
  if (!team.value) return
  membersLoading.value = true
  try {
    const res = await teamsApi.members({
      q: qApplied.value || undefined,
      status: statusFilter.value,
      page: page.value,
      page_size: pageSize,
    })
    members.value = res.items
    membersTotal.value = res.total
  } catch (err) {
    toast.error(teamErrorMessage(err, '成员列表加载失败'))
  } finally {
    membersLoading.value = false
  }
}

function applySearch() {
  qApplied.value = qDraft.value.trim()
  page.value = 1
  void loadMembers()
}

function goPage(next: number) {
  page.value = next
  void loadMembers()
}

/* ---------------- 创建 / 升级（两步弹窗） ---------------- */

const buyOpen = ref(false)
const buyStep = ref<1 | 2 | 'loading'>(1)
const buyError = ref('')
const buying = ref(false)
const form = ref<{ line: TeamLine; traffic_gb: number; months: number; streams: number }>({
  line: 'global',
  traffic_gb: 10000,
  months: 1,
  streams: 0,
})
const preview = ref<TeamPreview | null>(null)
const teamName = ref('')
const purchaseKey = ref('')

const lineMin = computed(() =>
  form.value.line === 'global'
    ? config.value.team_global_min_traffic_gb
    : config.value.team_premium_min_traffic_gb,
)
const subCountPreview = computed(() =>
  Math.floor((form.value.traffic_gb || 0) / Math.max(1, config.value.team_sub_account_divisor)),
)

function clampTraffic() {
  const max = config.value.team_max_traffic_gb
  let v = Math.floor(Number(form.value.traffic_gb) || 0)
  if (v > max) v = max
  if (v < lineMin.value) v = lineMin.value
  form.value.traffic_gb = v
}

function setLine(line: TeamLine) {
  form.value.line = line
  clampTraffic()
}

function openBuy() {
  const t = team.value
  form.value = {
    line: t?.plan_line === 'premium' ? 'premium' : 'global',
    traffic_gb: t?.plan_traffic_gb ?? config.value.team_global_min_traffic_gb,
    months: 1,
    streams: t?.plan_streams ?? 0,
  }
  clampTraffic()
  preview.value = null
  buyError.value = ''
  buyStep.value = 1
  buyOpen.value = true
}

async function doPreview() {
  buyError.value = ''
  const traffic = Math.floor(Number(form.value.traffic_gb) || 0)
  if (traffic < lineMin.value) {
    buyError.value = `起订流量最低为 ${lineMin.value.toLocaleString()} GB`
    return
  }
  if (traffic > config.value.team_max_traffic_gb) {
    buyError.value = `流量上限为 ${config.value.team_max_traffic_gb.toLocaleString()} GB`
    return
  }
  form.value.traffic_gb = traffic
  form.value.months = Math.min(12, Math.max(1, Math.floor(Number(form.value.months) || 1)))
  form.value.streams = Math.max(0, Math.floor(Number(form.value.streams) || 0))
  buyStep.value = 'loading'
  try {
    preview.value = await teamsApi.preview({
      line: form.value.line,
      traffic_gb: form.value.traffic_gb,
      months: form.value.months,
      streams: form.value.streams,
    })
    teamName.value = team.value?.name ?? ''
    purchaseKey.value = newWalletKey()
    buyStep.value = 2
  } catch (err) {
    buyStep.value = 1
    buyError.value = teamErrorMessage(err, '结算预览失败，请稍后重试')
  }
}

async function doPurchase() {
  if (!preview.value || buying.value) return
  buying.value = true
  buyError.value = ''
  try {
    await teamsApi.purchase({
      line: form.value.line,
      traffic_gb: form.value.traffic_gb,
      months: form.value.months,
      streams: form.value.streams,
      name: teamName.value.trim() || undefined,
      idempotency_key: purchaseKey.value,
    })
    buyOpen.value = false
    toast.success('团队套餐购买成功')
    await loadDetail()
  } catch (err) {
    buyError.value = teamErrorMessage(err, '支付失败，请稍后重试')
  } finally {
    buying.value = false
  }
}

/* ---------------- 添加 / 移除成员、改名 ---------------- */

const memberOpen = ref(false)
const memberBusy = ref(false)
const memberError = ref('')
const memberForm = ref({ username: '', password: '' })

function openMember() {
  if (atLimit.value) {
    toast.error(`成员已达上限（${team.value?.plan_member_limit} 个子账号）`)
    return
  }
  memberForm.value = { username: '', password: '' }
  memberError.value = ''
  memberOpen.value = true
}

async function doCreateMember() {
  if (memberBusy.value) return
  memberBusy.value = true
  memberError.value = ''
  try {
    await teamsApi.createMember({
      username: memberForm.value.username.trim(),
      password: memberForm.value.password,
    })
    memberOpen.value = false
    toast.success('成员已创建')
    page.value = 1
    await loadDetail()
  } catch (err) {
    memberError.value = teamErrorMessage(err, '创建成员失败，请稍后重试')
  } finally {
    memberBusy.value = false
  }
}

const removeOpen = ref(false)
const removeBusy = ref(false)
const removeError = ref('')
const removeTarget = ref<TeamMemberRow | null>(null)

function askRemove(row: TeamMemberRow) {
  removeTarget.value = row
  removeError.value = ''
  removeOpen.value = true
}

async function doRemoveMember() {
  if (!removeTarget.value || removeBusy.value) return
  removeBusy.value = true
  removeError.value = ''
  try {
    await teamsApi.removeMember(removeTarget.value.user_id)
    removeOpen.value = false
    toast.success('成员已移除')
    await loadDetail()
  } catch (err) {
    removeError.value = teamErrorMessage(err, '移除成员失败，请稍后重试')
  } finally {
    removeBusy.value = false
  }
}

const renameOpen = ref(false)
const renameBusy = ref(false)
const renameError = ref('')
const renameValue = ref('')

function openRename() {
  renameValue.value = team.value?.name ?? ''
  renameError.value = ''
  renameOpen.value = true
}

async function doRename() {
  if (renameBusy.value) return
  renameBusy.value = true
  renameError.value = ''
  try {
    const res = await teamsApi.rename(renameValue.value.trim())
    if (detail.value) detail.value.team = res.team
    renameOpen.value = false
    toast.success('团队名已更新')
  } catch (err) {
    renameError.value = teamErrorMessage(err, '改名失败，请稍后重试')
  } finally {
    renameBusy.value = false
  }
}

onMounted(() => {
  void loadDetail()
})
</script>

<style scoped>
/* 团队页版式：主题变量在 team-view.css（令牌契约要求独立样式表）；
   字阶/圆角/动效走 --uhd-* 令牌。 */
.tm-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 14px 20px 120px;
  box-sizing: border-box;
}
/* 桌面并壳时内容区放宽到办事面宽度（闸门 ≥769px 才进壳） */
@media (min-width: 769px) {
  .tm-inner { max-width: 1000px; }
}
.tm-head { margin: 10px 0 18px; }
.tm-title {
  margin: 0;
  font-size: var(--uhd-fs-h1-app);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: var(--uhd-lh-tight);
}
.tm-err {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--tm-line);
  border-radius: 16px;
  color: var(--tm-text-2);
  font-size: var(--uhd-fs-body);
}
.tm-retry {
  border: 1px solid var(--tm-line);
  background: transparent;
  color: inherit;
  border-radius: var(--uhd-radius-pill);
  padding: 6px 14px;
  cursor: pointer;
  font-size: var(--uhd-fs-caption);
}
.tm-loading {
  min-height: 40dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
}
.tm-loading__text { margin: 0; font-size: var(--uhd-fs-caption); color: var(--tm-text-3); }
.tm-spin {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 3px solid var(--tm-line);
  border-top-color: var(--tm-text-2);
  animation: tm-spin 0.8s linear infinite;
}
.tm-spin--sm { width: 16px; height: 16px; border-width: 2px; }
@keyframes tm-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .tm-spin { animation-duration: 1.6s; }
}

/* ---------- 卡片与介绍卡 ---------- */
.tm-card {
  background: var(--tm-card);
  border: 1px solid var(--tm-line);
  border-radius: 20px;
}
.tm-intro { padding: 22px; }
.tm-intro__head {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.tm-intro__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  flex: none;
  border-radius: 14px;
  background: var(--tm-chip);
}
.tm-intro__title {
  margin: 2px 0 6px;
  font-size: var(--uhd-fs-title);
  font-weight: var(--uhd-fw-bold);
}
.tm-intro__desc {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
  line-height: var(--uhd-lh-body);
}
.tm-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 20px;
}
.tm-stat {
  padding: 14px;
  border-radius: 14px;
  background: var(--tm-chip);
}
.tm-stat__label {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
}
.tm-stat__value {
  margin: 0;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
}
.tm-pending {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--tm-warn-bg);
  color: var(--tm-warn-text);
  font-size: var(--uhd-fs-caption);
  line-height: var(--uhd-lh-body);
}
.tm-active-line {
  margin: 16px 0 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-review {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--tm-line);
}
.tm-review__text { flex: 1; min-width: 0; }
.tm-review__title {
  margin: 0 0 4px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
}
.tm-review__desc {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
  line-height: var(--uhd-lh-body);
}
.tm-cta-note {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
  white-space: nowrap;
}
@media (max-width: 640px) {
  .tm-stats { grid-template-columns: minmax(0, 1fr); }
  .tm-review { flex-direction: column; align-items: stretch; }
  .tm-cta-note { white-space: normal; }
}

/* ---------- 团队信息条 ---------- */
.tm-teambar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
  padding: 16px 22px;
}
.tm-teambar__main { flex: 1; min-width: 0; }
.tm-teambar__name {
  margin: 0;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tm-teambar__sub {
  margin: 4px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
}
.tm-chip {
  flex: none;
  padding: 4px 10px;
  border-radius: var(--uhd-radius-pill);
  background: var(--tm-warn-bg);
  color: var(--tm-warn-text);
  font-size: var(--uhd-fs-aux-sm);
  font-weight: var(--uhd-fw-semibold);
}
.tm-chip--ok {
  background: rgba(22, 163, 74, 0.14);
  color: var(--uhd-success);
}
.tm-chip--off {
  margin-left: 8px;
  background: var(--tm-chip);
  color: var(--tm-text-2);
}
.tm-mini-btn {
  flex: none;
  border: 1px solid var(--tm-line);
  background: transparent;
  color: inherit;
  border-radius: var(--uhd-radius-pill);
  padding: 7px 14px;
  font-size: var(--uhd-fs-caption);
  cursor: pointer;
}
.tm-mini-btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* ---------- 成员区 ---------- */
.tm-members { margin-top: 28px; }
.tm-members__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.tm-sec-title {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
}
.tm-add-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--tm-line);
  background: var(--tm-card);
  color: inherit;
  border-radius: var(--uhd-radius-pill);
  height: 38px;
  padding: 0 16px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.tm-add-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tm-limit-note {
  margin: 0 0 12px;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-toolbar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.tm-tool { display: flex; }
.tm-tool--grow { flex: 1; min-width: 160px; }
.tm-select,
.tm-input {
  height: 40px;
  padding: 0 14px;
  border: 1px solid var(--tm-line);
  border-radius: var(--uhd-radius-pill);
  background: var(--tm-input);
  color: inherit;
  font-size: var(--uhd-fs-caption);
  font-family: var(--uhd-font-sans);
  box-sizing: border-box;
}
.tm-tool .tm-input { width: 100%; border-radius: var(--uhd-radius-pill); }
.tm-select:disabled,
.tm-input:disabled { opacity: 0.5; }
.tm-search-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 18px;
  border: none;
  border-radius: var(--uhd-radius-pill);
  background: var(--tm-text-2);
  color: var(--tm-card);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.tm-search-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tm-table-card { padding: 6px 22px 14px; }
.tm-table-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-table-wrap { overflow-x: auto; }
.tm-table {
  width: 100%;
  min-width: 640px;
  border-collapse: collapse;
}
.tm-table th {
  padding: 12px 16px 10px 2px;
  text-align: left;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  color: var(--tm-text-2);
  border-bottom: 1px solid var(--tm-line);
  white-space: nowrap;
}
.tm-table td {
  padding: 12px 16px 12px 2px;
  font-size: var(--uhd-fs-body);
  border-bottom: 1px solid var(--tm-line);
  vertical-align: middle;
}
.tm-table tbody tr:last-child td { border-bottom: none; }
.tm-member-name { font-weight: var(--uhd-fw-semibold); }
.tm-member-id {
  display: block;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--tm-text-3);
}
.tm-usage-text { display: block; white-space: nowrap; }
.tm-usage-bar {
  display: block;
  width: 110px;
  height: 5px;
  margin-top: 6px;
  border-radius: 3px;
  background: var(--tm-chip);
  overflow: hidden;
}
.tm-usage-bar__fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--uhd-l-link);
}
.tm-remove-btn {
  border: none;
  background: transparent;
  color: var(--tm-danger);
  font-size: var(--uhd-fs-caption);
  cursor: pointer;
  padding: 4px 6px;
}
.tm-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding-top: 12px;
}
.tm-pager__info { font-size: var(--uhd-fs-caption); color: var(--tm-text-2); }

/* ---------- 弹窗内通用 ---------- */
.tm-field-label {
  margin: 18px 0 8px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
}
.tm-field-label:first-child { margin-top: 0; }
.tm-buy .tm-input,
.tm-settle .tm-input,
.tm-member-form .tm-input,
.tm-rename .tm-input {
  width: 100%;
  border-radius: 12px;
  /* 弹窗经 Teleport 出 .tm-page 作用域，--tm-* 不可达：边框/底色用
     中性字面值＋弹窗作用域翻转的 --uhd-l-input-bg（与订阅确认弹窗同法） */
  border-color: rgba(128, 128, 140, 0.35);
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text);
}
.tm-btn {
  height: 44px;
  padding: 0 20px;
  border-radius: var(--uhd-radius-pill);
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  font-family: var(--uhd-font-sans);
  cursor: pointer;
}
.tm-btn--ghost {
  border: 1px solid rgba(128, 128, 140, 0.35);
  background: transparent;
  color: var(--uhd-l-text);
}
.tm-btn--danger {
  border: none;
  background: var(--uhd-danger);
  color: #fff;
}
.tm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.tm-buy-error {
  margin: 0 0 14px;
  padding: 11px 14px;
  border-radius: 12px;
  background: rgba(220, 38, 38, 0.12);
  /* 只在弹窗内用（Teleport 出页面作用域）：深色玻璃上用亮红，浅色翻深红 */
  color: #f87171;
  font-size: var(--uhd-fs-caption);
  line-height: var(--uhd-lh-body);
}
html[data-theme='light'] .tm-buy-error { color: var(--uhd-danger); }
.tm-settle .tm-buy-error,
.tm-member-form .tm-buy-error { margin-top: 14px; }
.tm-confirm-text {
  margin: 0;
  font-size: var(--uhd-fs-body);
  line-height: var(--uhd-lh-body);
}

/* ---------- 购买弹窗 ---------- */
.tm-buy-loading {
  min-height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-caption);
}
.tm-buy-loading p { margin: 0; }
.tm-buy__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 24px;
  align-items: start;
}
@media (max-width: 640px) {
  .tm-buy__grid { grid-template-columns: minmax(0, 1fr); }
}
.tm-line-btns { display: flex; gap: 10px; }
.tm-line-btn {
  flex: 1;
  height: 44px;
  padding: 0 10px;
  border: 1px solid rgba(128, 128, 140, 0.35);
  border-radius: 12px;
  background: transparent;
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  font-family: var(--uhd-font-sans);
  cursor: pointer;
}
.tm-line-btn--on {
  border-color: var(--uhd-l-link);
  color: var(--uhd-l-text);
  background: rgba(128, 128, 140, 0.16);
}
.tm-traffic-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.tm-traffic-conv {
  flex: none;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
}
.tm-slider {
  width: 100%;
  margin: 12px 0 4px;
  accent-color: var(--uhd-l-link);
}
.tm-slider-caps {
  display: flex;
  justify-content: space-between;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--uhd-l-text-2);
}
.tm-buy__summary {
  padding: 16px;
  border: 1px solid rgba(128, 128, 140, 0.35);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tm-sum-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  font-size: var(--uhd-fs-caption);
}
.tm-sum-row span { color: var(--uhd-l-text-2); }
.tm-sum-row b { color: var(--uhd-l-text); font-weight: var(--uhd-fw-semibold); text-align: right; }
.tm-sum-row--total {
  padding-top: 10px;
  border-top: 1px solid rgba(128, 128, 140, 0.35);
  font-size: var(--uhd-fs-body);
}
.tm-sum-note {
  display: block;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--uhd-l-text-2);
}
.tm-back-config {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: none;
  background: transparent;
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
  padding: 0;
  margin-bottom: 6px;
}
.tm-settle__sub {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.tm-settle__rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid rgba(128, 128, 140, 0.35);
}
.tm-member-note {
  margin: 16px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  line-height: var(--uhd-lh-body);
}
</style>
