<script setup lang="ts">
/**
 * 媒体库图片与顺序（媒体库学习 L1 / L2 / L4 管理端）。
 *
 * 上：库封面策略——库封面按什么顺序尝试出图（借代表海报 / 自研渲染 /
 * 直传 / SVG 占位）。顺序即尝试顺序；管理端保存进 system_configs，
 * 文件配置 LIBRARY_COVER_STRATEGY_ORDER 已设置时优先于此处保存；
 * 借代表一步仅在借代表开关开启时生效（文件配置，只读）。
 * 库展示顺序已收进媒体库页各库卡片封面处的小控件（改动即保存，走
 * 同一后端设序接口），本页不再单列顺序分组。
 * 下：条目图人工覆盖——管理员给条目手工指定海报 / 背景图。
 * 优先级：覆盖 > 旁挂 > 刮削。覆盖只接受 http(s) URL；读侧是否生效
 * 由 ITEM_ARTWORK_OVERRIDE_ENABLED 开关决定（写侧不受开关影响，
 * 可以先配好再开开关）。
 *
 * 下二：多版本管理——按条目 guid 查同一作品的全部物理版本
 * （含被合并行），可逐个拆出（unmerge）或整组拆分；也可手动触发
 * 单集多版本合并（按库或全部库）。后端端点 /api/admin/media/versions*
 * 与 /media/episode-versions/merge，操作后端自带审计。
 *
 * 布局纪律：本页就是媒体库设置的收纳面，封面策略、条目图覆盖与
 * 多版本管理在此分组呈现，不另起导航入口与新页面。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowUp, Image as ImageIcon, Layers } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import {
  deleteArtworkOverride,
  fetchArtworkOverrides,
  fetchCoverStrategy,
  fetchItemVersions,
  fetchLibraries,
  mergeEpisodeVersions,
  setArtworkOverride,
  setCoverStrategy,
  unmergeAllVersions,
  unmergeVersion,
  type ArtworkOverrideRow,
  type MediaVersionRow,
} from '@/api/admin'
import type { EmbyLibrary } from '@/types'

const STRATEGY_META: Record<string, { label: string; desc: string }> = {
  borrow: { label: '借代表海报', desc: '借库内已绑定代表条目的海报' },
  render: { label: '自研渲染封面', desc: '库已设置的渲染封面' },
  uploaded: { label: '管理员直传', desc: '管理员直接上传的封面' },
  svg: { label: 'SVG 占位', desc: '无图时给占位图，不破图' },
}

interface StrategyRow {
  step: string
  included: boolean
}

const strategyColumns: DataColumn[] = [
  { key: 'included', label: '启用', width: 70 },
  { key: 'step', label: '步骤', mobile: 'title' },
  { key: 'actions', label: '调整', width: 130 },
]

const overrideColumns: DataColumn[] = [
  { key: 'item_name', label: '条目', mobile: 'title' },
  { key: 'item_type', label: '类型', width: 100 },
  { key: 'kind', label: '图类', width: 110 },
  { key: 'image_url', label: '图片 URL', minWidth: 260 },
  { key: 'actions', label: '操作', width: 100 },
]

const strategyRows = ref<StrategyRow[]>([])
const strategyLoading = ref(false)
const strategySaving = ref(false)
const strategyEnvOrder = ref<string[] | null>(null)
const strategyBorrowEnabled = ref(false)

const overrideRows = ref<ArtworkOverrideRow[]>([])
const overrideLoading = ref(false)
const overridePage = ref(1)
const OVERRIDE_PAGE_SIZE = 20
const overrideTotal = ref(0)
const formItemId = ref<number | null>(null)
const formKind = ref('Primary')
const formUrl = ref('')
const formSaving = ref(false)

async function loadStrategy() {
  strategyLoading.value = true
  try {
    const d = await fetchCoverStrategy()
    strategyEnvOrder.value = d.env_order
    strategyBorrowEnabled.value = d.borrow_enabled
    // 可编辑顺序：已保存的用保存的，否则用当前生效顺序；已勾选的在前
    const editOrder = d.stored_order ?? d.effective_order
    const included = editOrder.filter((s) => d.known_steps.includes(s))
    const excluded = d.known_steps.filter((s) => !included.includes(s))
    strategyRows.value = [
      ...included.map((step) => ({ step, included: true })),
      ...excluded.map((step) => ({ step, included: false })),
    ]
  } catch {
    ElMessage.error('库封面策略加载失败')
  } finally {
    strategyLoading.value = false
  }
}

function toggleStrategy(row: StrategyRow) {
  row.included = !row.included
  // 勾选的沉到已勾选区末尾，取消的沉到未勾选区，保持两区各自有序
  const included = strategyRows.value.filter((r) => r.included)
  const excluded = strategyRows.value.filter((r) => !r.included)
  strategyRows.value = [...included, ...excluded]
}

function moveStrategy(index: number, dir: -1 | 1) {
  const rows = strategyRows.value
  const target = index + dir
  if (target < 0 || target >= rows.length) return
  if (!rows[index].included || !rows[target].included) return
  ;[rows[index], rows[target]] = [rows[target], rows[index]]
}

async function saveStrategy() {
  const order = strategyRows.value.filter((r) => r.included).map((r) => r.step)
  if (order.length === 0) {
    ElMessage.warning('至少保留一步封面策略')
    return
  }
  strategySaving.value = true
  try {
    await setCoverStrategy(order)
    ElMessage.success('库封面策略已保存')
    await loadStrategy()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '库封面策略保存失败')
  } finally {
    strategySaving.value = false
  }
}

async function loadOverrides() {
  overrideLoading.value = true
  try {
    const d: unknown = await fetchArtworkOverrides({
      limit: OVERRIDE_PAGE_SIZE,
      offset: (overridePage.value - 1) * OVERRIDE_PAGE_SIZE,
    })
    // 容错解析：新后端回信封 {items,total}，旧后端只回 {items} 或数组
    if (Array.isArray(d)) {
      overrideRows.value = d as ArtworkOverrideRow[]
      overrideTotal.value = (d as ArtworkOverrideRow[]).length
    } else {
      const env = d as { items?: ArtworkOverrideRow[]; total?: number }
      overrideRows.value = env.items ?? []
      overrideTotal.value = typeof env.total === 'number' ? env.total : overrideRows.value.length
    }
  } catch {
    ElMessage.error('覆盖列表加载失败')
  } finally {
    overrideLoading.value = false
  }
}

function onOverridePageChange(p: number) {
  overridePage.value = p
  void loadOverrides()
}

async function submitOverride() {
  if (formItemId.value == null || !formUrl.value.trim()) {
    ElMessage.warning('请填条目 id 与图片 URL')
    return
  }
  formSaving.value = true
  try {
    await setArtworkOverride({
      item_id: formItemId.value,
      kind: formKind.value,
      image_url: formUrl.value.trim(),
    })
    ElMessage.success('覆盖已保存')
    formUrl.value = ''
    await loadOverrides()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '覆盖保存失败（只接受 http(s) URL）')
  } finally {
    formSaving.value = false
  }
}

async function removeOverride(row: ArtworkOverrideRow) {
  try {
    await ElMessageBox.confirm(
      `删除条目「${row.item_name || `#${row.item_id}`}」的${row.kind === 'Backdrop' ? '背景图' : '海报'}人工覆盖？删除后将恢复按旁挂 / 刮削出图。`,
      '删除条目图覆盖',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteArtworkOverride(row.item_id, row.kind)
    ElMessage.success('覆盖已取消')
    await loadOverrides()
  } catch {
    ElMessage.error('取消覆盖失败')
  }
}

// ==================== 多版本管理 ====================
const versionColumns: DataColumn[] = [
  { key: 'name', label: '版本', mobile: 'title' },
  { key: 'file_path', label: '文件路径', minWidth: 260 },
  { key: 'height', label: '分辨率', width: 100 },
  { key: 'size', label: '大小', width: 110 },
  { key: 'state', label: '状态', width: 110 },
  { key: 'actions', label: '操作', width: 110 },
]

const versionGuid = ref('')
const versionRows = ref<MediaVersionRow[]>([])
const versionLoading = ref(false)
const versionQueried = ref(false)
const versionBusyGuid = ref('')
const unmergeAllBusy = ref(false)

const libraries = ref<EmbyLibrary[]>([])
const mergeLibraryId = ref<number | null>(null)
const mergeBusy = ref(false)
const mergeResult = ref('')

const mergedCount = computed(() => versionRows.value.filter((v) => v.is_merged).length)

function formatSize(bytes: number) {
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

async function loadVersions() {
  const guid = versionGuid.value.trim()
  if (!guid) {
    ElMessage.warning('请填条目 guid')
    return
  }
  versionLoading.value = true
  try {
    const d = await fetchItemVersions(guid)
    versionRows.value = d.versions
    versionQueried.value = true
  } catch (e) {
    versionRows.value = []
    versionQueried.value = false
    ElMessage.error(e instanceof Error ? e.message : '版本列表加载失败')
  } finally {
    versionLoading.value = false
  }
}

async function unmergeOne(row: MediaVersionRow) {
  try {
    await ElMessageBox.confirm(
      `把版本「${row.name}」从合并中拆出、恢复为独立条目？拆出后它会单独出现在片库列表里。`,
      '拆出版本',
      { type: 'warning', confirmButtonText: '确认拆出', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  versionBusyGuid.value = row.guid
  try {
    const d = await unmergeVersion(row.guid)
    ElMessage.success(d.message || '已拆出')
    await loadVersions()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '拆出失败')
  } finally {
    versionBusyGuid.value = ''
  }
}

async function unmergeAll() {
  const guid = versionGuid.value.trim()
  if (!guid) return
  try {
    await ElMessageBox.confirm(
      `解除该作品全部 ${mergedCount.value} 个版本的合并？所有版本都会恢复为独立条目。`,
      '整组拆分',
      { type: 'warning', confirmButtonText: '确认拆分', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  unmergeAllBusy.value = true
  try {
    const d = await unmergeAllVersions(guid)
    ElMessage.success(d.message || '已整组拆分')
    await loadVersions()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '整组拆分失败')
  } finally {
    unmergeAllBusy.value = false
  }
}

async function loadLibraries() {
  try {
    const d = await fetchLibraries()
    libraries.value = d.libraries ?? []
  } catch {
    libraries.value = []
  }
}

async function runEpisodeMerge() {
  const lib = mergeLibraryId.value
  const libName = lib == null ? '全部启用库' : (libraries.value.find((l) => l.id === lib)?.name ?? `库 #${lib}`)
  try {
    await ElMessageBox.confirm(
      `对${libName}执行单集多版本合并？同一集的多个文件将合并为多版本（主记录展示，其余挂为版本）。`,
      '触发单集合并',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  mergeBusy.value = true
  try {
    const d = await mergeEpisodeVersions(lib)
    mergeResult.value =
      `处理分组 ${d.groups ?? 0}，新合并 ${d.merged ?? 0}，晋升主记录 ${d.promoted ?? 0}，` +
      `孤儿解绑 ${d.unmerged_orphans ?? 0}，孤儿改挂 ${d.repointed_orphans ?? 0}`
    ElMessage.success('单集合并已执行')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '单集合并执行失败')
  } finally {
    mergeBusy.value = false
  }
}

onMounted(() => {
  void loadStrategy()
  void loadOverrides()
  void loadLibraries()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader title="图片与顺序" description="库封面策略、条目图人工覆盖与多版本合并 / 拆分（媒体库学习 L1 / L4）；库展示顺序在媒体库页各库卡片封面处改" />

    <SectionCard title="库封面策略" :icon="ImageIcon">
      <template #actions>
        <el-button type="primary" :loading="strategySaving" @click="saveStrategy">保存策略</el-button>
      </template>
      <p class="ml-note">顺序即尝试顺序：有图的步骤先命中。借代表一步仅在借代表开关开启时生效，当前：{{ strategyBorrowEnabled ? '已开启' : '未开启（文件配置）' }}。文件配置已设置时优先于此处保存，当前：{{ strategyEnvOrder ? '已设置' : '未设置' }}。保存后下次出图即生效。</p>
      <DataTable
        :columns="strategyColumns"
        :rows="strategyRows"
        :loading="strategyLoading"
        row-key="step"
        empty="暂无封面策略步骤"
      >
        <template #cell-included="{ row }">
          <el-checkbox :model-value="row.included" @change="toggleStrategy(row)" />
        </template>
        <template #cell-step="{ row }">
          {{ STRATEGY_META[row.step]?.label ?? row.step }}
          <span class="ml-note-inline">（{{ STRATEGY_META[row.step]?.desc ?? '' }}）</span>
        </template>
        <template #cell-actions="{ index }">
          <el-button link :disabled="index === 0 || !strategyRows[index].included" @click="moveStrategy(index, -1)">
            <el-icon><ArrowUp /></el-icon>
          </el-button>
          <el-button link :disabled="index >= strategyRows.filter((r) => r.included).length - 1 || !strategyRows[index].included" @click="moveStrategy(index, 1)">
            <el-icon><ArrowDown /></el-icon>
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard title="条目图覆盖" :icon="ImageIcon">
      <p class="ml-note">覆盖只接受 http(s) URL；读侧生效受 ITEM_ARTWORK_OVERRIDE_ENABLED 开关控制，关时配好的覆盖不生效、也不影响现有出图。</p>
      <el-form label-position="top" class="ml-form" @submit.prevent="submitOverride">
        <el-form-item label="条目 id（数字，见条目详情）">
          <el-input-number v-model="formItemId" :min="1" placeholder="条目 id" />
        </el-form-item>
        <el-form-item label="图类">
          <el-select v-model="formKind">
            <el-option label="海报 Primary" value="Primary" />
            <el-option label="背景 Backdrop" value="Backdrop" />
          </el-select>
        </el-form-item>
        <el-form-item label="图片 URL（http(s)）">
          <el-input v-model="formUrl" placeholder="https://…" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="formSaving" @click="submitOverride">保存覆盖</el-button>
        </el-form-item>
      </el-form>
      <DataTable
        :columns="overrideColumns"
        :rows="overrideRows"
        :loading="overrideLoading"
        :pagination="{ total: overrideTotal, page: overridePage, pageSize: OVERRIDE_PAGE_SIZE }"
        @page-change="onOverridePageChange"
      >
        <template #cell-actions="{ row }">
          <el-button type="danger" link @click="removeOverride(row)">取消</el-button>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard title="多版本管理" :icon="Layers">
      <template #actions>
        <el-button
          type="warning"
          :disabled="mergedCount === 0"
          :loading="unmergeAllBusy"
          @click="unmergeAll"
        >
          整组拆分{{ mergedCount ? `（${mergedCount} 个已合并）` : '' }}
        </el-button>
      </template>
      <p class="ml-note">按条目 guid 查同一作品的全部物理版本：主记录正常展示，被合并的版本挂在主记录下。拆出后版本恢复为独立条目，会单独出现在片库列表里。</p>
      <el-form label-position="top" class="ml-form" @submit.prevent="loadVersions">
        <el-form-item label="条目 guid（条目详情页地址中的 id）">
          <el-input v-model="versionGuid" placeholder="如 37a142b6546280b8a4f04147e41336af" clearable @keyup.enter="loadVersions" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="versionLoading" @click="loadVersions">查询版本</el-button>
        </el-form-item>
      </el-form>
      <DataTable
        v-if="versionQueried"
        :columns="versionColumns"
        :rows="versionRows"
        :loading="versionLoading"
        row-key="guid"
        empty="该条目没有其他版本"
      >
        <template #cell-height="{ row }">
          {{ row.height ? `${row.height}p` : '—' }}
        </template>
        <template #cell-size="{ row }">
          {{ formatSize(row.size) }}
        </template>
        <template #cell-state="{ row }">
          <el-tag v-if="row.is_primary" type="success" size="small">主记录</el-tag>
          <el-tag v-else-if="row.is_merged" type="info" size="small">已合并</el-tag>
          <el-tag v-else type="warning" size="small">独立</el-tag>
        </template>
        <template #cell-actions="{ row }">
          <el-button
            v-if="row.is_merged"
            type="warning"
            link
            :loading="versionBusyGuid === row.guid"
            @click="unmergeOne(row)"
          >
            拆出
          </el-button>
        </template>
      </DataTable>

      <p class="ml-note" style="margin-top: 20px">手动触发单集多版本合并：同一集的多个文件合并为多版本。自动合并平时由入库链路完成，这里用于存量回填 / 主动整理。</p>
      <el-form label-position="top" class="ml-form" @submit.prevent="runEpisodeMerge">
        <el-form-item label="目标库">
          <el-select v-model="mergeLibraryId" placeholder="全部启用库" clearable>
            <el-option v-for="lib in libraries" :key="lib.id" :label="lib.name" :value="lib.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="mergeBusy" @click="runEpisodeMerge">执行单集合并</el-button>
        </el-form-item>
      </el-form>
      <p v-if="mergeResult" class="ml-note">上次执行结果：{{ mergeResult }}</p>
    </SectionCard>
  </div>
</template>

<style scoped>
.ml-note { opacity: 0.75; font-size: var(--ax-fs-aux); margin: 0 0 12px; }
.ml-note-inline { opacity: 0.75; font-size: var(--ax-fs-aux); }
.ml-form { max-width: 560px; }
</style>
