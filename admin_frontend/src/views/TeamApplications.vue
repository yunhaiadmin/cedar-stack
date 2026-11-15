<script setup lang="ts">
/**
 * 团队申请（意向单审核 + 团队配置）。
 *
 * UI 统一批 3：全自绘原生控件收编 EP 控件＋共享原语（PageHeader/SectionCard/
 * DataTable、el-tabs、el-tag、el-input）。行为级改动逐项核对过：
 * - 配置三输入保留原生 number 语义：el-input type="number" + v-model.number，
 *   min/step 属性照旧透传，不用 el-input-number（避免失焦钳制改变提交值），
 *   保存换算公式（|| 0 归一、TB 换算）一字未动；
 * - 状态页签改 el-tabs：点页签即重拉列表（@tab-change 在 v-model 更新后触发，
 *   等价于原 setFilter+load）；仅「点已选中的页签」不再重拉（原自绘会重拉），
 *   刷新走页头「刷新」钮；
 * - 通过/驳回的确认框、忙碌禁用条件（busyId）、错误文案口径全部原样。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveTeamApplication,
  getTeamConfig,
  listTeamApplications,
  rejectTeamApplication,
  updateTeamConfig,
  type AdminTeamApplication,
  type TeamConfig,
} from '@/api/adminTeams'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'

const TB = 10 ** 12

const items = ref<AdminTeamApplication[]>([])
const loading = ref(false)
const error = ref('')
const busyId = ref<number | null>(null)

type TabName = 'pending' | 'all' | 'approved' | 'rejected'
const activeTab = ref<TabName>('pending')
/** 与原 statusFilter 同值域：'all' 页签对应接口的空状态参数 */
const statusParam = computed<'' | 'pending' | 'approved' | 'rejected'>(() =>
  activeTab.value === 'all' ? '' : activeTab.value,
)

const tabs: { name: TabName; label: string }[] = [
  { name: 'pending', label: '待审核' },
  { name: 'all', label: '全部' },
  { name: 'approved', label: '已通过' },
  { name: 'rejected', label: '已驳回' },
]

const columns: DataColumn[] = [
  { key: 'applicant', label: '申请人', minWidth: 115, mobile: 'title' },
  { key: 'team_name', label: '团队', minWidth: 90 },
  { key: 'line', label: '线路', width: 90 },
  { key: 'traffic', label: '总流量', width: 80 },
  { key: 'member_limit', label: '子账号', width: 70 },
  { key: 'note', label: '备注', minWidth: 90 },
  { key: 'created_at', label: '提交时间', width: 140 },
  { key: 'status', label: '状态', width: 100 },
  { key: 'actions', label: '操作', width: 140 },
]

const config = ref<TeamConfig | null>(null)
const cfgForm = reactive({ minTrafficTb: 10, maxMembers: 100, defaultLimit: 10 })
const savingCfg = ref(false)
const cfgMsg = ref('')

const configDesc = computed(
  () =>
    `套餐口径：起订流量 ${config.value ? fmtBytes(config.value.team_plan_min_traffic_bytes) : '—'}、` +
    `子账号上限 ${config.value?.team_plan_max_members ?? '—'} 个——数值在本卡里改，不写死在页面。`,
)

function fmtBytes(bytes: number): string {
  if (!bytes) return '—'
  if (bytes >= TB) {
    const tb = bytes / TB
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`
  }
  const gb = bytes / 10 ** 9
  return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function statusText(s: AdminTeamApplication['status']): string {
  if (s === 'approved') return '已通过'
  if (s === 'rejected') return '已驳回'
  return '待审核'
}

function statusTagType(s: AdminTeamApplication['status']): 'warning' | 'success' | 'danger' {
  if (s === 'approved') return 'success'
  if (s === 'rejected') return 'danger'
  return 'warning'
}

function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

function onTabChange() {
  void load()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listTeamApplications({ status: statusParam.value })
    items.value = res.applications
  } catch (e) {
    error.value = errText(e, '申请列表加载失败')
  } finally {
    loading.value = false
  }
}

async function loadConfig() {
  try {
    const cfg = await getTeamConfig()
    config.value = cfg
    cfgForm.minTrafficTb = Math.round(cfg.team_plan_min_traffic_bytes / TB)
    cfgForm.maxMembers = cfg.team_plan_max_members
    cfgForm.defaultLimit = cfg.team_default_member_limit
  } catch {
    /* 配置读不到就只展示申请列表，保存时后端会再校验 */
  }
}

async function saveConfig() {
  savingCfg.value = true
  cfgMsg.value = ''
  try {
    const saved = await updateTeamConfig({
      team_plan_min_traffic_bytes: Math.round((cfgForm.minTrafficTb || 0) * TB),
      team_plan_max_members: cfgForm.maxMembers || 0,
      team_default_member_limit: cfgForm.defaultLimit || 0,
    })
    config.value = saved
    cfgMsg.value = '已保存，立即生效'
  } catch (e) {
    ElMessage.error(errText(e, '配置保存失败'))
  } finally {
    savingCfg.value = false
  }
}

async function doApprove(a: AdminTeamApplication) {
  try {
    await ElMessageBox.confirm(
      `通过「${a.username}」的团队套餐申请（${fmtBytes(a.traffic_bytes)} · ${a.member_limit} 个子账号）？` +
        '通过后该团会登记为已开通套餐；如申请人还没有团队，会按申请的团队名自动建团。',
      '通过申请',
      { confirmButtonText: '通过', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  busyId.value = a.id
  try {
    await approveTeamApplication(a.id)
    ElMessage.success('已通过，套餐已登记开通')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '通过失败'))
  } finally {
    busyId.value = null
  }
}

async function doReject(a: AdminTeamApplication) {
  let note = ''
  try {
    const res = await ElMessageBox.prompt('填写驳回原因（会展示给申请人，可留空）', '驳回申请', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputPlaceholder: '如：信息不完整，请补充后重新提交',
    })
    note = (res.value || '').trim()
  } catch {
    return
  }
  busyId.value = a.id
  try {
    await rejectTeamApplication(a.id, note)
    ElMessage.success('已驳回')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '驳回失败'))
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
    <PageHeader eyebrow="经营" title="团队申请">
      <template #description>
        用户提交的团队套餐申请（意向单）。通过即给该团登记套餐开通；付款与开通细节走客服确认
        （本站未接在线支付与团队流量计量，审核只是人工开通登记）。
      </template>
      <template #actions>
        <el-button :disabled="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 团队配置（系统配置 team 段，改完立即生效） -->
    <SectionCard title="团队配置" :description="configDesc">
      <el-form label-position="top">
        <div class="cfg-grid">
          <el-form-item label="套餐起订流量（TB）">
            <el-input v-model.number="cfgForm.minTrafficTb" type="number" min="0" step="1" />
          </el-form-item>
          <el-form-item label="套餐子账号上限（人）">
            <el-input v-model.number="cfgForm.maxMembers" type="number" min="1" step="1" />
          </el-form-item>
          <el-form-item label="未开通套餐的团队成员上限（人）">
            <el-input v-model.number="cfgForm.defaultLimit" type="number" min="1" step="1" />
          </el-form-item>
        </div>
        <div class="row-actions">
          <el-button type="primary" :disabled="savingCfg" @click="saveConfig">保存配置</el-button>
          <span v-if="cfgMsg" class="hint">{{ cfgMsg }}</span>
        </div>
      </el-form>
    </SectionCard>

    <!-- 申请列表 -->
    <SectionCard title="申请列表" flush>
      <div class="list-head">
        <el-tabs v-model="activeTab" @tab-change="onTabChange">
          <el-tab-pane v-for="t in tabs" :key="t.name" :label="t.label" :name="t.name" />
        </el-tabs>
        <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="list-error" />
      </div>
      <DataTable :rows="items" :columns="columns" :loading="loading" empty="暂无申请">
        <template #cell-applicant="{ row }">{{ row.username }}（#{{ row.user_id }}）</template>
        <template #cell-team_name="{ row }">{{ row.team_name || '—' }}</template>
        <template #cell-line="{ row }">{{ row.line || '—' }}</template>
        <template #cell-traffic="{ row }">{{ fmtBytes(row.traffic_bytes) }}</template>
        <template #cell-note="{ row }"><span class="note-cell">{{ row.note || '—' }}</span></template>
        <template #cell-created_at="{ row }">{{ fmtDateTime(row.created_at) }}</template>
        <template #cell-status="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          <span v-if="row.review_note" class="review-note">{{ row.review_note }}</span>
        </template>
        <template #cell-actions="{ row }">
          <template v-if="row.status === 'pending'">
            <el-button size="small" type="primary" :disabled="busyId === row.id" @click="doApprove(row)">通过</el-button>
            <el-button size="small" type="danger" :disabled="busyId === row.id" @click="doReject(row)">驳回</el-button>
          </template>
          <span v-else class="hint">{{ row.reviewed_at ? fmtDateTime(row.reviewed_at) : '' }}</span>
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>

<style scoped>
.cfg-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.row-actions { margin-top: 12px; display: flex; align-items: center; gap: 12px; }
.hint { font-size: 12.5px; color: var(--au-text-3); }
.list-head { padding: 8px 16px 0; }
.list-error { margin-bottom: 12px; }
.note-cell { display: inline-block; max-width: 220px; white-space: pre-wrap; word-break: break-word; }
.review-note { display: block; margin-top: 4px; font-size: 12px; color: var(--au-text-3); }
@media (max-width: 900px) { .cfg-grid { grid-template-columns: 1fr; } }
</style>
