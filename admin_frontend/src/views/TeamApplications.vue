<template>
  <div class="team-apps">
    <header class="team-apps__head">
      <div>
        <h1 class="team-apps__title">团队申请</h1>
        <p class="team-apps__sub">
          用户提交的团队套餐申请（意向单）。通过即给该团登记套餐开通；付款与开通细节走客服确认
          （本站未接在线支付与团队流量计量，审核只是人工开通登记）。
        </p>
        <p class="team-apps__sub">
          套餐口径：起订流量 {{ config ? fmtBytes(config.team_plan_min_traffic_bytes) : '—' }}、子账号上限
          {{ config?.team_plan_max_members ?? '—' }} 个——数值在下方「团队配置」里改，不写死在页面。
        </p>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="load">刷新</button>
    </header>

    <!-- 团队配置（系统配置 team 段，改完立即生效） -->
    <section class="card">
      <h2 class="card__title">团队配置</h2>
      <div class="grid3">
        <label class="field">
          <span class="field__label">套餐起订流量（TB）</span>
          <input v-model.number="cfgForm.minTrafficTb" class="input" type="number" min="0" step="1" />
        </label>
        <label class="field">
          <span class="field__label">套餐子账号上限（人）</span>
          <input v-model.number="cfgForm.maxMembers" class="input" type="number" min="1" step="1" />
        </label>
        <label class="field">
          <span class="field__label">未开通套餐的团队成员上限（人）</span>
          <input v-model.number="cfgForm.defaultLimit" class="input" type="number" min="1" step="1" />
        </label>
      </div>
      <div class="row-actions">
        <button type="button" class="btn btn--primary" :disabled="savingCfg" @click="saveConfig">
          保存配置
        </button>
        <span v-if="cfgMsg" class="hint">{{ cfgMsg }}</span>
      </div>
    </section>

    <!-- 申请列表 -->
    <section class="card">
      <div class="tabs" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.value"
          type="button"
          class="tab"
          :class="{ 'tab--on': statusFilter === t.value }"
          @click="setFilter(t.value)"
        >
          {{ t.label }}
        </button>
      </div>

      <p v-if="error" class="err">{{ error }}</p>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">暂无申请</div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>申请人</th>
            <th>团队</th>
            <th>线路</th>
            <th>总流量</th>
            <th>子账号</th>
            <th>备注</th>
            <th>提交时间</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in items" :key="a.id">
            <td>{{ a.username }}（#{{ a.user_id }}）</td>
            <td>{{ a.team_name || '—' }}</td>
            <td>{{ a.line || '—' }}</td>
            <td>{{ fmtBytes(a.traffic_bytes) }}</td>
            <td>{{ a.member_limit }}</td>
            <td class="note">{{ a.note || '—' }}</td>
            <td>{{ fmtDateTime(a.created_at) }}</td>
            <td>
              <span class="chip" :class="`chip--${a.status}`">{{ statusText(a.status) }}</span>
              <span v-if="a.review_note" class="review-note">{{ a.review_note }}</span>
            </td>
            <td>
              <template v-if="a.status === 'pending'">
                <button
                  type="button"
                  class="btn btn--sm btn--primary"
                  :disabled="busyId === a.id"
                  @click="doApprove(a)"
                >
                  通过
                </button>
                <button
                  type="button"
                  class="btn btn--sm btn--danger"
                  :disabled="busyId === a.id"
                  @click="doReject(a)"
                >
                  驳回
                </button>
              </template>
              <span v-else class="hint">{{ a.reviewed_at ? fmtDateTime(a.reviewed_at) : '' }}</span>
            </td>
          </tr>
        </tbody>
      </table>

    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
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

const TB = 10 ** 12

const items = ref<AdminTeamApplication[]>([])
const loading = ref(false)
const error = ref('')
const busyId = ref<number | null>(null)
const statusFilter = ref<'' | 'pending' | 'approved' | 'rejected'>('pending')

const tabs = [
  { value: 'pending' as const, label: '待审核' },
  { value: '' as const, label: '全部' },
  { value: 'approved' as const, label: '已通过' },
  { value: 'rejected' as const, label: '已驳回' },
]

const config = ref<TeamConfig | null>(null)
const cfgForm = reactive({ minTrafficTb: 10, maxMembers: 100, defaultLimit: 10 })
const savingCfg = ref(false)
const cfgMsg = ref('')

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

function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

function setFilter(v: typeof statusFilter.value) {
  statusFilter.value = v
  void load()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listTeamApplications({ status: statusFilter.value })
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

<style scoped>
.team-apps { display: grid; gap: 16px; }
.team-apps__head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.team-apps__title { margin: 0; font-size: 22px; font-weight: 700; }
.team-apps__sub { margin: 6px 0 0; font-size: 13px; color: var(--el-text-color-secondary); max-width: 760px; line-height: 1.6; }
.card { background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 16px; }
.card__title { margin: 0 0 12px; font-size: 16px; font-weight: 600; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.field { display: grid; gap: 6px; }
.field__label { font-size: 13px; color: var(--el-text-color-regular); }
.input { height: 34px; padding: 0 10px; border: 1px solid var(--el-border-color); border-radius: 6px; background: var(--el-bg-color); color: var(--el-text-color-primary); font-size: 14px; }
.row-actions { margin-top: 12px; display: flex; align-items: center; gap: 12px; }
.tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.tab { padding: 6px 14px; border-radius: 999px; border: 1px solid var(--el-border-color); background: transparent; color: var(--el-text-color-primary); cursor: pointer; font-size: 13px; }
.tab--on { background: var(--el-color-primary); border-color: var(--el-color-primary); color: #fff; }
.table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.table th, .table td { padding: 8px 10px; border-bottom: 1px solid var(--el-border-color-lighter); text-align: left; vertical-align: top; }
.table th { color: var(--el-text-color-secondary); font-weight: 500; }
.note { max-width: 220px; white-space: pre-wrap; word-break: break-word; }
.chip { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; border: 1px solid var(--el-border-color); }
.chip--pending { color: var(--el-color-warning); border-color: var(--el-color-warning); }
.chip--approved { color: var(--el-color-success); border-color: var(--el-color-success); }
.chip--rejected { color: var(--el-color-danger); border-color: var(--el-color-danger); }
.review-note { display: block; margin-top: 4px; font-size: 12px; color: var(--el-text-color-secondary); }
.btn { padding: 7px 14px; border-radius: 6px; border: 1px solid var(--el-border-color); background: transparent; color: var(--el-text-color-primary); cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.5; cursor: default; }
.btn--primary { background: var(--el-color-primary); border-color: var(--el-color-primary); color: #fff; }
.btn--danger { color: var(--el-color-danger); border-color: var(--el-color-danger); }
.btn--sm { padding: 4px 10px; margin-right: 6px; }
.empty { padding: 32px 0; text-align: center; color: var(--el-text-color-secondary); }
.err { color: var(--el-color-danger); font-size: 13.5px; }
.hint { font-size: 12.5px; color: var(--el-text-color-secondary); }
@media (max-width: 900px) { .grid3 { grid-template-columns: 1fr; } }
</style>
