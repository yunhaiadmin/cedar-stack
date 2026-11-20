<script setup lang="ts">
/**
 * 热搜管理（二期缺口「搜索排序/热搜管理」）：片库热搜榜的管理员面
 * （/api/admin/search*，见 backend/api/admin_search.py）。
 *
 * 榜单数据是用户真实搜索的聚合计数（按词 × 日累计，不关联用户），
 * 用户端搜索页展示近 7 天生效榜。这里能做的干预只有三种，语义
 * 别混：置顶 = 排到榜首区（统计照记）；下榜 = 统计照记但永不上榜
 * （违规词 / 误拼词用）；删除 = 清掉该词的全部统计（之后再被搜
 * 会重新累计）。全部写操作记操作日志，保存即对用户端生效。
 *
 * 搜索结果本身的排序（标题精确 > 前缀 > 别名 > …）是代码里的
 * 相关度分层、没有配置开关，本页不提供排序切换。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowUp, RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import {
  deleteSearchTermStats,
  fetchSearchOverrides,
  fetchSearchRanking,
  fetchSearchTerms,
  removeSearchOverride,
  reorderSearchOverrides,
  setSearchOverride,
  type HotRankingItem,
  type SearchOverrideRow,
  type SearchTermRow,
} from '@/api/search-hot'

const days = ref(7)
const ranking = ref<HotRankingItem[]>([])
const terms = ref<SearchTermRow[]>([])
const termsTotal = ref(0)
const overrides = ref<SearchOverrideRow[]>([])
const loading = ref(false)
const busyKey = ref<string | null>(null)
const newTerm = ref('')

const pins = computed(() => overrides.value.filter((o) => o.mode === 'pin'))

const rankingRows = computed(() =>
  ranking.value.map((r, i) => ({ ...r, rank: i + 1 })))

const rankingColumns: DataColumn[] = [
  { key: 'rank', label: '名次', width: 70 },
  { key: 'term', label: '搜索词', minWidth: 200, mobile: 'title' },
  { key: 'count', label: '次数', width: 100 },
  { key: 'pinned', label: '标记', width: 100 },
]

const termColumns: DataColumn[] = [
  { key: 'term', label: '搜索词', minWidth: 200, mobile: 'title' },
  { key: 'count', label: '次数', width: 100 },
  { key: 'last_seen_at', label: '最近出现', width: 168 },
  { key: 'override', label: '状态', width: 110 },
  { key: 'actions', label: '操作', width: 300 },
]

const overrideColumns: DataColumn[] = [
  { key: 'term', label: '搜索词', minWidth: 200, mobile: 'title' },
  { key: 'mode', label: '干预', width: 100 },
  { key: 'updated_at', label: '最近变动', width: 168 },
  { key: 'actions', label: '操作', width: 220 },
]

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function load() {
  loading.value = true
  try {
    const [rank, termRes, ovRes] = await Promise.all([
      fetchSearchRanking(days.value),
      fetchSearchTerms(days.value),
      fetchSearchOverrides(),
    ])
    ranking.value = rank.items
    terms.value = termRes.items
    termsTotal.value = termRes.total
    overrides.value = ovRes.items
  } catch (err) {
    ElMessage.error(toAdminError(err, '热搜数据加载失败'))
  } finally {
    loading.value = false
  }
}

function onDaysChange() {
  void load()
}

async function runBusy(key: string, fn: () => Promise<void>) {
  busyKey.value = key
  try {
    await fn()
    await load()
  } catch (err) {
    ElMessage.error(toAdminError(err, '操作失败'))
  } finally {
    busyKey.value = null
  }
}

async function onSetMode(term: string, mode: 'pin' | 'block', key?: string) {
  if (mode === 'block') {
    try {
      await ElMessageBox.confirm(
        `把「${term}」下榜？统计会继续累计，但这个词不会再出现在用户端的热搜榜上，直到你取消下榜。`,
        '下榜这个词',
        { confirmButtonText: '确认下榜', cancelButtonText: '再想想', type: 'warning' },
      )
    } catch {
      return
    }
  }
  await runBusy(key ?? term, async () => {
    await setSearchOverride(term, mode)
    ElMessage.success(mode === 'pin' ? `已置顶「${term}」` : `已下榜「${term}」`)
  })
}

async function onRemoveOverride(row: { term: string; term_key: string }) {
  await runBusy(row.term_key, async () => {
    await removeSearchOverride(row.term)
    ElMessage.success(`已取消对「${row.term}」的干预`)
  })
}

async function onDeleteStats(row: SearchTermRow) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.term}」的全部搜索统计？这个词会立即从热搜榜消失；` +
      '之后再被用户搜索到，会重新开始累计（要让它永远不上榜请用「下榜」）。',
      '删除统计',
      { confirmButtonText: '确认删除', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  await runBusy(row.term_key, async () => {
    const res = await deleteSearchTermStats(row.term)
    ElMessage.success(
      `已删除「${row.term}」的统计（${res.deleted_rows} 天记录）` +
      (res.removed_pin ? '，置顶已一并取消' : ''))
  })
}

async function onMovePin(row: SearchOverrideRow, dir: -1 | 1) {
  const list = pins.value
  const idx = list.findIndex((o) => o.term_key === row.term_key)
  const swap = idx + dir
  if (idx < 0 || swap < 0 || swap >= list.length) return
  const keys = list.map((o) => o.term_key)
  ;[keys[idx], keys[swap]] = [keys[swap], keys[idx]]
  await runBusy(row.term_key, async () => {
    await reorderSearchOverrides(keys)
    ElMessage.success('置顶顺序已更新')
  })
}

async function onAddTerm(mode: 'pin' | 'block') {
  const term = newTerm.value.trim()
  if (!term) {
    ElMessage.warning('先填要干预的搜索词')
    return
  }
  await onSetMode(term, mode)
  newTerm.value = ''
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="search-hot-page">
    <PageHeader
      eyebrow="内容"
      title="热搜管理"
      description="用户在搜索页搜过的词按天聚合成本榜（不关联到具体用户），用户端展示近 7 天的榜。这里可以把词置顶到榜首、下榜屏蔽、或删掉某个词的统计，保存即对用户端生效，操作会记入操作日志。搜索结果本身的排序是相关度算法、没有开关，不在这里调。"
    />

    <SectionCard title="统计窗口">
      <div class="window-row">
        <el-radio-group v-model="days" @change="onDaysChange">
          <el-radio-button :value="7">近 7 天（用户端同口径）</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" @click="load">
          <RefreshCw :size="14" /> 刷新
        </el-button>
      </div>
    </SectionCard>

    <SectionCard :title="`用户端生效榜（近 ${days} 天）`">
      <DataTable
        :columns="rankingColumns"
        :rows="rankingRows"
        :loading="loading"
        row-key="term_key"
        empty="暂无热搜数据"
        empty-description="用户在搜索页搜索后，词会出现在这里。"
      >
        <template #cell-pinned="{ row }">
          <span v-if="row.pinned" class="badge-pin">已置顶</span>
          <span v-else class="dim">—</span>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard title="干预清单（置顶排序在这里调）">
      <DataTable
        :columns="overrideColumns"
        :rows="overrides"
        :loading="loading"
        row-key="term_key"
        empty="暂无干预"
        empty-description="在下方词表里置顶或下榜一个词，或手动输入一个词添加。"
      >
        <template #cell-mode="{ row }">
          <span :class="row.mode === 'pin' ? 'badge-pin' : 'badge-block'">
            {{ row.mode === 'pin' ? '置顶' : '下榜' }}
          </span>
        </template>
        <template #cell-updated_at="{ row }">
          <span class="mono">{{ fmtTime(row.updated_at) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <template v-if="row.mode === 'pin'">
            <el-button
              link
              :disabled="busyKey === row.term_key || pins[0]?.term_key === row.term_key"
              :loading="busyKey === row.term_key"
              @click="onMovePin(row, -1)"
            >
              <ArrowUp :size="13" /> 上移
            </el-button>
            <el-button
              link
              :disabled="busyKey === row.term_key || pins[pins.length - 1]?.term_key === row.term_key"
              :loading="busyKey === row.term_key"
              @click="onMovePin(row, 1)"
            >
              <ArrowDown :size="13" /> 下移
            </el-button>
          </template>
          <el-button
            link
            type="danger"
            :loading="busyKey === row.term_key"
            @click="onRemoveOverride(row)"
          >
            {{ row.mode === 'pin' ? '取消置顶' : '取消下榜' }}
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard :title="`搜索词管理（近 ${days} 天共 ${termsTotal} 个词）`">
      <div class="add-row">
        <el-input
          v-model="newTerm"
          placeholder="手动输入一个搜索词（可以是还没被搜过的）"
          clearable
          @keyup.enter="onAddTerm('pin')"
        />
        <el-button :loading="busyKey === newTerm.trim()" @click="onAddTerm('pin')">
          置顶这个词
        </el-button>
        <el-button :loading="busyKey === newTerm.trim()" @click="onAddTerm('block')">
          屏蔽这个词
        </el-button>
      </div>
      <DataTable
        :columns="termColumns"
        :rows="terms"
        :loading="loading"
        row-key="term_key"
        empty="窗口内暂无搜索记录"
        empty-description="用户在搜索页搜索后，词会出现在这里。"
      >
        <template #cell-last_seen_at="{ row }">
          <span class="mono">{{ fmtTime(row.last_seen_at) }}</span>
        </template>
        <template #cell-override="{ row }">
          <span v-if="row.override === 'pin'" class="badge-pin">已置顶</span>
          <span v-else-if="row.override === 'block'" class="badge-block">已下榜</span>
          <span v-else class="dim">—</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button
            v-if="row.override !== 'pin'"
            link
            type="primary"
            :loading="busyKey === row.term_key"
            @click="onSetMode(row.term, 'pin', row.term_key)"
          >
            置顶
          </el-button>
          <el-button
            v-else
            link
            :loading="busyKey === row.term_key"
            @click="onRemoveOverride(row)"
          >
            取消置顶
          </el-button>
          <el-button
            v-if="row.override !== 'block'"
            link
            type="warning"
            :loading="busyKey === row.term_key"
            @click="onSetMode(row.term, 'block', row.term_key)"
          >
            下榜
          </el-button>
          <el-button
            v-else
            link
            :loading="busyKey === row.term_key"
            @click="onRemoveOverride(row)"
          >
            取消下榜
          </el-button>
          <el-button
            link
            type="danger"
            :loading="busyKey === row.term_key"
            @click="onDeleteStats(row)"
          >
            删除统计
          </el-button>
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>

<style scoped>
.search-hot-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.window-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.add-row .el-input {
  max-width: 420px;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--ax-fs-aux-sm);
}

.dim {
  color: var(--au-text-3);
}

.badge-pin {
  color: var(--au-success, #2f9e44);
  font-size: var(--ax-fs-aux-sm);
}

.badge-block {
  color: var(--au-danger, #d6336c);
  font-size: var(--ax-fs-aux-sm);
}
</style>
