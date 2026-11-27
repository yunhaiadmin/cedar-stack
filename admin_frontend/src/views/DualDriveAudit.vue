<script setup lang="ts">
/**
 * 双盘审计台账（goal-38 审计第一步）：个人盘 + 公共贡献池的全程
 * 留痕汇成一个查询面（GET /api/admin/audit/events，只读）。
 *
 * 五路记录归一展示：转存/池决策留痕、池成员状态流水、池激励
 * 发奖、Drive 接口报错、个人盘起播取用事件（门禁被拒 / 副本
 * 选路 / 换线失败）。可按用户、池成员、绑定、条目、来源、动作
 * 类别、结果组与时间范围筛。
 *
 * 口径如实：Drive 报错只有账号不可逆哈希，没有用户/绑定/条目
 * 维度——按这三者筛时报错行不会出现，页头说明里写明。
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
  fetchAuditEvents,
  type AuditEventRow,
} from '@/api/admin'

const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'decision', label: '决策留痕' },
  { value: 'drive_error', label: 'Drive 报错' },
  { value: 'play_access', label: '起播取用' },
]

const CATEGORY_OPTIONS = [
  { value: '', label: '全部动作' },
  { value: 'transfer_gate', label: '转存门禁（个人盘）' },
  { value: 'gate_reject', label: '起播门禁被拒' },
  { value: 'copy_resolve', label: '副本选路' },
  { value: 'switch_failed', label: '换线失败' },
]

const OUTCOME_OPTIONS = [
  { value: '', label: '全部结果' },
  { value: 'success', label: '放行 / 完成' },
  { value: 'rejected', label: '被拒 / 失败' },
  { value: 'in_progress', label: '处理中' },
  { value: 'changed', label: '状态变更' },
  { value: 'error', label: '报错' },
]

const OUTCOME_TEXT: Record<string, string> = {
  success: '放行 / 完成',
  rejected: '被拒 / 失败',
  in_progress: '处理中',
  changed: '状态变更',
  error: '报错',
}

const OUTCOME_CLASS: Record<string, string> = {
  success: 'outcome-ok',
  rejected: 'outcome-bad',
  in_progress: 'outcome-mid',
  changed: 'outcome-mut',
  error: 'outcome-bad',
}

const PAGE_SIZE = 50

const filters = reactive({
  source: '',
  category: '',
  outcome: '',
  userId: '',
  memberId: '',
  bindingId: '',
  itemId: '',
  start: '',
  end: '',
})

const rows = ref<AuditEventRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)

const { isPhone } = useBreakpoint()

const columns: DataColumn[] = [
  { key: 'occurred_at', label: '时间', width: 168 },
  { key: 'action_label', label: '动作', minWidth: 140, mobile: 'title' },
  { key: 'ids', label: '用户 / 成员 / 绑定 / 条目', minWidth: 190 },
  { key: 'outcome', label: '结果', width: 150 },
  { key: 'reason', label: '原因', minWidth: 180 },
  { key: 'actions', label: '详情', width: 90 },
]

const pageNum = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)

function onPageChange(page: number) {
  offset.value = (page - 1) * PAGE_SIZE
  void load()
}

// ---------- 事件详情（原展开行改抽屉，沿全站详情承载口径） ----------
const detailVisible = ref(false)
const detailRow = ref<AuditEventRow | null>(null)

function openDetail(row: AuditEventRow) {
  detailRow.value = row
  detailVisible.value = true
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function idOrUndef(text: string): number | undefined {
  const n = Number(text)
  return text.trim() !== '' && Number.isFinite(n) && n > 0 ? n : undefined
}

async function load() {
  loading.value = true
  try {
    const res = await fetchAuditEvents({
      source: filters.source || undefined,
      category: filters.category || undefined,
      outcome: filters.outcome || undefined,
      user_id: idOrUndef(filters.userId),
      member_id: idOrUndef(filters.memberId),
      binding_id: idOrUndef(filters.bindingId),
      item_id: idOrUndef(filters.itemId),
      start: filters.start || undefined,
      end: filters.end || undefined,
      limit: PAGE_SIZE,
      offset: offset.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '审计台账加载失败'))
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

/** 筛选一律 FilterBar（统一标准第 4 条）：本页是草稿型——下拉/输入只改草稿，
 * 点「查询」才发请求（不挂 chips，chips 只反映已提交条件）；「重置」仍留在
 * FilterBar 旁，口径与原来一致（清草稿 + 立即查）。 */
const auditFilterConfigs = computed<FilterConfig[]>(() => [
  { key: 'source', label: '来源', type: 'select', value: filters.source, placeholder: '全部来源', options: SOURCE_OPTIONS },
  { key: 'category', label: '动作', type: 'select', value: filters.category, placeholder: '全部动作', options: CATEGORY_OPTIONS },
  { key: 'outcome', label: '结果', type: 'select', value: filters.outcome, placeholder: '全部结果', options: OUTCOME_OPTIONS },
  { key: 'userId', label: '用户 ID', type: 'custom', value: filters.userId },
  { key: 'memberId', label: '池成员 ID', type: 'custom', value: filters.memberId },
  { key: 'bindingId', label: '绑定 ID', type: 'custom', value: filters.bindingId },
  { key: 'itemId', label: '条目 ID', type: 'custom', value: filters.itemId },
  { key: 'start', label: '开始时间', type: 'custom', value: filters.start },
  { key: 'end', label: '结束时间', type: 'custom', value: filters.end },
])

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'source') filters.source = String(value)
  else if (key === 'category') filters.category = String(value)
  else if (key === 'outcome') filters.outcome = String(value)
}

function resetFilters() {
  filters.source = ''
  filters.category = ''
  filters.outcome = ''
  filters.userId = ''
  filters.memberId = ''
  filters.bindingId = ''
  filters.itemId = ''
  filters.start = ''
  filters.end = ''
  search()
}

function detailText(row: AuditEventRow): string {
  if (!row.detail) return '—'
  try {
    return JSON.stringify(row.detail, null, 2)
  } catch {
    return String(row.detail)
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="audit-page">
    <PageHeader
      eyebrow="媒体"
      title="双盘审计"
      description="个人盘与公共贡献池的全程留痕：谁的盘、何时、做了什么、成没成、为什么被拒。Drive 报错只有账号哈希、没有用户/绑定/条目维度，按这三者筛选时报错行不会出现。"
    />

    <SectionCard title="筛选">
      <FilterBar
        :filters="auditFilterConfigs"
        :loading="loading"
        query-button
        @filter-change="onFilterChange"
        @query="search"
      >
        <template #filter-userId>
          <el-input v-model="filters.userId" placeholder="用户 ID" clearable class="f-id" />
        </template>
        <template #filter-memberId>
          <el-input v-model="filters.memberId" placeholder="池成员 ID" clearable class="f-id" />
        </template>
        <template #filter-bindingId>
          <el-input v-model="filters.bindingId" placeholder="绑定 ID" clearable class="f-id" />
        </template>
        <template #filter-itemId>
          <el-input v-model="filters.itemId" placeholder="条目 ID" clearable class="f-id" />
        </template>
        <template #filter-start>
          <el-date-picker
            v-model="filters.start"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="开始时间"
            class="f-date"
          />
        </template>
        <template #filter-end>
          <el-date-picker
            v-model="filters.end"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="结束时间（不含）"
            class="f-date"
          />
        </template>
      </FilterBar>
      <div class="filter-actions">
        <el-button @click="resetFilters">
          <RefreshCw :size="14" /> 重置
        </el-button>
      </div>
    </SectionCard>

    <SectionCard :title="`台账（共 ${total} 条）`">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        empty="没有符合条件的记录"
        empty-description="换个筛选条件试试；起播取用类记录从本功能上线后开始积累。"
        :pagination="{ total, page: pageNum, pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-occurred_at="{ row }">
          <span class="mono">{{ fmtTime(row.occurred_at) }}</span>
        </template>
        <template #cell-ids="{ row }">
          <span class="mono dim">
            用户 {{ row.user_id ?? '—' }} · 成员 {{ row.member_id ?? '—' }}
            · 绑定 {{ row.binding_id ?? '—' }} · 条目 {{ row.item_id ?? '—' }}
          </span>
        </template>
        <template #cell-outcome="{ row }">
          <span :class="OUTCOME_CLASS[row.outcome] || 'outcome-mut'">
            {{ OUTCOME_TEXT[row.outcome] || row.outcome }}
          </span>
          <span class="mono dim"> {{ row.result }}</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </DataTable>
    </SectionCard>

    <el-drawer
      v-model="detailVisible"
      title="事件详情"
      :size="isPhone ? '100%' : '560px'"
    >
      <pre v-if="detailRow" class="detail-pre">{{ detailText(detailRow) }}</pre>
    </el-drawer>
  </div>
</template>

<style scoped>
.audit-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.f-id { width: 140px; }
.f-date { width: 210px; }

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

.detail-pre {
  margin: 0;
  padding: 8px 12px;
  font-size: var(--ax-fs-aux-sm);
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--au-text-2);
}

.outcome-ok { color: var(--au-success); }
.outcome-bad { color: var(--au-danger); }
.outcome-mid { color: var(--au-warning); }
.outcome-mut { color: var(--au-text-2); }
</style>
