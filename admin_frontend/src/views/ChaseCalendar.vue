<script setup lang="ts">
/**
 * 追新日历（后台大改造二期缺口项）：与用户端追新日历同一口径的
 * 管理视图（GET /api/admin/chase-calendar、.../items/{id}）。
 *
 * 用户端日历按「入库日」分组（emby_items.date_added、Asia/Shanghai
 * 自然日），不是播出日历——单集播出日（air_date）在本仓没有落库
 * 数据源（核实结论见 backend/api/admin_chase_calendar.py 头注）。
 * 这里同口径呈现：最近什么进了库、哪天进了多少、哪些剧有人在追
 * （追剧人数来自 emby_series_follows 的 active 订阅）。全只读：
 * 系统里没有可运营编辑的排期字段，不硬造。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CalendarDays, RefreshCw } from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard, StatTile } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { FilterConfig } from '@/components/FilterBar.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import {
  fetchChaseCalendar,
  fetchChaseCalendarItem,
  type ChaseCalendarDay,
  type ChaseCalendarDetail,
  type ChaseCalendarItemRow,
} from '@/api/chase-calendar'

const { isPhone } = useBreakpoint()

function fmtLocalDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const today = new Date()
const filters = reactive({
  start: fmtLocalDate(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 29)),
  end: fmtLocalDate(today),
  itemType: '' as '' | 'movie' | 'series' | 'episode',
})

const days = ref<ChaseCalendarDay[]>([])
const types = ref<Record<string, number>>({})
const total = ref(0)
const loading = ref(false)

const columns: DataColumn[] = [
  { key: 'name', label: '条目', minWidth: 210, mobile: 'title' },
  { key: 'item_type', label: '类型', width: 76 },
  { key: 'library_name', label: '媒体库', width: 130 },
  { key: 'followers', label: '追剧人数', width: 92 },
  { key: 'date_added', label: '入库时间', width: 196 },
  { key: 'actions', label: '操作', width: 80 },
]

const TYPE_LABELS: Record<string, string> = {
  movie: '电影',
  series: '剧集',
  episode: '单集',
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function dayTitle(day: ChaseCalendarDay): string {
  const [y, m, d] = day.date.split('-').map(Number)
  const wd = WEEKDAYS[new Date(y, m - 1, d).getDay()]
  return `${day.date}（${wd}）· 共 ${day.count} 条`
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function episodeCode(row: {
  item_type: string
  season_number: number | null
  episode_number: number | null
}): string {
  if (row.item_type !== 'episode') return ''
  const s = row.season_number != null ? `S${String(row.season_number).padStart(2, '0')}` : ''
  const e = row.episode_number != null ? `E${String(row.episode_number).padStart(2, '0')}` : ''
  return `${s}${e}`
}

async function load() {
  loading.value = true
  try {
    const res = await fetchChaseCalendar({
      start: filters.start || undefined,
      end: filters.end || undefined,
      item_type: filters.itemType || undefined,
    })
    days.value = res.days
    types.value = res.types || {}
    total.value = res.total
    // 服务端可能把区间截到上限/补齐单边，回显以服务端为准
    filters.start = res.start
    filters.end = res.end
  } catch (err) {
    ElMessage.error(toAdminError(err, '追新日历加载失败'))
    days.value = []
    types.value = {}
    total.value = 0
  } finally {
    loading.value = false
  }
}

const typeFilterConfigs = computed<FilterConfig[]>(() => [
  {
    key: 'itemType', label: '类型', type: 'select', value: filters.itemType, placeholder: '全部类型',
    options: [
      { value: 'movie', label: '电影' },
      { value: 'series', label: '剧集' },
      { value: 'episode', label: '单集' },
    ],
  },
])

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'itemType') filters.itemType = String(value) as typeof filters.itemType
}

function resetFilters() {
  const t = new Date()
  filters.start = fmtLocalDate(new Date(t.getFullYear(), t.getMonth(), t.getDate() - 29))
  filters.end = fmtLocalDate(t)
  filters.itemType = ''
  void load()
}

// ---------- 详情抽屉 ----------
const detailVisible = ref(false)
const detail = ref<ChaseCalendarDetail | null>(null)
const detailLoading = ref(false)

async function openDetail(row: ChaseCalendarItemRow) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await fetchChaseCalendarItem(row.item_id)
  } catch (err) {
    ElMessage.error(toAdminError(err, '条目详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

function fmtSize(bytes: number): string {
  if (!bytes) return '—'
  const gb = bytes / 1024 / 1024 / 1024
  return gb >= 1 ? `${gb.toFixed(2)} GB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="chase-page">
    <PageHeader
      eyebrow="内容"
      title="追新日历"
      description="与用户端追新日历同一口径：按入库日（Asia/Shanghai 自然日）看最近什么进了库。这里能看全库（含用户端看不到的未绑定条目），并带每部剧的追剧人数；点条目看来源与订阅详情。单集播出日没有落库数据源，本页不做播出排期，全页只读。"
    />

    <div class="stat-row">
      <StatTile label="区间新增" :value="total" :icon="CalendarDays" />
      <StatTile label="电影" :value="types.movie || 0" />
      <StatTile label="剧集" :value="types.series || 0" />
      <StatTile label="单集" :value="types.episode || 0" />
    </div>

    <SectionCard title="筛选">
      <div class="range-row">
        <el-date-picker
          v-model="filters.start"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="开始日期"
          :clearable="false"
          class="range-date"
        />
        <span class="range-sep">至</span>
        <el-date-picker
          v-model="filters.end"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="结束日期"
          :clearable="false"
          class="range-date"
        />
      </div>
      <FilterBar
        :filters="typeFilterConfigs"
        :loading="loading"
        @query="load"
        @filter-change="onFilterChange"
      />
      <div class="filter-actions">
        <el-button @click="resetFilters">
          <RefreshCw :size="14" /> 重置（近 30 天）
        </el-button>
      </div>
    </SectionCard>

    <EmptyState
      v-if="!loading && days.length === 0"
      title="这段时间没有新增入库"
      description="换个日期区间看看；新文件扫描入库后会按入库日出现在这里。"
    />

    <SectionCard v-for="day in days" :key="day.date" :title="dayTitle(day)">
      <DataTable
        :columns="columns"
        :rows="day.items"
        :loading="loading"
        row-key="item_id"
        empty="当天没有条目"
      >
        <template #cell-name="{ row }">
          <template v-if="row.item_type === 'episode'">
            {{ row.series_name || row.name }}
            <span class="mono dim">{{ episodeCode(row) }}</span>
            <span v-if="row.name && row.name !== row.series_name" class="dim"> · {{ row.name }}</span>
          </template>
          <template v-else>
            {{ row.name }}
            <span v-if="row.production_year" class="dim">（{{ row.production_year }}）</span>
          </template>
        </template>
        <template #cell-item_type="{ row }">
          {{ TYPE_LABELS[row.item_type] || row.item_type }}
        </template>
        <template #cell-library_name="{ row }">
          {{ row.library_name || `#${row.library_id}` }}
        </template>
        <template #cell-followers="{ row }">
          <span v-if="row.followers != null">{{ row.followers }} 人</span>
          <span v-else class="dim">—</span>
        </template>
        <template #cell-date_added="{ row }">
          <span class="mono">{{ fmtTime(row.date_added) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </DataTable>
      <p v-if="day.truncated" class="dim truncate-note">
        当天共 {{ day.count }} 条，此处只展开最新 {{ day.items.length }} 条。
      </p>
    </SectionCard>

    <el-drawer
      v-model="detailVisible"
      :title="detail ? detail.name : '条目详情'"
      :size="isPhone ? '100%' : '560px'"
    >
      <div v-if="detail" class="detail">
        <div class="detail-row">
          <span class="detail-label">类型</span>
          <span>{{ TYPE_LABELS[detail.item_type] || detail.item_type }}</span>
        </div>
        <div v-if="detail.series" class="detail-row">
          <span class="detail-label">所属剧</span>
          <span>{{ detail.series.series_name }}</span>
        </div>
        <div
          v-if="detail.item_type === 'episode' && episodeCode(detail)"
          class="detail-row"
        >
          <span class="detail-label">季 / 集</span>
          <span class="mono">{{ episodeCode(detail) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">媒体库</span>
          <span>{{ detail.library_name || `#${detail.library_id}` }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">入库时间</span>
          <span class="mono">{{ fmtTime(detail.date_added) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">首播 / 上映</span>
          <span>{{ detail.premiere_date || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">来源文件</span>
          <span class="path">{{ detail.file_path || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">来源形态</span>
          <span>
            {{ detail.media_source || '—' }}
            <span v-if="detail.container" class="dim">· {{ detail.container }}</span>
            <span class="dim">· {{ fmtSize(detail.size) }}</span>
          </span>
        </div>
        <div class="detail-row">
          <span class="detail-label">TMDB / IMDb</span>
          <span class="mono">{{ detail.tmdb_id || '—' }} / {{ detail.imdb_id || '—' }}</span>
        </div>

        <template v-if="detail.follow">
          <h4 class="detail-sub">追剧订阅（{{ detail.follow.series_name }}）</h4>
          <div class="detail-row">
            <span class="detail-label">订阅中</span>
            <span>{{ detail.follow.followers_active }} 人（累计 {{ detail.follow.followers_total }} 人）</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">库内集数</span>
            <span>{{ detail.follow.episode_count }} 集</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">最近入库单集</span>
            <span class="mono">{{ fmtTime(detail.follow.latest_episode_added) }}</span>
          </div>
          <div v-if="detail.follow.recent_followers.length" class="detail-row">
            <span class="detail-label">最近订阅</span>
            <span>
              <span
                v-for="f in detail.follow.recent_followers"
                :key="f.user_id"
                class="follower"
              >
                {{ f.username || `用户 ${f.user_id}` }}（{{ f.status === 'active' ? '订阅中' : '已取消' }}）
              </span>
            </span>
          </div>
        </template>

        <h4 class="detail-sub">MP 订阅</h4>
        <p class="dim mp-note">{{ detail.mp_note }}。</p>
      </div>
      <p v-else-if="detailLoading" class="dim">加载中…</p>
    </el-drawer>
  </div>
</template>

<style scoped>
.chase-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

@media (max-width: 720px) {
  .stat-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

.range-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.range-date {
  width: 180px;
}

.range-sep {
  color: var(--au-text-3);
  font-size: var(--uhd-fs-aux);
}

.filter-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--uhd-fs-aux-sm);
}

.dim {
  color: var(--au-text-3);
}

.truncate-note {
  margin: 10px 0 0;
  font-size: var(--uhd-fs-aux);
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-row {
  display: flex;
  gap: 12px;
  font-size: var(--uhd-fs-aux);
}

.detail-label {
  flex: 0 0 88px;
  color: var(--au-text-3);
}

.detail-sub {
  margin: 14px 0 2px;
  font-size: var(--uhd-fs-caption);
  color: var(--au-text);
}

.path {
  word-break: break-all;
}

.follower {
  display: inline-block;
  margin-right: 10px;
}

.mp-note {
  margin: 0;
  font-size: var(--uhd-fs-aux);
}
</style>
