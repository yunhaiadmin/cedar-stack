<script setup lang="ts">
/**
 * 转存变体（管理端入口，O-02 补缺）。
 *
 * 转存变体是把条目文件转存出一份服主副本、播放优先走副本的机制。
 * 后端端点（backend/api/playback_admin.py）：
 * - GET  /playback/transfer-variants        副本登记列表
 * - GET  /playback/transfer-variants/jobs   转存任务列表（可按状态过滤）
 * - POST /playback/transfer-variants/{id}/transfer  手动触发转存（只建队）
 * - POST /playback/transfer-variants/{id}/unbind    条目级解绑（副本转 released）
 *
 * 注意现状：转存总闸关着时工人空转，手动建的队会挂起不执行——这是
 * 后端口径，本页只如实展示任务状态，不假装已转存。
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Copy, RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import {
  fetchTransferJobs,
  fetchTransferVariants,
  transferVariantItem,
  unbindTransferVariant,
  type TransferJobRow,
  type TransferVariantRow,
} from '@/api/transfer-variants'

const variantColumns: DataColumn[] = [
  { key: 'item_id', label: '条目 id', width: 100, mobile: 'title' },
  { key: 'status', label: '状态', width: 110 },
  { key: 'owner_scope', label: '归属', width: 130 },
  { key: 'size_bytes', label: '大小', width: 110 },
  { key: 'stale_count', label: '失效计数', width: 90 },
  { key: 'last_played_at', label: '最近播放', width: 170 },
  { key: 'created_at', label: '登记时间', width: 170 },
  { key: 'actions', label: '操作', width: 100 },
]

const jobColumns: DataColumn[] = [
  { key: 'id', label: '任务', width: 80, mobile: 'title' },
  { key: 'item_id', label: '条目 id', width: 100 },
  { key: 'trigger', label: '触发', width: 110 },
  { key: 'status', label: '状态', width: 110 },
  { key: 'attempts', label: '尝试', width: 70 },
  { key: 'error_detail', label: '错误', minWidth: 200 },
  { key: 'updated_at', label: '更新时间', width: 170 },
]

const variants = ref<TransferVariantRow[]>([])
const variantsLoading = ref(false)
const jobs = ref<TransferJobRow[]>([])
const jobsLoading = ref(false)
const jobStatus = ref('')
const unbindBusyId = ref<number | null>(null)

const formItemId = ref<number | null>(null)
const formForce = ref(false)
const transferBusy = ref(false)

function formatSize(bytes: number | null) {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

function formatTime(iso: string | null) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}

function variantStatusTag(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'ready') return 'success'
  if (status === 'released') return 'info'
  if (status === 'pending') return 'warning'
  return 'danger'
}

function jobStatusTag(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'done' || status === 'ready') return 'success'
  if (status === 'pending' || status === 'queued') return 'warning'
  if (status === 'running') return 'warning'
  if (status === 'failed' || status === 'error') return 'danger'
  return 'info'
}

async function loadVariants() {
  variantsLoading.value = true
  try {
    const d = await fetchTransferVariants(100)
    variants.value = d.items ?? []
  } catch {
    ElMessage.error('转存副本列表加载失败')
  } finally {
    variantsLoading.value = false
  }
}

async function loadJobs() {
  jobsLoading.value = true
  try {
    const d = await fetchTransferJobs({ status: jobStatus.value || undefined, limit: 100 })
    jobs.value = d.items ?? []
  } catch {
    ElMessage.error('转存任务列表加载失败')
  } finally {
    jobsLoading.value = false
  }
}

async function submitTransfer() {
  if (formItemId.value == null) {
    ElMessage.warning('请填条目 id（数字）')
    return
  }
  try {
    await ElMessageBox.confirm(
      `给条目 #${formItemId.value} 建一条转存任务？只建队不立即执行，工人按轮次处理（总闸关时任务会挂起）。${formForce.value ? '本次将越过 24 小时节流。' : ''}`,
      '手动转存',
      { type: 'warning', confirmButtonText: '确认建队', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  transferBusy.value = true
  try {
    const d = await transferVariantItem(formItemId.value, formForce.value)
    ElMessage.success(`已建队（任务 #${d.job_id}，状态 ${d.status}）`)
    await loadJobs()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '建队失败')
  } finally {
    transferBusy.value = false
  }
}

async function unbindRow(row: TransferVariantRow) {
  try {
    await ElMessageBox.confirm(
      `解除条目 #${row.item_id} 的转存绑定？该条目全部生效中的副本会转为已释放，下次播放回原件；副本文件保留不删。`,
      '解绑转存副本',
      { type: 'warning', confirmButtonText: '确认解绑', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  unbindBusyId.value = row.item_id
  try {
    const d = await unbindTransferVariant(row.item_id)
    ElMessage.success(`已释放 ${d.released} 条副本`)
    await loadVariants()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '解绑失败')
  } finally {
    unbindBusyId.value = null
  }
}

onMounted(() => {
  void loadVariants()
  void loadJobs()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader title="转存变体" description="转存副本登记与转存任务：播放优先走服主副本；可手动给条目建转存队、或解绑回原件" />

    <SectionCard title="手动转存" :icon="Copy">
      <p class="tv-note">按条目数字 id 建一条转存任务。已有健康副本、原件盘不可定位等情况会被后端拒绝并给出原因；24 小时内已尝试过的默认拦截，勾选强制可越过（留审计）。</p>
      <el-form label-position="top" class="tv-form" @submit.prevent="submitTransfer">
        <el-form-item label="条目 id（数字，见条目详情）">
          <el-input-number v-model="formItemId" :min="1" placeholder="条目 id" />
        </el-form-item>
        <el-form-item label="强制">
          <el-checkbox v-model="formForce">越过 24 小时节流</el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="transferBusy" @click="submitTransfer">建转存任务</el-button>
        </el-form-item>
      </el-form>
    </SectionCard>

    <SectionCard title="转存副本登记" :icon="Copy">
      <template #actions>
        <el-button :loading="variantsLoading" @click="loadVariants">
          <el-icon><RefreshCw /></el-icon>刷新
        </el-button>
      </template>
      <DataTable
        :columns="variantColumns"
        :rows="variants"
        :loading="variantsLoading"
        row-key="id"
        empty="暂无转存副本登记"
      >
        <template #cell-status="{ row }">
          <el-tag :type="variantStatusTag(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
        <template #cell-owner_scope="{ row }">
          {{ row.owner_scope || '—' }}{{ row.owner_ref ? ` / ${row.owner_ref}` : '' }}
        </template>
        <template #cell-size_bytes="{ row }">
          {{ formatSize(row.size_bytes) }}
        </template>
        <template #cell-last_played_at="{ row }">
          {{ formatTime(row.last_played_at) }}
        </template>
        <template #cell-created_at="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
        <template #cell-actions="{ row }">
          <el-button
            v-if="row.status === 'ready'"
            type="warning"
            link
            :loading="unbindBusyId === row.item_id"
            @click="unbindRow(row)"
          >
            解绑
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard title="转存任务" :icon="Copy">
      <template #actions>
        <el-select v-model="jobStatus" placeholder="全部状态" clearable style="width: 140px" @change="loadJobs">
          <el-option label="pending" value="pending" />
          <el-option label="running" value="running" />
          <el-option label="done" value="done" />
          <el-option label="failed" value="failed" />
        </el-select>
        <el-button :loading="jobsLoading" @click="loadJobs">
          <el-icon><RefreshCw /></el-icon>刷新
        </el-button>
      </template>
      <DataTable
        :columns="jobColumns"
        :rows="jobs"
        :loading="jobsLoading"
        row-key="id"
        empty="暂无转存任务"
      >
        <template #cell-status="{ row }">
          <el-tag :type="jobStatusTag(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
        <template #cell-error_detail="{ row }">
          {{ row.error_kind ? `${row.error_kind}：` : '' }}{{ row.error_detail || '—' }}
        </template>
        <template #cell-updated_at="{ row }">
          {{ formatTime(row.updated_at) }}
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>

<style scoped>
.tv-note { opacity: 0.75; font-size: var(--ax-fs-aux); margin: 0 0 12px; }
.tv-form { max-width: 560px; }
</style>
