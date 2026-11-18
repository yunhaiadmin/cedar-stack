<script setup lang="ts">
/**
 * 团队管理（2026-11-18 重建批 3）。
 *
 * 管理面＝购买制团队的运营入口：团长钱包购买后团队是「待开通」态，
 * 管理员确认客服已审核后点「开通播放权限」，成员才能正常使用；
 * 购买开关/起订量/单价/锚点表等配置同页可改，保存即生效。
 * #120 的意向单审核（申请列表＋通过/驳回）已随批 1 的购买制退役：
 * 没有 team_applications 表，本页不复活它。
 * 版式：PageHeader + SectionCard + FilterBar + DataTable，只用 --au-* 令牌。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { AppliedFilter, FilterConfig } from '@/components/FilterBar.vue'
import {
  activateTeam,
  getTeamConfig,
  getTeamDetail,
  listTeams,
  updateTeamConfig,
  type AdminTeam,
  type AdminTeamDetailPayload,
  type TeamConfig,
  type TeamStatus,
} from '@/api/adminTeams'

const GB = 1024 ** 3
const PAGE_SIZE = 20

const items = ref<AdminTeam[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref('')
const busyId = ref<number | null>(null)

const statusFilter = ref<'' | TeamStatus>('')
const searchText = ref('')
const appliedQ = ref('')

const STATUS_OPTIONS: { value: '' | TeamStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending_activation', label: '待开通' },
  { value: 'active', label: '已开通' },
  { value: 'dissolved', label: '已解散' },
]

const statusLabels: Record<TeamStatus, string> = {
  pending_activation: '待开通',
  active: '已开通',
  dissolved: '已解散',
}

const filters = computed<FilterConfig[]>(() => [
  {
    key: 'status',
    label: '状态',
    type: 'select',
    value: statusFilter.value,
    options: STATUS_OPTIONS,
  },
])

const appliedFilters = computed<AppliedFilter[]>(() => {
  const list: AppliedFilter[] = []
  if (statusFilter.value) {
    list.push({
      key: 'status',
      label: '状态',
      text: statusLabels[statusFilter.value],
    })
  }
  if (appliedQ.value) list.push({ key: 'q', label: '搜索', text: appliedQ.value })
  return list
})

const columns: DataColumn[] = [
  { key: 'team', label: '团队', minWidth: 110, mobile: 'title' },
  { key: 'status', label: '状态', width: 80 },
  { key: 'line', label: '线路', width: 90 },
  { key: 'traffic', label: '购买流量', width: 90 },
  { key: 'members', label: '成员', width: 70 },
  { key: 'price', label: '实付', width: 80 },
  { key: 'expires', label: '有效期至', width: 100 },
  { key: 'created', label: '创建时间', width: 120 },
  { key: 'actions', label: '操作', width: 190 },
]

const memberColumns: DataColumn[] = [
  { key: 'member', label: '成员', minWidth: 120, mobile: 'title' },
  { key: 'role', label: '角色', width: 80 },
  { key: 'quota', label: '配额', width: 100 },
  { key: 'used', label: '已用', width: 140 },
  { key: 'reset', label: '重置日', width: 120 },
  { key: 'joined', label: '加入时间', width: 140 },
]

const orderColumns: DataColumn[] = [
  { key: 'order', label: '订单号', minWidth: 150, mobile: 'title' },
  { key: 'line', label: '线路', width: 90 },
  { key: 'traffic', label: '流量', width: 100 },
  { key: 'months', label: '时长', width: 80 },
  { key: 'amount', label: '实付', width: 100 },
  { key: 'created', label: '下单时间', width: 140 },
]

const config = ref<TeamConfig | null>(null)
const cfgForm = reactive({
  purchaseEnabled: true,
  premiumEnabled: false,
  globalMinGb: 10000,
  premiumMinGb: 5000,
  maxGb: 1000000,
  premiumPriceFen: 400,
  divisor: 100,
  anchorsText: '',
})
const savingCfg = ref(false)
const cfgMsg = ref('')

const configDesc = computed(() => {
  if (!config.value) return '购买开关、起订量、单价与锚点表——数值在本卡里改，保存即生效。'
  return `当前：购买${config.value.team_purchase_enabled ? '开启' : '关闭'}、` +
    `Premium ${config.value.team_premium_enabled ? '开售' : '暂停服务'}、` +
    `Global 起订 ${config.value.team_global_min_traffic_gb} GB、` +
    `Premium 起订 ${config.value.team_premium_min_traffic_gb} GB、` +
    `Premium 单价 $${(config.value.team_premium_price_per_1000gb_fen / 100).toFixed(2)}/千GB。`
})

const selected = ref<AdminTeamDetailPayload | null>(null)
const detailLoading = ref(false)

function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  if (bytes >= 10 ** 12) {
    const tb = bytes / 10 ** 12
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`
  }
  const gb = bytes / GB
  return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`
}

function fmtMoneyFen(fen: number | null | undefined): string {
  if (fen == null) return '—'
  return `$${(fen / 100).toFixed(2)}`
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function lineLabel(line: string | null | undefined): string {
  if (line === 'global') return 'Global'
  if (line === 'premium') return 'Premium'
  return line || '—'
}

function statusTagType(s: TeamStatus): 'warning' | 'success' | 'info' {
  if (s === 'active') return 'success'
  if (s === 'dissolved') return 'info'
  return 'warning'
}

function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listTeams({
      status: statusFilter.value,
      q: appliedQ.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    items.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = errText(e, '团队列表加载失败')
  } finally {
    loading.value = false
  }
}

function onFilterChange(key: string, value: FilterConfig['value']) {
  if (key === 'status') {
    statusFilter.value = value as '' | TeamStatus
    page.value = 1
    void load()
  }
}

function onSearch(value: string) {
  searchText.value = value
  appliedQ.value = value.trim()
  page.value = 1
  void load()
}

function onSearchSubmit() {
  appliedQ.value = searchText.value.trim()
  page.value = 1
  void load()
}

function onRemoveChip(key: string) {
  if (key === 'status') statusFilter.value = ''
  if (key === 'q') {
    appliedQ.value = ''
    searchText.value = ''
  }
  page.value = 1
  void load()
}

function onClearFilters() {
  statusFilter.value = ''
  appliedQ.value = ''
  searchText.value = ''
  page.value = 1
  void load()
}

function onPageChange(p: number) {
  page.value = p
  void load()
}

async function loadConfig() {
  try {
    const res = await getTeamConfig()
    config.value = res.config
    cfgForm.purchaseEnabled = res.config.team_purchase_enabled
    cfgForm.premiumEnabled = res.config.team_premium_enabled
    cfgForm.globalMinGb = res.config.team_global_min_traffic_gb
    cfgForm.premiumMinGb = res.config.team_premium_min_traffic_gb
    cfgForm.maxGb = res.config.team_max_traffic_gb
    cfgForm.premiumPriceFen = res.config.team_premium_price_per_1000gb_fen
    cfgForm.divisor = res.config.team_sub_account_divisor
    cfgForm.anchorsText = JSON.stringify(res.config.team_global_price_anchors, null, 2)
  } catch {
    /* 配置读不到就只展示团队列表，保存时后端会再校验 */
  }
}

async function saveConfig() {
  let anchors: unknown
  try {
    anchors = JSON.parse(cfgForm.anchorsText || '[]')
  } catch {
    ElMessage.error('Global 单价锚点表不是合法 JSON，请检查格式')
    return
  }
  savingCfg.value = true
  cfgMsg.value = ''
  try {
    const saved = await updateTeamConfig({
      team_purchase_enabled: cfgForm.purchaseEnabled,
      team_premium_enabled: cfgForm.premiumEnabled,
      team_global_min_traffic_gb: cfgForm.globalMinGb || 0,
      team_premium_min_traffic_gb: cfgForm.premiumMinGb || 0,
      team_max_traffic_gb: cfgForm.maxGb || 0,
      team_premium_price_per_1000gb_fen: cfgForm.premiumPriceFen || 0,
      team_sub_account_divisor: cfgForm.divisor || 0,
      team_global_price_anchors: anchors as TeamConfig['team_global_price_anchors'],
    })
    config.value = saved.config
    cfgMsg.value = '已保存，立即生效'
  } catch (e) {
    ElMessage.error(errText(e, '配置保存失败'))
  } finally {
    savingCfg.value = false
  }
}

async function selectTeam(team: AdminTeam) {
  detailLoading.value = true
  try {
    selected.value = await getTeamDetail(team.id)
  } catch (e) {
    ElMessage.error(errText(e, '团队详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

async function doActivate(team: AdminTeam) {
  try {
    await ElMessageBox.confirm(
      `开通「${team.name || team.owner_username || `#${team.id}`}」团队的播放权限？` +
        '开通后该团成员凭团队资格即可播放；请先确认客服已完成审核。',
      '开通播放权限',
      { confirmButtonText: '开通', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  busyId.value = team.id
  try {
    await activateTeam(team.id)
    ElMessage.success('已开通播放权限')
    await load()
    if (selected.value?.team.id === team.id) await selectTeam(team)
  } catch (e) {
    ElMessage.error(errText(e, '开通失败'))
  } finally {
    busyId.value = null
  }
}

onMounted(() => {
  void load()
  void loadConfig()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader eyebrow="经营" title="团队管理">
      <template #description>
        团长钱包购买后团队是「待开通」态：确认客服已完成审核后，在这里开通播放权限，
        成员才能凭团队资格正常使用。购买开关与计价口径在下方配置卡里改，保存即生效。
      </template>
      <template #actions>
        <el-button :disabled="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 团队配置（系统配置 team 段，改完立即生效） -->
    <SectionCard title="团队配置" :description="configDesc">
      <el-form label-position="top">
        <div class="cfg-grid">
          <el-form-item label="团队购买总开关">
            <el-switch v-model="cfgForm.purchaseEnabled" />
            <span class="hint">{{ cfgForm.purchaseEnabled ? '开启：允许新建团队购买' : '关闭：暂停新建团队购买' }}</span>
          </el-form-item>
          <el-form-item label="Premium 线开售">
            <el-switch v-model="cfgForm.premiumEnabled" />
            <span class="hint">{{ cfgForm.premiumEnabled ? '开启：Premium 线路可购买' : '关闭：Premium 线路暂停服务（存量 Premium 团队续费不受影响）' }}</span>
          </el-form-item>
          <el-form-item label="Global 起订流量（GB）">
            <el-input v-model.number="cfgForm.globalMinGb" type="number" min="1" step="1" />
          </el-form-item>
          <el-form-item label="Premium 起订流量（GB）">
            <el-input v-model.number="cfgForm.premiumMinGb" type="number" min="1" step="1" />
          </el-form-item>
          <el-form-item label="可购流量上限（GB）">
            <el-input v-model.number="cfgForm.maxGb" type="number" min="1" step="1" />
          </el-form-item>
          <el-form-item label="Premium 单价（美分 / 千GB）">
            <el-input v-model.number="cfgForm.premiumPriceFen" type="number" min="0" step="1" />
          </el-form-item>
          <el-form-item label="子账号除数（流量 GB / 除数＝子账号上限）">
            <el-input v-model.number="cfgForm.divisor" type="number" min="1" step="1" />
          </el-form-item>
        </div>
        <el-form-item label="Global 单价锚点表（JSON：[{gb, per_1000gb_fen}, …]，锚点间线性插值）">
          <el-input v-model="cfgForm.anchorsText" type="textarea" :rows="4" spellcheck="false" />
        </el-form-item>
        <div class="row-actions">
          <el-button type="primary" :disabled="savingCfg" @click="saveConfig">保存配置</el-button>
          <span v-if="cfgMsg" class="hint">{{ cfgMsg }}</span>
        </div>
      </el-form>
    </SectionCard>

    <!-- 团队列表 -->
    <SectionCard title="团队列表" flush>
      <div class="list-head">
        <FilterBar
          :search="{ value: searchText, placeholder: '搜索团队名 / 团长', mode: 'debounced' }"
          :filters="filters"
          :applied-filters="appliedFilters"
          :show-count="true"
          :count="total"
          :loading="loading"
          @update:search="onSearch"
          @search-submit="onSearchSubmit"
          @filter-change="onFilterChange"
          @remove-chip="onRemoveChip"
          @clear="onClearFilters"
        />
        <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="list-error" />
      </div>
      <DataTable
        :rows="items"
        :columns="columns"
        :loading="loading"
        :error="error"
        :pagination="{ total, page, pageSize: PAGE_SIZE }"
        :has-active-filters="appliedFilters.length > 0"
        empty="暂无团队"
        empty-description="团长在用户端购买团队套餐后会出现在这里，待开通的团队排在最前。"
        clickable
        @row-click="selectTeam"
        @page-change="onPageChange"
        @retry="load"
        @clear-filters="onClearFilters"
      >
        <template #cell-team="{ row }">
          {{ row.name || row.owner_username || `#${row.id}` }}
          <span class="sub-line">{{ row.owner_username ? `@${row.owner_username}` : '' }} #{{ row.id }}</span>
        </template>
        <template #cell-status="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabels[row.status] }}</el-tag>
        </template>
        <template #cell-line="{ row }">{{ lineLabel(row.plan_line) }}</template>
        <template #cell-traffic="{ row }">{{ fmtBytes(row.plan_traffic_bytes) }}</template>
        <template #cell-members="{ row }">
          {{ row.member_count }}{{ row.plan_member_limit ? ` / ${row.plan_member_limit}` : '' }}
        </template>
        <template #cell-price="{ row }">{{ fmtMoneyFen(row.plan_price_fen) }}</template>
        <template #cell-expires="{ row }">{{ fmtDate(row.plan_expires_at) }}</template>
        <template #cell-created="{ row }">{{ fmtDate(row.created_at) }}</template>
        <template #cell-actions="{ row }">
          <el-button size="small" @click.stop="selectTeam(row)">详情</el-button>
          <el-button
            v-if="row.status === 'pending_activation'"
            size="small"
            type="primary"
            :disabled="busyId === row.id"
            @click.stop="doActivate(row)"
          >
            开通播放权限
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <!-- 团队详情：成员＋购买订单 -->
    <SectionCard
      v-if="selected"
      title="团队详情"
      :meta="selected.team.name || selected.team.owner_username || `#${selected.team.id}`"
      flush
    >
      <div class="detail-body" v-loading="detailLoading">
        <div class="detail-head">
          <div>
            <strong>{{ selected.team.name || '未命名团队' }}</strong>
            <span class="hint">
              团长 @{{ selected.team.owner_username }} ·
              {{ lineLabel(selected.team.plan_line) }} ·
              {{ fmtBytes(selected.team.plan_traffic_bytes) }} ·
              {{ statusLabels[selected.team.status] }}
            </span>
          </div>
          <el-button
            v-if="selected.team.status === 'pending_activation'"
            type="primary"
            :disabled="busyId === selected.team.id"
            @click="doActivate(selected.team)"
          >
            开通播放权限
          </el-button>
        </div>
        <h3 class="detail-sub">成员账号</h3>
        <DataTable
          :rows="selected.members"
          :columns="memberColumns"
          empty="暂无成员"
        >
          <template #cell-member="{ row }">
            {{ row.username || `#${row.user_id}` }}
            <span class="sub-line">{{ row.public_id || '' }}</span>
          </template>
          <template #cell-role="{ row }">{{ row.role === 'owner' ? '团长' : '成员' }}</template>
          <template #cell-quota="{ row }">{{ fmtBytes(row.traffic_quota_bytes) }}</template>
          <template #cell-used="{ row }">
            {{ fmtBytes(row.traffic_used_bytes) }}{{ row.traffic_used_pct != null ? `（${row.traffic_used_pct}%）` : '' }}
          </template>
          <template #cell-reset="{ row }">{{ fmtDate(row.reset_date) }}</template>
          <template #cell-joined="{ row }">{{ fmtDateTime(row.joined_at) }}</template>
        </DataTable>
        <h3 class="detail-sub">购买订单</h3>
        <DataTable
          :rows="selected.orders"
          :columns="orderColumns"
          empty="暂无订单"
        >
          <template #cell-order="{ row }">{{ row.order_id }}</template>
          <template #cell-line="{ row }">{{ lineLabel(row.line) }}</template>
          <template #cell-traffic="{ row }">{{ row.traffic_gb }} GB</template>
          <template #cell-months="{ row }">{{ row.months }} 个月</template>
          <template #cell-amount="{ row }">{{ fmtMoneyFen(row.amount_fen) }}</template>
          <template #cell-created="{ row }">{{ fmtDateTime(row.created_at) }}</template>
        </DataTable>
      </div>
    </SectionCard>
  </div>
</template>

<style scoped>
.cfg-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.row-actions { margin-top: 12px; display: flex; align-items: center; gap: 12px; }
.hint { font-size: 12.5px; color: var(--au-text-3); }
.list-head { padding: 8px 16px 0; }
.list-error { margin: 8px 0 12px; }
.sub-line { display: block; font-size: 12px; color: var(--au-text-3); }
.detail-body { padding: 12px 16px 16px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.detail-head .hint { margin-left: 8px; }
.detail-sub { margin: 16px 0 8px; font-size: 14px; font-weight: 600; color: var(--au-text); }
@media (max-width: 900px) { .cfg-grid { grid-template-columns: 1fr; } }
</style>
