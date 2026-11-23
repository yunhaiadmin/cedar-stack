<script setup lang="ts">
/**
 * 可见性治理复核（活动治理 L7 管理端，O-02 补缺）。
 *
 * 复核队列：身份未定的条目在货架/搜索/直达面的露出存疑时进队，
 * 管理员在这里准入 / 驳回并留备注。
 *
 * 硬语义（后端 visibility_governance 模块头）：准入/驳回只追加决策行
 * 与事件、留处置痕迹，**不改变任何露出结果**——露不露永远由案 A
 * 判定函数现算。本页文案如实说明，不让管理员误以为点准入就上架。
 *
 * 后端：backend/api/admin_media_governance.py
 * （/api/admin/emby/visibility-governance/*，require_staff；
 * 成功写操作的审计由服务端中间件统一记录，与识别审查同口径）。
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Eye, RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import {
  approveVisibilityTask,
  fetchVisibilityEvents,
  fetchVisibilityTasks,
  rejectVisibilityTask,
  type VisibilityEventRow,
  type VisibilityTaskRow,
} from '@/api/governance'

const SURFACE_LABELS: Record<string, string> = {
  shelf: '货架',
  search: '搜索',
  direct: '直达',
}

const taskColumns: DataColumn[] = [
  { key: 'item_name', label: '条目', mobile: 'title' },
  { key: 'surface', label: '露出面', width: 90 },
  { key: 'reason', label: '入队原因', minWidth: 200 },
  { key: 'status', label: '状态', width: 100 },
  { key: 'created_at', label: '入队时间', width: 170 },
  { key: 'decision', label: '处置', width: 190 },
  { key: 'actions', label: '操作', width: 150 },
]

const eventColumns: DataColumn[] = [
  { key: 'id', label: '事件', width: 80, mobile: 'title' },
  { key: 'event_type', label: '类型', width: 160 },
  { key: 'item_id', label: '条目 id', width: 100 },
  { key: 'task_id', label: '任务 id', width: 100 },
  { key: 'actor', label: '操作人', width: 100 },
  { key: 'created_at', label: '时间', width: 170 },
]

const PAGE_SIZE = 20
const tasks = ref<VisibilityTaskRow[]>([])
const tasksTotal = ref(0)
const tasksPage = ref(1)
const tasksLoading = ref(false)
const statusFilter = ref('')
const busyTaskId = ref<number | null>(null)

const events = ref<VisibilityEventRow[]>([])
const eventsTotal = ref(0)
const eventsPage = ref(1)
const eventsLoading = ref(false)

function formatTime(iso: string | null) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}

function statusTag(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'danger'
  return 'warning'
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    const d = await fetchVisibilityTasks({
      status: statusFilter.value || undefined,
      page: tasksPage.value,
      page_size: PAGE_SIZE,
    })
    tasks.value = d.items ?? []
    tasksTotal.value = d.total ?? 0
  } catch {
    ElMessage.error('复核队列加载失败')
  } finally {
    tasksLoading.value = false
  }
}

async function loadEvents() {
  eventsLoading.value = true
  try {
    const d = await fetchVisibilityEvents({ page: eventsPage.value, page_size: PAGE_SIZE })
    events.value = d.items ?? []
    eventsTotal.value = d.total ?? 0
  } catch {
    ElMessage.error('事件流水加载失败')
  } finally {
    eventsLoading.value = false
  }
}

function onTasksPageChange(p: number) {
  tasksPage.value = p
  void loadTasks()
}

function onEventsPageChange(p: number) {
  eventsPage.value = p
  void loadEvents()
}

function onStatusChange() {
  tasksPage.value = 1
  void loadTasks()
}

async function review(row: VisibilityTaskRow, approve: boolean) {
  let note = ''
  try {
    const r = await ElMessageBox.prompt(
      `「${row.item_name}」（${SURFACE_LABELS[row.surface] ?? row.surface}面）——${approve ? '准入' : '驳回'}只记录复核结论与备注，不改变该条目的实际露出结果（露出由身份判定现算）。`,
      approve ? '准入复核' : '驳回复核',
      {
        confirmButtonText: approve ? '确认准入' : '确认驳回',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '处置备注（留痕，可空）',
        type: approve ? 'info' : 'warning',
      },
    )
    note = (r.value || '').trim()
  } catch {
    return
  }
  busyTaskId.value = row.id
  try {
    if (approve) await approveVisibilityTask(row.id, note)
    else await rejectVisibilityTask(row.id, note)
    ElMessage.success(approve ? '已准入（已留痕）' : '已驳回（已留痕）')
    await Promise.all([loadTasks(), loadEvents()])
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '处置失败（可能已被他人处置）')
    await loadTasks()
  } finally {
    busyTaskId.value = null
  }
}

onMounted(() => {
  void loadTasks()
  void loadEvents()
})
</script>

<template>
  <div>
    <PageHeader
      title="可见性治理复核"
      description="身份存疑条目的露出复核队列：准入 / 驳回只留处置痕迹，不直接改变露出结果（露出由案 A 身份判定现算）"
    />

    <SectionCard title="复核队列" :icon="Eye">
      <template #actions>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 130px" @change="onStatusChange">
          <el-option label="待复核" value="pending" />
          <el-option label="已准入" value="approved" />
          <el-option label="已驳回" value="rejected" />
        </el-select>
        <el-button :loading="tasksLoading" @click="loadTasks">
          <el-icon><RefreshCw /></el-icon>刷新
        </el-button>
      </template>
      <DataTable
        :columns="taskColumns"
        :rows="tasks"
        :loading="tasksLoading"
        :pagination="{ total: tasksTotal, page: tasksPage, pageSize: PAGE_SIZE }"
        row-key="id"
        empty="复核队列是空的"
        @page-change="onTasksPageChange"
      >
        <template #cell-surface="{ row }">
          {{ SURFACE_LABELS[row.surface] ?? row.surface }}
        </template>
        <template #cell-status="{ row }">
          <el-tag :type="statusTag(row.status)" size="small">
            {{ row.status === 'pending' ? '待复核' : row.status === 'approved' ? '已准入' : '已驳回' }}
          </el-tag>
        </template>
        <template #cell-created_at="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
        <template #cell-decision="{ row }">
          <span v-if="row.decided_at">
            {{ formatTime(row.decided_at) }}{{ row.decision_note ? ` · ${row.decision_note}` : '' }}
          </span>
          <span v-else>—</span>
        </template>
        <template #cell-actions="{ row }">
          <template v-if="row.status === 'pending'">
            <el-button type="success" link :loading="busyTaskId === row.id" @click="review(row, true)">准入</el-button>
            <el-button type="danger" link :loading="busyTaskId === row.id" @click="review(row, false)">驳回</el-button>
          </template>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard title="事件流水" :icon="Eye">
      <template #actions>
        <el-button :loading="eventsLoading" @click="loadEvents">
          <el-icon><RefreshCw /></el-icon>刷新
        </el-button>
      </template>
      <DataTable
        :columns="eventColumns"
        :rows="events"
        :loading="eventsLoading"
        :pagination="{ total: eventsTotal, page: eventsPage, pageSize: PAGE_SIZE }"
        row-key="id"
        empty="暂无治理事件"
        @page-change="onEventsPageChange"
      >
        <template #cell-created_at="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>
