<script setup lang="ts">
/**
 * 4K 探针结果（后台大改造二期）：探针页真机回传结果的只读查看面
 * （GET /api/admin/probe-4k/results*）。
 *
 * 探针页在真机上试播 4K 样本，把每次尝试的管线判定/出画/报错
 * 原文回传落库；这里按时间倒序看最新一轮，点开看 payload 原文。
 * 筛选只做两项：条目（GUID 包含）、结果（短码精确）。服务端不
 * 解析 payload，这里也不解析，原样展示。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { FilterConfig } from '@/components/FilterBar.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import {
  fetchProbe4kResult,
  fetchProbe4kResults,
  type Probe4kDetail,
  type Probe4kRow,
} from '@/api/probe-4k'

const PAGE_SIZE = 50

const filters = reactive({
  q: '',
  outcome: '',
})

const rows = ref<Probe4kRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const outcomeOptions = ref<string[]>([])

const { isPhone } = useBreakpoint()

const columns: DataColumn[] = [
  { key: 'created_at', label: '时间', width: 150 },
  { key: 'item_guid', label: '条目 GUID', minWidth: 170, mobile: 'title' },
  { key: 'attempt', label: '尝试', width: 120 },
  { key: 'outcome', label: '结果', width: 150 },
  { key: 'username', label: '提交人', width: 110 },
  { key: 'payload_chars', label: '记录大小', width: 90 },
  { key: 'actions', label: '明细', width: 110 },
]

const pageNum = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)

function onPageChange(page: number) {
  offset.value = (page - 1) * PAGE_SIZE
  void load()
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function load() {
  loading.value = true
  try {
    const res = await fetchProbe4kResults({
      limit: PAGE_SIZE,
      offset: offset.value,
      item_guid: filters.q.trim() || undefined,
      outcome: filters.outcome || undefined,
    })
    rows.value = res.items
    total.value = res.total
    if (res.outcome_options.length) outcomeOptions.value = res.outcome_options
  } catch (err) {
    ElMessage.error(toAdminError(err, '探针结果加载失败'))
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function search() {
  offset.value = 0
  void load()
}

const probeFilterConfigs = computed<FilterConfig[]>(() => [
  {
    key: 'outcome', label: '结果', type: 'select', value: filters.outcome, placeholder: '全部结果',
    options: outcomeOptions.value.map((value) => ({ value, label: value })),
  },
])

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'outcome') filters.outcome = String(value)
}

function resetFilters() {
  filters.q = ''
  filters.outcome = ''
  search()
}

// ---------- 详情抽屉 ----------
const detailVisible = ref(false)
const detail = ref<Probe4kDetail | null>(null)
const detailLoading = ref(false)

async function openDetail(row: Probe4kRow) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await fetchProbe4kResult(row.id)
  } catch (err) {
    ElMessage.error(toAdminError(err, '探针结果详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

function prettyPayload(payload: string): string {
  try {
    return JSON.stringify(JSON.parse(payload), null, 2)
  } catch {
    return payload
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="probe-page">
    <PageHeader
      eyebrow="系统"
      title="4K 探针结果"
      description="探针页在真机上试播 4K 样本后回传的诊断记录，按时间倒序，最新一轮在最上面。点明细看每次尝试的完整留痕（流清单、预检矩阵、报错原文）。"
    />

    <SectionCard title="筛选">
      <FilterBar
        :search="{ value: filters.q, placeholder: '按条目 GUID 搜', mode: 'submit' }"
        :filters="probeFilterConfigs"
        :loading="loading"
        @update:search="filters.q = $event"
        @search-submit="search"
        @query="search"
        @filter-change="onFilterChange"
      />
      <div class="filter-actions">
        <el-button @click="resetFilters">
          <RefreshCw :size="14" /> 重置
        </el-button>
      </div>
    </SectionCard>

    <SectionCard :title="`结果（共 ${total} 条）`">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        row-key="id"
        empty="还没有探针结果"
        empty-description="管理员在真机上打开探针页跑一轮，结果会自动回传到这里。"
        :pagination="{ total, page: pageNum, pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-created_at="{ row }">
          <span class="mono">{{ fmtTime(row.created_at) }}</span>
        </template>
        <template #cell-item_guid="{ row }">
          <span class="mono">{{ row.item_guid || '—' }}</span>
        </template>
        <template #cell-outcome="{ row }">
          <span class="mono">{{ row.outcome || '—' }}</span>
        </template>
        <template #cell-username="{ row }">
          {{ row.username || `用户 ${row.user_id}` }}
        </template>
        <template #cell-payload_chars="{ row }">
          {{ row.payload_chars }} 字
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">明细</el-button>
        </template>
      </DataTable>
    </SectionCard>

    <el-drawer
      v-model="detailVisible"
      :title="detail ? `探针结果 #${detail.id}` : '探针结果'"
      :size="isPhone ? '100%' : '640px'"
    >
      <div v-if="detail" class="detail">
        <div class="detail-row">
          <span class="detail-label">时间</span>
          <span class="mono">{{ fmtTime(detail.created_at) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">条目</span>
          <span class="mono">{{ detail.item_guid || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">尝试</span>
          <span class="mono">{{ detail.attempt || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">结果</span>
          <span class="mono">{{ detail.outcome || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">提交人</span>
          <span>{{ detail.username || `用户 ${detail.user_id}` }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">设备 UA</span>
          <span class="ua">{{ detail.user_agent || '—' }}</span>
        </div>
        <h4 class="detail-sub">完整留痕</h4>
        <pre class="detail-pre">{{ prettyPayload(detail.payload) }}</pre>
      </div>
      <p v-else-if="detailLoading" class="dim">加载中…</p>
    </el-drawer>
  </div>
</template>

<style scoped>
.probe-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--ax-fs-aux-sm);
}

.dim {
  color: var(--au-text-3);
}

.filter-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-row {
  display: flex;
  gap: 12px;
  font-size: var(--ax-fs-body);
  color: var(--au-text);
}

.detail-label {
  flex: none;
  width: 64px;
  color: var(--au-text-3);
  font-size: var(--ax-fs-aux);
}

.ua {
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-2);
  word-break: break-all;
}

.detail-sub {
  margin: 14px 0 4px;
  font-size: var(--ax-fs-lead);
  color: var(--au-text);
}

.detail-pre {
  margin: 0;
  padding: 8px 12px;
  font-size: var(--ax-fs-aux-sm);
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--au-text-2);
}
</style>
