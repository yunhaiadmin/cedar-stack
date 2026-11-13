<script setup lang="ts">
/**
 * 邀请管理：邀请记录、邀请码、推广奖励（会员天数）
 *
 * 推广奖励策略（PUT /promotion/policy）在本页的「推广奖励」卡上直接
 * 展示与编辑，与系统设置写的是同一组键。
 * 积分流水 / 手动调分 / 流水审计已随货币于 2026-11-13 下线删除。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  AlertTriangle, Gift, Megaphone, Plus, QrCode, RefreshCw, Save, Search, UserPlus,
} from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard, StatTile } from '@/components/ui'
import {
  fetchInvitations,
  fetchEconomyStats,
  type InvitationRow,
  type EconomyStats,
} from '@/api/economy'
import {
  fetchInvitationCodes,
  fetchPromotion,
  fetchUsers,
  generateInvitationCodes,
  revokeInvitationCodes,
  updateInvitationCode,
  updatePromotionPolicy,
  type InvitationCodeRow,
  type PromotionPolicy,
  type PromotionResponse,
} from '@/api/admin'
import { useAuthStore } from '@/stores/auth'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import { useTableSelection } from '@/composables/useTableSelection'
import type { RowKey } from '@/composables/useTableSelection'

const loading = ref(false)
/** 邀请记录读取失败：给出可重试的错误态，而不是假装「暂无邀请记录」 */
const invError = ref(false)
const stats = ref<EconomyStats | null>(null)

// ===== 邀请记录 =====
const invitations = ref<InvitationRow[]>([])

const inviteColumns: DataColumn[] = [
  { key: 'inviter', label: '邀请人', width: 140, mobile: 'title' },
  { key: 'invitee', label: '被邀请人', width: 140 },
  { key: 'created_at', label: '时间' },
]

function scrollToFirstError() {
  requestAnimationFrame(() => {
    document.querySelector('.el-dialog .el-form-item.is-error')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

/** 用户搜索（远程）：不再要求管理员手填数据库 ID */
const userOptions = ref<{ id: number; username: string }[]>([])
const userSearching = ref(false)

async function searchUsers(keyword: string) {
  userSearching.value = true
  try {
    const res = await fetchUsers({ search: keyword || undefined, limit: 20 })
    userOptions.value = res.users.map((u) => ({ id: u.id, username: u.username }))
  } catch {
    userOptions.value = []
  } finally {
    userSearching.value = false
  }
}

const inviteTotal = computed(() => invitations.value.length)

/** 邀请记录 + 经济总览：与流水的翻页 / 类型筛选无关，不随翻页重复请求 */
async function loadOverview() {
  loading.value = true
  invError.value = false
  try {
    const [inv, econ] = await Promise.all([
      fetchInvitations({ limit: 100 }).catch(() => null),
      fetchEconomyStats().catch(() => null),
    ])
    if (inv) invitations.value = inv.records
    else invError.value = true
    if (econ) stats.value = econ
  } finally {
    loading.value = false
  }
}

async function load() {
  await loadOverview()
}

function fmtTime(iso?: string | null) {
  return iso ? iso.slice(0, 19).replace('T', ' ') : '—'
}

// ===== 邀请码管理（v2.44.0） =====
const invLoading = ref(false)
const invCodes = ref<InvitationCodeRow[]>([])
const invSummary = ref<Record<string, number>>({})
const invStates = ref<{ value: string; label: string }[]>([])
const invKeyword = ref('')
const codesError = ref(false)
const invState = ref('')

const invColumns: DataColumn[] = [
  { key: 'code', label: '邀请码', width: 130, mobile: 'title' },
  { key: 'owner_username', label: '归属', width: 120 },
  { key: 'use_count', label: '使用情况', width: 120 },
  { key: 'state_label', label: '状态', width: 90 },
  { key: 'expires_at', label: '有效期', width: 150 },
  { key: 'whitelist', label: '白名单', minWidth: 150 },
  // 用 actions：DataTable 会把这一列渲染到手机卡片的底部操作区，
  // 用 action 会退化成一行「标签 / 值」，按钮变得不好按
  { key: 'actions', label: '操作', width: 150 },
]

async function loadCodes() {
  invLoading.value = true
  codesError.value = false
  try {
    const res = await fetchInvitationCodes({
      keyword: invKeyword.value || undefined,
      state: invState.value || undefined,
      limit: 300,
    })
    invCodes.value = res.codes
    invSummary.value = res.summary
    invStates.value = res.states
  } catch {
    /* 拦截器已提示 */
    codesError.value = true
  } finally {
    invLoading.value = false
  }
}

const usesText = (row: InvitationCodeRow) =>
  row.max_uses ? `${row.use_count} / ${row.max_uses}` : `${row.use_count} / 不限`

/**
 * 邀请码表批量作废（v2.58）
 * - 只动邀请码表这张 DataTable，邀请记录表不动
 * - 复用后端真正的批量接口 revokeInvitationCodes(ids)，一次调用，不循环
 * - isRowSelectable：已作废（is_active=false）的行不可选
 */
const selection = useTableSelection()
/** 模板 v-model 用的可写代理：嵌套 ref 在模板里不会自动解包 */
const selectedKeys = computed<RowKey[]>({
  get: () => selection.selectedKeys.value,
  set: (v) => { selection.selectedKeys.value = v },
})
const bulkRevokeBusy = ref(false)

function onCodesFilter() {
  // 筛选变化清空选择：老选择对应的行可能已不在结果里。
  // 不要在 loadCodes 内部清——批量作废后也要调 loadCodes()，在那里清会误清失败项
  selection.clear()
  loadCodes()
}

/** v2.59 空状态"清除筛选"：清空邀请码筛选条件 */
function clearCodesFilters() {
  invKeyword.value = ''
  invState.value = ''
  onCodesFilter()
}

async function bulkRevokeCodes() {
  const ids = selection.selectedKeys.value as number[]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `将作废 ${ids.length} 个邀请码？它们不能再被使用，但已产生的邀请关系不受影响。`,
      '批量作废邀请码',
      { type: 'warning', confirmButtonText: '作废', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  bulkRevokeBusy.value = true
  try {
    const res = await revokeInvitationCodes(ids)
    // 作废后行变不可选（is_active=false），留着选择无意义，全清
    selection.clear()
    ElMessage.success(res.message || `已作废 ${ids.length} 个邀请码`)
    await loadCodes()
  } catch {
    // 失败时不清空选择，保留可重试；错误由请求拦截器统一提示
  } finally {
    bulkRevokeBusy.value = false
  }
}

// --- 批量生成 ---
const genVisible = ref(false)
const genLoading = ref(false)
const genFormRef = ref<FormInstance>()
const genForm = ref({ owner_user_id: null as number | null, count: 5, max_uses: 100, expires_days: 0, whitelist: '' })
const genRules: FormRules = {
  owner_user_id: [{ required: true, message: '请选择归属用户', trigger: 'change' }],
  count: [
    { required: true, message: '请输入生成数量', trigger: 'blur' },
    { type: 'number', min: 1, max: 200, message: '数量需在 1–200 之间', trigger: 'blur' },
  ],
  max_uses: [{ type: 'number', min: 0, max: 100000, message: '次数需在 0–100000 之间', trigger: 'blur' }],
  expires_days: [{ type: 'number', min: 0, max: 3650, message: '天数需在 0–3650 之间', trigger: 'blur' }],
}
const genOwner = computed(() =>
  userOptions.value.find((u) => u.id === genForm.value.owner_user_id)?.username,
)

async function openGen() {
  genForm.value = { owner_user_id: null, count: 5, max_uses: 100, expires_days: 0, whitelist: '' }
  genVisible.value = true
  if (!userOptions.value.length) await searchUsers('')
}

async function handleGen() {
  try {
    await genFormRef.value?.validate()
  } catch {
    scrollToFirstError()
    return
  }
  genLoading.value = true
  try {
    const res = await generateInvitationCodes({
      owner_user_id: genForm.value.owner_user_id!,
      count: genForm.value.count,
      max_uses: genForm.value.max_uses,
      expires_days: genForm.value.expires_days,
      whitelist: genForm.value.whitelist,
    })
    ElMessage.success(res.message)
    genVisible.value = false
    await loadCodes()
  } catch {
    /* 拦截器已提示 */
  } finally {
    genLoading.value = false
  }
}

// --- 改单张 ---
const editVisible = ref(false)
const editLoading = ref(false)
const editFormRef = ref<FormInstance>()
const editForm = ref({ id: 0, code: '', max_uses: 100, expires_days: 0, whitelist: '', is_active: true })
const editRules: FormRules = {
  max_uses: [{ type: 'number', min: 0, max: 100000, message: '次数需在 0–100000 之间', trigger: 'blur' }],
  expires_days: [{ type: 'number', min: 0, max: 3650, message: '天数需在 0–3650 之间', trigger: 'blur' }],
}

function openEdit(row: InvitationCodeRow) {
  editForm.value = {
    id: row.id,
    code: row.code,
    max_uses: row.max_uses,
    // 有效天数按「从现在起」重算；0 = 永不过期
    expires_days: row.expires_days_left ?? 0,
    whitelist: row.whitelist.join(','),
    is_active: row.is_active,
  }
  editVisible.value = true
}

async function handleEdit() {
  try {
    await editFormRef.value?.validate()
  } catch {
    scrollToFirstError()
    return
  }
  editLoading.value = true
  try {
    const res = await updateInvitationCode(editForm.value.id, {
      max_uses: editForm.value.max_uses,
      expires_days: editForm.value.expires_days,
      whitelist: editForm.value.whitelist,
      is_active: editForm.value.is_active,
    })
    ElMessage.success('邀请码已更新')
    editVisible.value = false
    const idx = invCodes.value.findIndex((c) => c.id === res.code.id)
    if (idx >= 0) invCodes.value.splice(idx, 1, res.code)
  } catch {
    /* 拦截器已提示 */
  } finally {
    editLoading.value = false
  }
}

// --- 作废（可逆：只是把 is_active 翻成 false，行不删） ---
async function handleRevoke(rows: InvitationCodeRow[]) {
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(
      `将作废 ${rows.length} 个邀请码？它们不能再被使用，但已产生的邀请关系不受影响。`,
      '作废邀请码',
      { type: 'warning', confirmButtonText: '作废', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const res = await revokeInvitationCodes(rows.map((r) => r.id))
  ElMessage.success(res.message)
  await loadCodes()
}

// ===== 推广奖励：当前策略（可编辑）+ 发放记录台账 =====
// 策略写的是 SystemConfig 的 promotion_reward_* 四个键（PUT /promotion/policy），
// 与「系统设置 → 邀请返利」里的同名字段是同一份配置。
const auth = useAuthStore()
/** 只读角色（viewer）不能写；服务端 admin_roles 仍会自己判一次 */
const canWrite = computed(() => auth.admin?.can_write !== false)

const promo = ref<PromotionResponse | null>(null)
const promoLoading = ref(false)
const promoSaving = ref(false)

/** 编辑表单：加载后从服务端策略回填，前端不维护默认值 */
const promoForm = reactive({
  enabled: false,
  reward_type: 'days' as PromotionPolicy['reward_type'],
  days: 0,
})

function fillPromoForm(policy: PromotionPolicy) {
  promoForm.enabled = policy.enabled
  promoForm.reward_type = policy.reward_type
  promoForm.days = policy.days
}

async function loadPromotion() {
  promoLoading.value = true
  try {
    promo.value = await fetchPromotion({ limit: 100 })
    fillPromoForm(promo.value.policy)
  } catch {
    /* 拦截器已提示 */
  } finally {
    promoLoading.value = false
  }
}

const promoPolicy = computed(() => promo.value?.policy ?? null)

/** 类型标签以服务端给的为准，缺了再兜底 */
function promoTypeLabel(type: string): string {
  return promoPolicy.value?.reward_type_labels?.[type]
    ?? '有效期（天）'
}

/** 当前（已保存的）策略为什么不发：关着 / 当前档位数值为 0 */
const promoInactiveReason = computed(() => {
  const p = promoPolicy.value
  if (!p || p.active) return ''
  if (!p.enabled) return '推广奖励开关已关闭。'
  return '奖励类型为「有效期」，但天数为 0。'
})

/** 表单里的值是否和已保存的不同（没改就不让点保存） */
const promoDirty = computed(() => {
  const p = promoPolicy.value
  if (!p) return false
  return p.enabled !== promoForm.enabled
    || p.reward_type !== promoForm.reward_type
    || p.days !== promoForm.days
})

/** 按表单值预判保存后的效果，写进二次确认文案 */
function promoEffectText(): string {
  if (!promoForm.enabled) return '保存后推广奖励关闭：邀请成功不再给邀请人发推广奖励（渠道码专属奖励也不发）。'
  const value = promoForm.days
  if (value <= 0) return '保存后天数为 0：邀请成功不会给邀请人发放任何推广奖励。'
  return `保存后每成功邀请 1 人，邀请人加 ${value} 天会员有效期（归属当前服）。`
}

async function savePromotionPolicy() {
  if (!promoPolicy.value) return
  try {
    await ElMessageBox.confirm(
      `${promoEffectText()}只影响之后的邀请，已发放的记录不变。确认保存？`,
      '修改推广奖励策略',
      { type: 'warning', confirmButtonText: '确认保存', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  promoSaving.value = true
  try {
    const res = await updatePromotionPolicy({
      promotion_reward_enabled: promoForm.enabled,
      promotion_reward_type: promoForm.reward_type,
      promotion_reward_days: promoForm.days,
    })
    if (promo.value) promo.value = { ...promo.value, policy: res.policy }
    fillPromoForm(res.policy)
    ElMessage.success('推广奖励策略已保存')
  } catch {
    /* 拦截器已提示（只读角色会收到 403） */
  } finally {
    promoSaving.value = false
  }
}

function resetPromoForm() {
  if (promoPolicy.value) fillPromoForm(promoPolicy.value)
}

const promoColumns: DataColumn[] = [
  { key: 'inviter_username', label: '邀请人', width: 130, mobile: 'title' },
  { key: 'invitee_username', label: '被邀请人', width: 130 },
  { key: 'reward_type_label', label: '类型', width: 120 },
  { key: 'reward_value', label: '数值', width: 90 },
  { key: 'created_at', label: '时间' },
]

/** 邀请码状态 → 徽章配色 */
function codeBadge(state: string): string {
  if (state === 'active') return 'au-badge-green'
  if (state === 'revoked') return 'au-badge-muted'
  if (state === 'expired') return 'au-badge-rose'
  return 'badge-warn'
}

function reloadAll() {
  load()
  loadCodes()
  loadPromotion()
}

onMounted(reloadAll)
</script>

<template>
  <div class="admin-page invite-page">
    <PageHeader
      eyebrow="经营"
      title="邀请管理"
      description="邀请码的生成与台账、邀请记录、推广奖励（会员天数）都在这。"
    >
      <template #actions>
        <el-button :icon="RefreshCw" :loading="loading" @click="reloadAll">刷新</el-button>
      </template>
    </PageHeader>

    <section class="stat-row" aria-label="邀请概况">
      <StatTile label="累计邀请关系" :value="stats?.invitations ?? '—'" :icon="UserPlus" />
      <StatTile label="本页邀请记录" :value="inviteTotal" :icon="Gift" hint="最新 100 条" />
    </section>

    <!-- 页内锚点导航：5 个区块太长，点一下直达 -->
    <nav class="invite-anchors" aria-label="本页导航">
      <a href="#invite-codes">邀请码</a>
      <a href="#invite-records">邀请记录</a>
      <a href="#invite-promo">推广奖励</a>
    </nav>

    <!-- 邀请码管理（v2.44.0）：筛选在左、批量生成在右 -->
    <SectionCard
      id="invite-codes"
      title="邀请码"
      :icon="QrCode"
      :meta="`共 ${invSummary.total ?? 0} 张 · 可用 ${invSummary.active ?? 0} · 已用 ${invSummary.uses ?? 0} 次`"
      description="管理员维度发的渠道码，用户自己那张码（个人中心 → 邀请）不受影响。次数 0 = 不限，无有效期 = 永不过期，白名单留空 = 谁都能用。"
      flush
    >
      <div class="view-toolbar">
        <div class="view-toolbar__filters">
          <el-input
            v-model="invKeyword"
            class="f-search"
            placeholder="搜邀请码"
            clearable
            @change="onCodesFilter"
          >
            <template #prefix><Search :size="14" /></template>
          </el-input>
          <el-select v-model="invState" class="f-select" placeholder="全部状态" clearable @change="onCodesFilter">
            <el-option v-for="st in invStates" :key="st.value" :label="st.label" :value="st.value" />
          </el-select>
        </div>
        <div class="view-toolbar__actions">
          <el-button type="primary" :icon="Plus" @click="openGen">批量生成</el-button>
        </div>
      </div>

      <EmptyState v-if="codesError && !invCodes.length" :icon="AlertTriangle" title="邀请码加载失败" compact>
        <template #actions><el-button size="small" :loading="invLoading" @click="loadCodes">重试</el-button></template>
      </EmptyState>
      <DataTable
        v-else
        class="flush-table"
        :rows="invCodes"
        :columns="invColumns"
        :loading="invLoading"
        empty="还没有邀请码"
        empty-description="点「批量生成」给渠道或内测发一批。"
        :has-active-filters="!!(invKeyword || invState)"
        selectable
        v-model:selectedKeys="selectedKeys"
        :is-row-selectable="(row: InvitationCodeRow) => row.is_active"
        @clear-filters="clearCodesFilters"
      >
        <template #bulk-bar>
          <el-button type="danger" plain size="small" :loading="bulkRevokeBusy" @click="bulkRevokeCodes">批量作废</el-button>
        </template>
        <template #cell-code="{ row }">
          <span class="inv-code mono">{{ row.code }}</span>
        </template>
        <template #cell-owner_username="{ row }">
          <span class="user-name">{{ row.owner_username }}</span>
        </template>
        <template #cell-use_count="{ row }"><span class="num">{{ usesText(row) }}</span></template>
        <template #cell-state_label="{ row }">
          <span class="au-badge" :class="codeBadge(row.state)">{{ row.state_label }}</span>
        </template>
        <template #cell-expires_at="{ row }">
          <span v-if="!row.expires_at" class="faint">永久</span>
          <span v-else class="num">{{ fmtTime(row.expires_at) }}</span>
        </template>
        <template #cell-whitelist="{ row }">
          <span v-if="!row.whitelist.length" class="faint">不限</span>
          <span v-else>{{ row.whitelist.join('、') }}</span>
        </template>
        <template #cell-actions="{ row }">
          <div class="row-actions">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.is_active" size="small" type="danger" plain @click="handleRevoke([row])">作废</el-button>
          </div>
        </template>
      </DataTable>
    </SectionCard>

    <!-- 邀请记录 -->
      <SectionCard id="invite-records" title="邀请记录" :icon="UserPlus" :meta="`最新 ${invitations.length} 条`" flush>
        <EmptyState v-if="invError && !invitations.length" :icon="AlertTriangle" title="邀请记录加载失败" compact>
          <template #actions><el-button size="small" :loading="loading" @click="loadOverview">重试</el-button></template>
        </EmptyState>
        <DataTable v-else class="flush-table" :rows="invitations" :columns="inviteColumns" :loading="loading" empty="暂无邀请记录">
          <template #cell-inviter="{ row }">
            <span class="user-name">{{ row.inviter }}</span>
          </template>
          <template #cell-invitee="{ row }">{{ row.invitee }}</template>
          <template #cell-created_at="{ row }"><span class="num">{{ fmtTime(row.created_at) }}</span></template>
          <template #empty>
            <EmptyState compact :icon="UserPlus" title="暂无邀请记录" description="用户通过邀请码注册后会记在这里。" />
          </template>
        </DataTable>
      </SectionCard>

    <!-- 推广奖励：当前策略（可编辑，PUT /promotion/policy）+ 发放记录台账 -->
    <SectionCard
      id="invite-promo"
      :icon="Megaphone"
      description="邀请成功后给邀请人的那一笔（会员天数），全走配置。出厂开关为开、天数为 0，所以不改数值就不会发。"
      flush
    >
      <template #title>
        推广奖励
      </template>

      <EmptyState
        v-if="!promo && !promoLoading"
        :icon="AlertTriangle"
        title="推广奖励配置加载失败"
        compact
      >
        <template #actions><el-button size="small" @click="loadPromotion">重试</el-button></template>
      </EmptyState>

      <template v-else>
        <div v-if="promoPolicy" class="promo-policy">
          <el-alert
            v-if="!promoPolicy.active"
            type="warning"
            show-icon
            :closable="false"
            class="promo-zero-alert"
            title="当前邀请成功不会发放任何奖励"
          >
            <template #default>
              {{ promoInactiveReason }}需要发奖请在下方填写天数并保存。
            </template>
          </el-alert>
          <div v-else class="promo-active-line">
            <span class="au-badge au-badge-green">生效中</span>
            <span>
              每成功邀请 1 人，邀请人得
              <strong class="num">
                {{ `${promoPolicy.days} 天会员有效期` }}
              </strong>
            </span>
          </div>

          <el-form label-position="top" class="promo-form" :disabled="!canWrite || promoSaving" @submit.prevent>
            <el-form-item label="推广奖励开关">
              <el-switch v-model="promoForm.enabled" active-text="开启" inactive-text="关闭" />
            </el-form-item>
            <el-form-item label="奖励类型">
              <el-radio-group v-model="promoForm.reward_type">
                <el-radio-button
                  v-for="type in promoPolicy.reward_types"
                  :key="type"
                  :value="type"
                >
                  {{ promoTypeLabel(type) }}
                </el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="有效期天数（类型为「有效期」时生效，0 = 不发）">
              <el-input-number
                v-model="promoForm.days"
                :min="0"
                :max="3650"
                :precision="0"
                aria-label="推广奖励有效期天数"
              />
              <span class="field-suffix">天</span>
            </el-form-item>
          </el-form>
          <div class="promo-actions">
            <span v-if="!canWrite" class="promo-hint">当前账号是只读角色，只能查看。</span>
            <span v-else class="promo-hint">保存前会再确认一次；只影响之后的邀请。</span>
            <el-button :disabled="!promoDirty || promoSaving" @click="resetPromoForm">撤销修改</el-button>
            <el-button
              type="primary"
              :icon="Save"
              :loading="promoSaving"
              :disabled="!canWrite || !promoDirty"
              @click="savePromotionPolicy"
            >
              保存策略
            </el-button>
          </div>
        </div>

        <DataTable
          class="flush-table"
          :rows="promo?.rewards || []"
          :columns="promoColumns"
          :loading="promoLoading"
          empty="还没有推广奖励记录"
          empty-description="策略生效后，有人通过邀请注册成功，奖励记录会出现在这里。"
        >
          <template #cell-inviter_username="{ row }">
            <span class="user-name">{{ row.inviter_username || '—' }}</span>
          </template>
          <template #cell-invitee_username="{ row }">{{ row.invitee_username }}</template>
          <template #cell-reward_type_label="{ row }">
            <span class="au-badge au-badge-info">{{ row.reward_type_label }}</span>
          </template>
          <template #cell-reward_value="{ row }">
            <span class="amt-in">+{{ row.reward_value }}</span>
          </template>
          <template #cell-created_at="{ row }"><span class="num">{{ fmtTime(row.created_at) }}</span></template>
          <template #empty>
            <EmptyState
              compact
              :icon="Megaphone"
              title="还没有推广奖励记录"
              description="策略生效（开关开且数值大于 0）后，邀请成功发出的奖励会记在这里。"
            />
          </template>
        </DataTable>
      </template>
    </SectionCard>

    <!-- 批量生成邀请码 -->
    <el-dialog v-model="genVisible" title="批量生成邀请码" width="min(460px, 92vw)">
      <el-form ref="genFormRef" :model="genForm" :rules="genRules" label-position="top">
        <el-form-item label="归属用户" prop="owner_user_id">
          <el-select
            v-model="genForm.owner_user_id"
            filterable
            remote
            reserve-keyword
            :remote-method="searchUsers"
            :loading="userSearching"
            placeholder="搜索用户名（这些码都算他的邀请）"
            style="width: 100%"
          >
            <el-option v-for="u in userOptions" :key="u.id" :label="u.username" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="生成数量" prop="count">
          <el-input-number v-model="genForm.count" :min="1" :max="200" style="width: 100%" />
          <div class="form-hint">1–200 张，同一规格，每张码不同</div>
        </el-form-item>
        <el-form-item label="每码可用次数" prop="max_uses">
          <el-input-number v-model="genForm.max_uses" :min="0" :max="100000" style="width: 100%" />
          <div class="form-hint">0 = 不限次数</div>
        </el-form-item>
        <el-form-item label="有效天数" prop="expires_days">
          <el-input-number v-model="genForm.expires_days" :min="0" :max="3650" style="width: 100%" />
          <div class="form-hint">0 = 永不过期；从生成那一刻开始算</div>
        </el-form-item>
        <el-form-item label="白名单（可选）">
          <el-input v-model="genForm.whitelist" placeholder="留空 = 谁都能用；多个用户名用逗号分隔"
                    type="textarea" :rows="2" />
          <div class="form-hint">填了就只有这些人能拿这个码注册（内测码 / 渠道码）</div>
        </el-form-item>
        <div v-if="genOwner" class="adjust-preview">将为「{{ genOwner }}」生成 {{ genForm.count }} 个邀请码</div>
      </el-form>
      <template #footer>
        <el-button @click="genVisible = false">取消</el-button>
        <el-button type="primary" :loading="genLoading" @click="handleGen">生成</el-button>
      </template>
    </el-dialog>

    <!-- 改单张邀请码 -->
    <el-dialog v-model="editVisible" :title="`编辑邀请码 ${editForm.code}`" width="min(460px, 92vw)">
      <el-form ref="editFormRef" :model="editForm" :rules="editRules" label-position="top">
        <el-form-item label="每码可用次数" prop="max_uses">
          <el-input-number v-model="editForm.max_uses" :min="0" :max="100000" style="width: 100%" />
          <div class="form-hint">0 = 不限次数</div>
        </el-form-item>
        <el-form-item label="有效天数（从现在起）" prop="expires_days">
          <el-input-number v-model="editForm.expires_days" :min="0" :max="3650" style="width: 100%" />
          <div class="form-hint">0 = 永不过期</div>
        </el-form-item>
        <el-form-item label="白名单">
          <el-input v-model="editForm.whitelist" placeholder="留空 = 不限；多个用户名用逗号分隔"
                    type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="editForm.is_active" active-text="可用" inactive-text="已作废" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editLoading" @click="handleEdit">保存</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<style scoped>
.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
}

/* ---------- 页内锚点导航 ---------- */
.invite-page {
  scroll-behavior: smooth;
}

.invite-anchors {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 16px;
}

.invite-anchors a {
  font-size: 12px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--au-border);
  color: var(--au-text-3);
  background: var(--au-surface);
  text-decoration: none;
  transition: border-color 0.2s ease, color 0.2s ease;
}

.invite-anchors a:hover {
  border-color: var(--au-primary);
  color: var(--au-text);
}

.invite-page .au-section[id] {
  scroll-margin-top: 76px;
}

/* ---------- 工具条：筛选在左、动作在右 ---------- */
.view-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px 12px;
  flex-wrap: wrap;
  padding: 4px 20px 14px;
  border-bottom: 1px solid var(--au-border);
}

.view-toolbar__filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: 1 1 auto; min-width: 0; }
.view-toolbar__actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.f-search { width: 200px; }
.f-select { width: 140px; }

.flush-table :deep(.dt-cards) { padding: 12px 12px 8px; }
.card-pager { display: flex; justify-content: flex-end; flex-wrap: wrap; row-gap: 8px; }

/* ---------- 单元格 ---------- */
.user-name { font-weight: 600; color: var(--au-text); }
.num { font-variant-numeric: tabular-nums; }
.faint { color: var(--au-text-4); }
.amt-in { color: var(--au-success); font-weight: 600; font-variant-numeric: tabular-nums; }
.amt-out { color: var(--au-danger); font-weight: 600; font-variant-numeric: tabular-nums; }
.inv-code { font-size: 13px; color: var(--au-text); letter-spacing: 0.04em; }
.row-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.badge-warn { background: var(--au-warning-soft); color: var(--au-warning); border-color: var(--au-warning-border); }

.field-suffix { font-size: 12px; color: var(--au-text-3); }

/* ---------- 推广奖励策略 ---------- */
.promo-policy {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 20px 16px;
  border-bottom: 1px solid var(--au-border);
}
.promo-active-line { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--au-text-2); }
.promo-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 0 16px;
}
.promo-form :deep(.el-form-item__content) { gap: 8px; flex-wrap: nowrap; }
.promo-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.promo-hint { font-size: 12px; color: var(--au-text-3); margin-right: auto; }

/* ---------- 对话框 ---------- */
.adjust-preview {
  padding: 10px 12px;
  border-radius: var(--au-r-md);
  background: var(--au-primary-soft);
  border: 1px solid var(--au-primary-border);
  color: var(--au-primary);
  font-size: 12px;
}

@media (max-width: 768px) {
  .view-toolbar { padding: 2px 16px 12px; }
  .view-toolbar__filters > .f-search,
  .view-toolbar__filters > .f-select { flex: 1 1 140px; width: auto; }
  .view-toolbar__actions { width: 100%; }
  .view-toolbar__actions > .el-button { flex: 1; }
  .card-pager { justify-content: center; }
  .promo-policy { padding: 2px 16px 14px; }
}
</style>
