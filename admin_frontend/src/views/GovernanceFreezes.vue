<script setup lang="ts">
/**
 * 治理冻结（活动治理 Step 4 管理端，O-02 补缺）。
 *
 * 冻结的是用户的**治理特权**（举报 / 复核等治理参与权），不扣经验、
 * 不降级、不影响看片等核心权益。与「治理停权」标签（Step 2
 * GovernanceSuspension，恶意举报自动触发 / 手动停权）是两套独立
 * 机制，别混。
 *
 * 后端：backend/api/governance_admin.py
 * - GET  /governance/freezes        当前生效中的冻结列表
 * - POST /governance/freeze         新建冻结（理由必填，已有冻结 409）
 * - POST /governance/freeze/release 解冻（按用户，无生效中冻结 404）
 * - GET  /governance/audit-logs     治理审计日志（只读，无写接口）
 * 写操作双写审计（管理审计 + 治理审计日志），同一事务提交。
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCw, Snowflake } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import {
  createGovernanceFreeze,
  fetchGovernanceAuditLogs,
  fetchGovernanceFreezes,
  releaseGovernanceFreeze,
  type GovernanceAuditLogRow,
  type GovernanceFreezeRow,
} from '@/api/governance'

const freezeColumns: DataColumn[] = [
  { key: 'username', label: '用户', mobile: 'title' },
  { key: 'reason', label: '冻结理由', minWidth: 200 },
  { key: 'expires_at', label: '到期', width: 170 },
  { key: 'created_by_name', label: '冻结人', width: 130 },
  { key: 'created_at', label: '冻结时间', width: 170 },
  { key: 'actions', label: '操作', width: 100 },
]

const auditColumns: DataColumn[] = [
  { key: 'id', label: '日志', width: 80, mobile: 'title' },
  { key: 'action', label: '动作', width: 200 },
  { key: 'actor_name', label: '操作人', width: 130 },
  { key: 'target_name', label: '目标用户', width: 130 },
  { key: 'target_ref', label: '目标引用', width: 130 },
  { key: 'created_at', label: '时间', width: 170 },
]

const freezes = ref<GovernanceFreezeRow[]>([])
const freezesLoading = ref(false)
const releaseBusyId = ref<number | null>(null)

const auditLogs = ref<GovernanceAuditLogRow[]>([])
const auditLoading = ref(false)

const formUserId = ref<number | null>(null)
const formReason = ref('')
const formDays = ref<number | null>(null)
const freezeBusy = ref(false)

function formatTime(iso: string | null) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}

function userLabel(username: string | null, id: number | null) {
  if (username) return `${username}（#${id}）`
  return id != null ? `#${id}` : '—'
}

async function loadFreezes() {
  freezesLoading.value = true
  try {
    const d = await fetchGovernanceFreezes()
    freezes.value = d.items ?? []
  } catch {
    ElMessage.error('冻结列表加载失败')
  } finally {
    freezesLoading.value = false
  }
}

async function loadAuditLogs() {
  auditLoading.value = true
  try {
    const d = await fetchGovernanceAuditLogs({ limit: 100 })
    auditLogs.value = d.items ?? []
  } catch {
    ElMessage.error('治理审计日志加载失败')
  } finally {
    auditLoading.value = false
  }
}

async function submitFreeze() {
  if (formUserId.value == null) {
    ElMessage.warning('请填用户 id（数字）')
    return
  }
  if (!formReason.value.trim()) {
    ElMessage.warning('冻结理由必填')
    return
  }
  if (formDays.value != null && (!Number.isInteger(formDays.value) || formDays.value <= 0)) {
    ElMessage.warning('天数须为正整数，或留空表示不限期')
    return
  }
  try {
    await ElMessageBox.confirm(
      `冻结用户 #${formUserId.value} 的治理特权？冻结期间不能参与举报 / 复核等治理动作；不扣经验、不影响核心权益。${formDays.value ? `期限 ${formDays.value} 天。` : '不限期（手动解冻才恢复）。'}`,
      '新建治理冻结',
      { type: 'warning', confirmButtonText: '确认冻结', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  freezeBusy.value = true
  try {
    const d = await createGovernanceFreeze({
      user_id: formUserId.value,
      reason: formReason.value.trim(),
      days: formDays.value,
    })
    ElMessage.success(`已冻结（#${d.freeze_id}${d.expires_at ? `，到期 ${formatTime(d.expires_at)}` : '，不限期'}）`)
    formReason.value = ''
    formDays.value = null
    await Promise.all([loadFreezes(), loadAuditLogs()])
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '冻结失败')
  } finally {
    freezeBusy.value = false
  }
}

async function releaseRow(row: GovernanceFreezeRow) {
  try {
    await ElMessageBox.confirm(
      `解冻用户 ${userLabel(row.username, row.user_id)} 的治理特权？解冻后立即恢复治理参与权。`,
      '解冻治理特权',
      { type: 'warning', confirmButtonText: '确认解冻', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  releaseBusyId.value = row.user_id
  try {
    await releaseGovernanceFreeze(row.user_id)
    ElMessage.success('已解冻')
    await Promise.all([loadFreezes(), loadAuditLogs()])
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '解冻失败')
  } finally {
    releaseBusyId.value = null
  }
}

onMounted(() => {
  void loadFreezes()
  void loadAuditLogs()
})
</script>

<template>
  <div>
    <PageHeader
      title="治理冻结"
      description="冻结用户的治理特权（举报 / 复核参与权）：不扣经验、不影响核心权益；与治理停权（Step 2）是两套独立机制"
    />

    <SectionCard title="新建冻结" :icon="Snowflake">
      <el-form label-position="top" class="gf-form" @submit.prevent="submitFreeze">
        <el-form-item label="用户 id（数字）">
          <el-input-number v-model="formUserId" :min="1" placeholder="用户 id" />
        </el-form-item>
        <el-form-item label="冻结理由（必填，留痕）">
          <el-input v-model="formReason" placeholder="如 多次恶意举报，经终审确认" maxlength="200" />
        </el-form-item>
        <el-form-item label="天数（留空 = 不限期）">
          <el-input-number v-model="formDays" :min="1" placeholder="不限期" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="freezeBusy" @click="submitFreeze">冻结治理特权</el-button>
        </el-form-item>
      </el-form>
    </SectionCard>

    <SectionCard title="生效中的冻结" :icon="Snowflake">
      <template #actions>
        <el-button :loading="freezesLoading" @click="loadFreezes">
          <el-icon><RefreshCw /></el-icon>刷新
        </el-button>
      </template>
      <DataTable
        :columns="freezeColumns"
        :rows="freezes"
        :loading="freezesLoading"
        row-key="id"
        empty="当前没有生效中的冻结"
      >
        <template #cell-username="{ row }">
          {{ userLabel(row.username, row.user_id) }}
        </template>
        <template #cell-expires_at="{ row }">
          {{ row.expires_at ? formatTime(row.expires_at) : '不限期' }}
        </template>
        <template #cell-created_by_name="{ row }">
          {{ userLabel(row.created_by_name, row.created_by) }}
        </template>
        <template #cell-created_at="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
        <template #cell-actions="{ row }">
          <el-button
            type="warning"
            link
            :loading="releaseBusyId === row.user_id"
            @click="releaseRow(row)"
          >
            解冻
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard title="治理审计日志" :icon="Snowflake">
      <template #actions>
        <el-button :loading="auditLoading" @click="loadAuditLogs">
          <el-icon><RefreshCw /></el-icon>刷新
        </el-button>
      </template>
      <DataTable
        :columns="auditColumns"
        :rows="auditLogs"
        :loading="auditLoading"
        row-key="id"
        empty="暂无治理审计日志"
      >
        <template #cell-actor_name="{ row }">
          {{ userLabel(row.actor_name, row.actor_user_id) }}
        </template>
        <template #cell-target_name="{ row }">
          {{ userLabel(row.target_name, row.target_user_id) }}
        </template>
        <template #cell-target_ref="{ row }">
          {{ row.target_ref || '—' }}
        </template>
        <template #cell-created_at="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>

<style scoped>
.gf-form { max-width: 560px; }
</style>
