<script setup lang="ts">
/**
 * 字幕管理（后台大改造二期全缺第 7 项）：已入库字幕轨的只读盘点面
 * （GET /api/admin/subtitles/items*）。
 *
 * 字幕两个来源都落在媒体流轨道表里：内嵌轨（探测写入）与外挂文件
 * （扫描写入，带文件路径）。本页按片名搜条目、看每条有几轨、点开
 * 看每轨的语言/编码/来源。本页是盘点口：不提供搜字幕、补字幕、
 * 删除等写操作；尚未被扫描入库的文件不在盘点范围内。
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
import { toAdminError } from '@/utils/errorMessage'
import {
  fetchSubtitleItem,
  fetchSubtitleItems,
  type SubtitleItemDetail,
  type SubtitleItemRow,
} from '@/api/subtitles'

const PAGE_SIZE = 50

const filters = reactive({
  q: '',
  subtitle_filter: '',
})

const rows = ref<SubtitleItemRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)

const { isPhone } = useBreakpoint()

const columns: DataColumn[] = [
  { key: 'name', label: '条目', minWidth: 220, mobile: 'title' },
  { key: 'item_type', label: '类型', width: 80 },
  { key: 'production_year', label: '年份', width: 80 },
  { key: 'subtitle_count', label: '字幕轨', width: 130 },
  { key: 'embedded_count', label: '内嵌', width: 80 },
  { key: 'external_count', label: '外挂', width: 80 },
  { key: 'actions', label: '明细', width: 110 },
]

const pageNum = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)

function onPageChange(page: number) {
  offset.value = (page - 1) * PAGE_SIZE
  void load()
}

async function load() {
  loading.value = true
  try {
    const res = await fetchSubtitleItems({
      limit: PAGE_SIZE,
      offset: offset.value,
      q: filters.q.trim() || undefined,
      subtitle_filter: filters.subtitle_filter || undefined,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '字幕盘点加载失败'))
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

const subtitleFilterConfigs = computed<FilterConfig[]>(() => [
  {
    key: 'subtitle_filter', label: '字幕', type: 'select', value: filters.subtitle_filter, placeholder: '全部条目',
    options: [
      { value: 'with', label: '有字幕' },
      { value: 'without', label: '无字幕' },
    ],
  },
])

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'subtitle_filter') filters.subtitle_filter = String(value)
}

function resetFilters() {
  filters.q = ''
  filters.subtitle_filter = ''
  search()
}

/** 集的副行：剧名 + 第 S 季第 E 集；电影只显示自身 */
function itemSub(row: SubtitleItemRow): string {
  if (row.item_type !== 'episode') return ''
  const parts: string[] = []
  if (row.series_name) parts.push(row.series_name)
  if (row.season_number != null && row.episode_number != null) {
    parts.push(`S${row.season_number} E${row.episode_number}`)
  }
  return parts.join(' · ')
}

// ---------- 轨明细抽屉 ----------
const detailVisible = ref(false)
const detail = ref<SubtitleItemDetail | null>(null)
const detailLoading = ref(false)

async function openDetail(row: SubtitleItemRow) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await fetchSubtitleItem(row.id)
  } catch (err) {
    ElMessage.error(toAdminError(err, '字幕轨明细加载失败'))
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="subs-page">
    <PageHeader
      eyebrow="媒体"
      title="字幕管理"
      description="只读盘点：看每个条目已入库的字幕轨（内嵌轨＋外挂文件），按语言、编码、来源逐轨核对。本页不提供搜字幕、补字幕等写操作；尚未被扫描入库的字幕文件不在盘点范围内。"
    />

    <SectionCard title="筛选">
      <FilterBar
        :search="{ value: filters.q, placeholder: '按片名或剧名搜', mode: 'submit' }"
        :filters="subtitleFilterConfigs"
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

    <SectionCard :title="`条目（共 ${total} 条）`">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        row-key="id"
        empty="没有符合条件的条目"
        empty-description="换个片名或筛选条件再试；无字幕筛选可以快速找出还没字幕的条目。"
        :pagination="{ total, page: pageNum, pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-name="{ row }">
          <div class="item-name">{{ row.name }}</div>
          <div v-if="itemSub(row)" class="item-sub">{{ itemSub(row) }}</div>
        </template>
        <template #cell-item_type="{ row }">
          {{ row.item_type === 'episode' ? '剧集' : '电影' }}
        </template>
        <template #cell-production_year="{ row }">
          {{ row.production_year ?? '—' }}
        </template>
        <template #cell-subtitle_count="{ row }">
          <span v-if="row.subtitle_count > 0">{{ row.subtitle_count }} 轨</span>
          <span v-else class="no-sub">无字幕</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">轨明细</el-button>
        </template>
      </DataTable>
    </SectionCard>

    <el-drawer
      v-model="detailVisible"
      :title="detail ? `字幕轨 · ${detail.name}` : '字幕轨'"
      :size="isPhone ? '100%' : '640px'"
    >
      <div v-if="detail" class="detail">
        <div class="detail-row">
          <span class="detail-label">条目</span>
          <span>{{ detail.name }}</span>
        </div>
        <div v-if="itemSub(detail)" class="detail-row">
          <span class="detail-label">所属</span>
          <span>{{ itemSub(detail) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">字幕轨</span>
          <span>共 {{ detail.subtitle_count }} 轨（内嵌 {{ detail.embedded_count }} · 外挂 {{ detail.external_count }}）</span>
        </div>
        <h4 class="detail-sub">逐轨明细</h4>
        <p v-if="!detail.tracks.length" class="dim">这个条目还没有已入库的字幕轨。</p>
        <div v-for="track in detail.tracks" :key="track.id" class="track">
          <div class="track-head">
            <span class="mono">#{{ track.stream_index }}</span>
            <span class="track-source">{{ track.source === 'external' ? '外挂文件' : '内嵌轨' }}</span>
            <span>{{ track.language || '—' }}</span>
            <span class="mono">{{ track.codec || '—' }}</span>
            <span v-if="track.is_default" class="track-flag">默认</span>
            <span v-if="track.is_forced" class="track-flag">强制</span>
          </div>
          <div v-if="track.title" class="track-title">{{ track.title }}</div>
          <div v-if="track.external_path" class="track-path mono">{{ track.external_path }}</div>
        </div>
      </div>
      <p v-else-if="detailLoading" class="dim">加载中…</p>
    </el-drawer>
  </div>
</template>

<style scoped>
.subs-page {
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

.no-sub {
  color: var(--au-danger, #e5484d);
  font-weight: 600;
}

.item-name {
  font-weight: 600;
}

.item-sub {
  font-size: var(--ax-fs-aux-sm);
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

.detail-sub {
  margin: 14px 0 4px;
  font-size: var(--ax-fs-lead);
  color: var(--au-text);
}

.track {
  padding: 8px 12px;
  border: 1px solid var(--au-border);
  border-radius: var(--au-r-md);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.track-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: var(--ax-fs-body);
  color: var(--au-text);
}

.track-source {
  font-weight: 600;
}

.track-flag {
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-2);
  border: 1px solid var(--au-border);
  border-radius: var(--au-r-sm);
  padding: 0 6px;
}

.track-title {
  font-size: var(--ax-fs-aux);
  color: var(--au-text-2);
  word-break: break-all;
}

.track-path {
  color: var(--au-text-3);
  word-break: break-all;
}
</style>
