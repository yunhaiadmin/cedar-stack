<script setup lang="ts">
/**
 * 缺集清单（缺集全套）：缺集核算工人台账的只读面
 * （GET /api/admin/missing-episodes*）。
 *
 * 工人周期拿 TMDB 集表与盘内实有集对表，缺集落台账；这里看
 * 哪部剧缺哪几季几集、缺了多久、多少人在求补，管理员对着清单
 * 补源。补齐后工人自动划掉并完成对应的求补，不用在这里操作。
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
  fetchMissingEpisodes,
  fetchMissingEpisodesSummary,
  fetchMpFillConfig,
  saveMpFillConfig,
  type MissingEpisodeRow,
  type MissingEpisodesSummary,
  type MpFillConfig,
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

const { isPhone } = useBreakpoint()

const columns: DataColumn[] = [
  { key: 'series_name', label: '剧名', minWidth: 180, mobile: 'title' },
  { key: 'library_name', label: '所属库', width: 140 },
  { key: 'missing_text', label: '缺集', minWidth: 260 },
  { key: 'missing_count', label: '缺集数', width: 80 },
  { key: 'first_detected_at', label: '首次发现', width: 168 },
  { key: 'requesters', label: '求补人数', width: 90 },
  { key: 'actions', label: '明细', width: 90 },
]

const pageNum = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)

function onPageChange(page: number) {
  offset.value = (page - 1) * PAGE_SIZE
  void load()
}

// ---------- 缺集明细（原展开行改抽屉，沿全站详情承载口径） ----------
const detailVisible = ref(false)
const detailRow = ref<MissingEpisodeRow | null>(null)

function openDetail(row: MissingEpisodeRow) {
  detailRow.value = row
  detailVisible.value = true
}

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

/** 筛选一律 FilterBar（统一标准第 4 条）：剧名搜索是提交型（回车/点查询才查），
 * 库与排序是草稿（改了不查、点查询才生效）；「重置」仍留在 FilterBar 旁。 */
const missingFilterConfigs = computed<FilterConfig[]>(() => [
  {
    key: 'library', label: '媒体库', type: 'select', value: filters.libraryId, placeholder: '全部媒体库',
    options: (summary.value?.libraries ?? []).map((lib) => ({
      value: lib.id,
      label: `${lib.name}（缺 ${lib.open_series} 部）`,
    })),
  },
  {
    key: 'sort', label: '排序', type: 'select', value: filters.sort, placeholder: '排序',
    options: [
      { value: 'missing_count', label: '缺得多的在前' },
      { value: 'first_detected_at', label: '缺得最久的在前' },
    ],
  },
])

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'library') filters.libraryId = value === '' ? undefined : Number(value)
  else if (key === 'sort') filters.sort = String(value) as typeof filters.sort
}

function resetFilters() {
  filters.q = ''
  filters.libraryId = undefined
  filters.sort = 'missing_count'
  search()
}

/** 自动补集（MP 手动通道）：配置卡状态。token 只收新值、从不回填。 */
const fillCfg = ref<MpFillConfig | null>(null)
const fillToken = ref('')
const fillSaving = ref(false)

async function loadFillConfig() {
  try {
    fillCfg.value = await fetchMpFillConfig()
  } catch {
    fillCfg.value = null
  }
}

async function saveFill() {
  if (!fillCfg.value) return
  fillSaving.value = true
  try {
    const payload: Parameters<typeof saveMpFillConfig>[0] = {
      mp_download_base: fillCfg.value.mp_download_base,
      mp_episode_fill_enabled: fillCfg.value.mp_episode_fill_enabled,
      mp_episode_fill_daily_cap: fillCfg.value.mp_episode_fill_daily_cap,
      mp_episode_fill_cooldown_hours:
        fillCfg.value.mp_episode_fill_cooldown_hours,
    }
    if (fillToken.value.trim()) {
      payload.mp_download_token = fillToken.value.trim()
    }
    fillCfg.value = await saveMpFillConfig(payload)
    fillToken.value = ''
    ElMessage.success('补集通道配置已保存')
  } catch (err) {
    ElMessage.error(toAdminError(err, '补集通道配置保存失败'))
  } finally {
    fillSaving.value = false
  }
}

onMounted(() => {
  void loadSummary()
  void load()
  void loadFillConfig()
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

    <SectionCard title="自动补集（MoviePilot 手动通道）">
      <p class="dim fill-note">
        开启后，系统按这张缺集清单自动去 MoviePilot 搜种、发起下载（不走订阅，
        不受云海库存影响）。下好的集入库后会自动从清单划掉。总开关默认关着。
      </p>
      <div v-if="fillCfg" class="fill-grid">
        <label class="fill-field">
          <span>自动补集</span>
          <el-switch v-model="fillCfg.mp_episode_fill_enabled" />
        </label>
        <label class="fill-field fill-wide">
          <span>MoviePilot 地址</span>
          <el-input
            v-model="fillCfg.mp_download_base"
            placeholder="http://167.17.76.115:4500"
          />
        </label>
        <label class="fill-field fill-wide">
          <span>API Token（密钥）</span>
          <el-input
            v-model="fillToken"
            type="password"
            show-password
            :placeholder="
              fillCfg.mp_download_token_set
                ? '已设置，留空不修改'
                : '未设置，请填入'
            "
          />
        </label>
        <label class="fill-field">
          <span>每日最多发起下载</span>
          <el-input-number
            v-model="fillCfg.mp_episode_fill_daily_cap"
            :min="0"
            :max="500"
            controls-position="right"
          />
        </label>
        <label class="fill-field">
          <span>同剧同季冷却（小时）</span>
          <el-input-number
            v-model="fillCfg.mp_episode_fill_cooldown_hours"
            :min="1"
            :max="720"
            controls-position="right"
          />
        </label>
        <div class="fill-actions">
          <el-button
            type="primary"
            :loading="fillSaving"
            @click="saveFill"
          >
            保存补集设置
          </el-button>
        </div>
      </div>
      <div v-else class="dim">补集通道配置加载失败，刷新页面再试。</div>
    </SectionCard>

    <SectionCard title="筛选">
      <FilterBar
        :search="{ value: filters.q, placeholder: '按剧名搜', mode: 'submit' }"
        :filters="missingFilterConfigs"
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

    <SectionCard :title="`清单（共 ${total} 部）`">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        row-key="series_item_id"
        empty="没有正在缺集的剧"
        empty-description="核算工人跑过之后，缺集的剧会自动出现在这里。"
        :pagination="{ total, page: pageNum, pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-first_detected_at="{ row }">
          <span class="mono">{{ fmtTime(row.first_detected_at) }}</span>
        </template>
        <template #cell-requesters="{ row }">
          {{ row.requesters > 0 ? `${row.requesters} 人` : '—' }}
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">明细</el-button>
        </template>
      </DataTable>
    </SectionCard>

    <el-drawer
      v-model="detailVisible"
      :title="detailRow ? `缺集明细 · ${detailRow.series_name}` : '缺集明细'"
      :size="isPhone ? '100%' : '520px'"
    >
      <pre v-if="detailRow" class="detail-pre">{{ detailText(detailRow) }}</pre>
    </el-drawer>
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

.filter-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
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

.fill-note {
  margin: 0 0 12px;
  font-size: 13px;
}

.fill-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  align-items: end;
}

.fill-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--au-text-2);
}

.fill-actions {
  display: flex;
  align-items: end;
}
</style>
