<script setup lang="ts">
/**
 * 缺集清单（缺集全套）：缺集核算工人台账的只读面
 * （GET /api/admin/missing-episodes*）。
 *
 * 工人周期拿 TMDB 集表与盘内实有集对表，缺集落台账；这里看
 * 哪部剧缺哪几季几集、缺了多久、多少人在求补，管理员对着清单
 * 补源。补齐后工人自动划掉并完成对应的求补，不用在这里操作。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ListChecks, RefreshCw, Search } from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard } from '@/components/ui'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import {
  fetchMissingEpisodes,
  fetchMissingEpisodesSummary,
  type MissingEpisodeRow,
  type MissingEpisodesSummary,
} from '@/api/missing-episodes'

const PAGE_SIZE = 50

const filters = reactive({
  q: '',
  libraryId: undefined as number | undefined,
  sort: 'missing_count' as 'missing_count' | 'first_detected_at',
})

const rows = ref<MissingEpisodeRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const summary = ref<MissingEpisodesSummary | null>(null)

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function detailText(row: MissingEpisodeRow): string {
  const parts: string[] = []
  for (const sn of Object.keys(row.missing_detail).sort(
    (a, b) => Number(a) - Number(b))) {
    parts.push(`第 ${sn} 季：缺第 ${row.missing_detail[sn].join('、')} 集`)
  }
  return parts.join('\n') || row.missing_text
}

async function loadSummary() {
  try {
    summary.value = await fetchMissingEpisodesSummary()
  } catch {
    summary.value = null
  }
}

async function load() {
  loading.value = true
  try {
    const res = await fetchMissingEpisodes({
      limit: PAGE_SIZE,
      offset: offset.value,
      sort: filters.sort,
      q: filters.q.trim() || undefined,
      library_id: filters.libraryId,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '缺集清单加载失败'))
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

function resetFilters() {
  filters.q = ''
  filters.libraryId = undefined
  filters.sort = 'missing_count'
  search()
}

function prevPage() {
  if (offset.value <= 0) return
  offset.value = Math.max(0, offset.value - PAGE_SIZE)
  void load()
}

function nextPage() {
  if (offset.value + PAGE_SIZE >= total.value) return
  offset.value += PAGE_SIZE
  void load()
}

onMounted(() => {
  void loadSummary()
  void load()
})
</script>

<template>
  <div class="missing-page">
    <PageHeader
      eyebrow="媒体"
      title="缺集清单"
      description="系统定期拿 TMDB 的集表跟盘里实际有的集对，缺哪几集都在这里。补上之后系统会自动划掉，并通知求补的人；没有 TMDB 号的剧对不了表，不在这张清单里。"
    />

    <div class="stat-grid">
      <SectionCard title="正在缺集的剧">
        <div class="stat-num">{{ summary?.open_series ?? '—' }}</div>
        <div class="dim">部剧有缺集</div>
      </SectionCard>
      <SectionCard title="总缺集数">
        <div class="stat-num">{{ summary?.total_missing ?? '—' }}</div>
        <div class="dim">集还没补上</div>
      </SectionCard>
      <SectionCard title="没法对表的剧">
        <div class="stat-num">{{ summary?.skipped_no_tmdb ?? '—' }}</div>
        <div class="dim">部剧没绑 TMDB 号，只能等绑上后再算</div>
      </SectionCard>
    </div>

    <SectionCard title="筛选">
      <div class="filter-grid">
        <el-input
          v-model="filters.q"
          placeholder="按剧名搜"
          clearable
          @keyup.enter="search"
        />
        <el-select
          v-model="filters.libraryId"
          placeholder="全部媒体库"
          clearable
        >
          <el-option
            v-for="lib in summary?.libraries ?? []"
            :key="lib.id"
            :label="`${lib.name}（缺 ${lib.open_series} 部）`"
            :value="lib.id"
          />
        </el-select>
        <el-select v-model="filters.sort" placeholder="排序">
          <el-option label="缺得多的在前" value="missing_count" />
          <el-option label="缺得最久的在前" value="first_detected_at" />
        </el-select>
        <div class="filter-actions">
          <el-button type="primary" :loading="loading" @click="search">
            <Search :size="14" /> 查询
          </el-button>
          <el-button @click="resetFilters">
            <RefreshCw :size="14" /> 重置
          </el-button>
        </div>
      </div>
    </SectionCard>

    <SectionCard :title="`清单（共 ${total} 部）`">
      <el-table
        v-if="rows.length"
        :data="rows"
        size="small"
        class="missing-table"
        v-loading="loading"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <pre class="detail-pre">{{ detailText(row) }}</pre>
          </template>
        </el-table-column>
        <el-table-column label="剧名" min-width="180">
          <template #default="{ row }">{{ row.series_name }}</template>
        </el-table-column>
        <el-table-column label="所属库" width="140">
          <template #default="{ row }">{{ row.library_name || '—' }}</template>
        </el-table-column>
        <el-table-column label="缺集" min-width="260">
          <template #default="{ row }">{{ row.missing_text }}</template>
        </el-table-column>
        <el-table-column label="缺集数" width="80">
          <template #default="{ row }">{{ row.missing_count }}</template>
        </el-table-column>
        <el-table-column label="首次发现" width="168">
          <template #default="{ row }">
            <span class="mono">{{ fmtTime(row.first_detected_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="求补人数" width="90">
          <template #default="{ row }">
            {{ row.requesters > 0 ? `${row.requesters} 人` : '—' }}
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else-if="!loading"
        compact
        :icon="ListChecks"
        title="没有正在缺集的剧"
        description="核算工人跑过之后，缺集的剧会自动出现在这里。"
      />
      <div class="pager">
        <el-button :disabled="offset <= 0 || loading" @click="prevPage">
          上一页
        </el-button>
        <span class="dim">
          第 {{ Math.floor(offset / PAGE_SIZE) + 1 }} 页 · 本页 {{ rows.length }} 条
        </span>
        <el-button
          :disabled="offset + PAGE_SIZE >= total || loading"
          @click="nextPage"
        >
          下一页
        </el-button>
      </div>
    </SectionCard>
  </div>
</template>

<style scoped>
.missing-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: var(--au-text);
  line-height: 1.2;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  align-items: center;
}

.filter-actions {
  display: flex;
  gap: 8px;
}

.missing-table {
  width: 100%;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.dim {
  color: var(--au-text-3);
}

.detail-pre {
  margin: 0;
  padding: 8px 12px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--au-text-2);
}

.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}
</style>
