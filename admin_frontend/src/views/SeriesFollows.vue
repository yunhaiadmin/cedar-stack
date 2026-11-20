<script setup lang="ts">
/**
 * 追剧订阅管理（二期缺口项 2）：全站追剧订阅的管理员面
 * （GET /api/admin/series-follows、POST .../{id}/cancel）。
 *
 * 用户在账号页订阅一部剧后，系统有新集到货时自动为其转存。
 * 这里看全站谁订了什么、状态如何；用户失联/投诉等场景下管理员
 * 可代取消——与用户自助取消同语义（只置已取消、不删行，日后
 * 用户可自行重新订阅），动作记操作日志。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { FilterConfig } from '@/components/FilterBar.vue'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import {
  cancelSeriesFollow,
  fetchSeriesFollows,
  type SeriesFollowRow,
} from '@/api/series-follows'

const PAGE_SIZE = 50

const filters = reactive({
  q: '',
  status: '' as '' | 'active' | 'cancelled',
})

const rows = ref<SeriesFollowRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const cancellingId = ref<number | null>(null)

const columns: DataColumn[] = [
  { key: 'series_name', label: '剧名', minWidth: 200, mobile: 'title' },
  { key: 'username', label: '订阅用户', width: 160 },
  { key: 'status', label: '状态', width: 100 },
  { key: 'created_at', label: '订阅时间', width: 168 },
  { key: 'updated_at', label: '最近变动', width: 168 },
  { key: 'actions', label: '操作', width: 110 },
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
    const res = await fetchSeriesFollows({
      limit: PAGE_SIZE,
      offset: offset.value,
      status: filters.status || undefined,
      q: filters.q.trim() || undefined,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '追剧订阅加载失败'))
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

const followFilterConfigs = computed<FilterConfig[]>(() => [
  {
    key: 'status', label: '状态', type: 'select', value: filters.status, placeholder: '全部状态',
    options: [
      { value: 'active', label: '订阅中' },
      { value: 'cancelled', label: '已取消' },
    ],
  },
])

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'status') {
    filters.status = String(value) as typeof filters.status
  }
}

function resetFilters() {
  filters.q = ''
  filters.status = ''
  search()
}

async function onCancel(row: SeriesFollowRow) {
  try {
    await ElMessageBox.confirm(
      `代用户「${row.username}」取消对《${row.series_name || row.series_item_id}》的追剧订阅？` +
      '取消后新集不再自动转存，订阅记录保留，用户日后可自行重新订阅。',
      '代取消订阅',
      { confirmButtonText: '确认取消订阅', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  cancellingId.value = row.id
  try {
    await cancelSeriesFollow(row.id)
    ElMessage.success('已代为取消订阅')
    void load()
  } catch (err) {
    ElMessage.error(toAdminError(err, '取消订阅失败'))
  } finally {
    cancellingId.value = null
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="follows-page">
    <PageHeader
      eyebrow="内容"
      title="追剧订阅"
      description="用户订阅一部剧后，新集到货会自动转存到他自己的盘。这里看全站的订阅情况；需要时可代用户取消（只置已取消、不删记录，用户日后可自行重新订阅），操作会记入操作日志。"
    />

    <SectionCard title="筛选">
      <FilterBar
        :search="{ value: filters.q, placeholder: '按用户名或剧名搜', mode: 'submit' }"
        :filters="followFilterConfigs"
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

    <SectionCard :title="`订阅记录（共 ${total} 条）`">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        row-key="id"
        empty="没有符合条件的追剧订阅"
        empty-description="用户在账号页订阅剧集后，记录会出现在这里。"
        :pagination="{ total, page: pageNum, pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-series_name="{ row }">
          {{ row.series_name || `条目 #${row.series_item_id}` }}
          <span v-if="row.series_year" class="dim">（{{ row.series_year }}）</span>
        </template>
        <template #cell-username="{ row }">
          {{ row.username || `用户 #${row.user_id}` }}
        </template>
        <template #cell-status="{ row }">
          <span :class="row.status === 'active' ? 'st-active' : 'st-cancelled'">
            {{ row.status === 'active' ? '订阅中' : '已取消' }}
          </span>
        </template>
        <template #cell-created_at="{ row }">
          <span class="mono">{{ fmtTime(row.created_at) }}</span>
        </template>
        <template #cell-updated_at="{ row }">
          <span class="mono">{{ fmtTime(row.updated_at) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button
            v-if="row.status === 'active'"
            link
            type="danger"
            :loading="cancellingId === row.id"
            @click="onCancel(row)"
          >
            代取消
          </el-button>
          <span v-else class="dim">—</span>
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>

<style scoped>
.follows-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.filter-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--ax-fs-aux-sm);
}

.dim {
  color: var(--au-text-3);
}

.st-active {
  color: var(--au-success, #2f9e44);
}

.st-cancelled {
  color: var(--au-text-3);
}
</style>
