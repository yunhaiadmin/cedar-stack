<script setup lang="ts">
/**
 * 人物条目管理（二期部分缺）：credits 聚合人物的管理员面
 * （GET /api/admin/people、POST /rename、GET /merge-preview、POST /merge）。
 *
 * 人物没有实体表：emby_people 是按条目落的演职员行，人物按规范键聚合
 * （有 TMDB id 用 tmdb:<id>，否则用归一化姓名键）。刮削偶尔会把同一个人
 * 落成两份（一份有 TMDB id、一份只有名字，或名字写法不同），用户端就
 * 会出现两个重复人物。这里改名（只改显示名）与合并（把源人物的全部
 * 条目关联改指目标、源人物消灭），两个动作都记操作日志；合并是破坏性
 * 操作，执行前必须在弹窗里看清将迁移的关联数并二次勾选确认。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Merge, Pencil, RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import { toAdminError } from '@/utils/errorMessage'
import {
  fetchMergePreview,
  fetchPeople,
  mergePerson,
  renamePerson,
  type MergePreview,
  type PersonRow,
} from '@/api/people'

const PAGE_SIZE = 50

const q = ref('')
const rows = ref<PersonRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)

const columns: DataColumn[] = [
  { key: 'person', label: '人物', minWidth: 220, mobile: 'title' },
  { key: 'tmdb_id', label: 'TMDB ID', width: 120 },
  { key: 'credits_count', label: '关联数', width: 90 },
  { key: 'works_count', label: '作品数', width: 90 },
  { key: 'actions', label: '操作', width: 170 },
]

const pageNum = () => Math.floor(offset.value / PAGE_SIZE) + 1

function onPageChange(page: number) {
  offset.value = (page - 1) * PAGE_SIZE
  void load()
}

async function load() {
  loading.value = true
  try {
    const res = await fetchPeople({
      q: q.value.trim() || undefined,
      limit: PAGE_SIZE,
      offset: offset.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '人物列表加载失败'))
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

function resetSearch() {
  q.value = ''
  search()
}

const brokenImages = ref<Set<string>>(new Set())
function onImageError(key: string) {
  brokenImages.value = new Set(brokenImages.value).add(key)
}

/* ==================== 改名 ==================== */

const renameDialog = reactive({
  visible: false,
  row: null as PersonRow | null,
  newName: '',
  saving: false,
})

function openRename(row: PersonRow) {
  renameDialog.row = row
  renameDialog.newName = row.name
  renameDialog.visible = true
}

async function submitRename() {
  const row = renameDialog.row
  const newName = renameDialog.newName.trim()
  if (!row || !newName) return
  renameDialog.saving = true
  try {
    const res = await renamePerson(row.key, newName)
    ElMessage.success(res.renamed
      ? `已改名：${row.name} → ${newName}（${res.credits_count ?? row.credits_count} 条关联）`
      : '名字没有变化')
    renameDialog.visible = false
    void load()
  } catch (err) {
    ElMessage.error(toAdminError(err, '改名失败'))
  } finally {
    renameDialog.saving = false
  }
}

/* ==================== 合并 ==================== */

const mergeDialog = reactive({
  visible: false,
  source: null as PersonRow | null,
  targetQ: '',
  candidates: [] as PersonRow[],
  searching: false,
  target: null as PersonRow | null,
  preview: null as MergePreview | null,
  previewLoading: false,
  confirmed: false,
  merging: false,
})

function openMerge(row: PersonRow) {
  mergeDialog.source = row
  mergeDialog.targetQ = ''
  mergeDialog.candidates = []
  mergeDialog.target = null
  mergeDialog.preview = null
  mergeDialog.confirmed = false
  mergeDialog.visible = true
}

async function searchTargets() {
  const source = mergeDialog.source
  if (!source) return
  mergeDialog.searching = true
  try {
    const res = await fetchPeople({
      q: mergeDialog.targetQ.trim() || undefined,
      limit: 20,
      offset: 0,
    })
    mergeDialog.candidates = res.items.filter((r) => r.key !== source.key)
  } catch (err) {
    ElMessage.error(toAdminError(err, '目标人物搜索失败'))
    mergeDialog.candidates = []
  } finally {
    mergeDialog.searching = false
  }
}

async function selectTarget(row: PersonRow) {
  const source = mergeDialog.source
  if (!source) return
  mergeDialog.target = row
  mergeDialog.preview = null
  mergeDialog.confirmed = false
  mergeDialog.previewLoading = true
  try {
    mergeDialog.preview = await fetchMergePreview(source.key, row.key)
  } catch (err) {
    ElMessage.error(toAdminError(err, '合并预览加载失败'))
    mergeDialog.target = null
  } finally {
    mergeDialog.previewLoading = false
  }
}

async function submitMerge() {
  const source = mergeDialog.source
  const target = mergeDialog.target
  if (!source || !target || !mergeDialog.preview || !mergeDialog.confirmed) return
  mergeDialog.merging = true
  try {
    const res = await mergePerson(source.key, target.key)
    ElMessage.success(
      `已合并：${source.name} → ${res.name}（迁移 ${res.migrated} 条、去重 ${res.duplicates_dropped} 条，合并后共 ${res.credits_count} 条关联 / ${res.works_count} 部作品）`)
    mergeDialog.visible = false
    void load()
  } catch (err) {
    ElMessage.error(toAdminError(err, '合并失败'))
  } finally {
    mergeDialog.merging = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="people-page">
    <PageHeader
      eyebrow="媒体"
      title="人物管理"
      description="演职人员按条目落库、按人物聚合（有 TMDB id 用 TMDB 键，否则用姓名键）。刮削把同一个人落成两份时，用户端会出现重复人物：改名只改显示名；合并把源人物的全部条目关联改指目标、源人物随之消失。两个动作都记操作日志。"
    />

    <SectionCard title="筛选">
      <FilterBar
        :search="{ value: q, placeholder: '按姓名或 TMDB ID 搜', mode: 'submit' }"
        :loading="loading"
        @update:search="q = $event"
        @search-submit="search"
        @query="search"
      />
      <div class="filter-actions">
        <el-button @click="resetSearch">
          <RefreshCw :size="14" /> 重置
        </el-button>
      </div>
    </SectionCard>

    <SectionCard :title="`人物（共 ${total} 人）`">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        row-key="key"
        empty="没有符合条件的人物"
        empty-description="条目刮削到演职员后，人物会出现在这里。"
        :pagination="{ total, page: pageNum(), pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-person="{ row }">
          <span class="person-cell">
            <span class="avatar">
              <img
                v-if="row.image && !brokenImages.has(row.key)"
                :src="row.image"
                alt=""
                loading="lazy"
                @error="onImageError(row.key)"
              />
              <template v-else>{{ row.name.charAt(0) }}</template>
            </span>
            <span class="person-main">
              <span class="person-name">{{ row.name }}</span>
              <span class="person-key">{{ row.key }}</span>
            </span>
          </span>
        </template>
        <template #cell-tmdb_id="{ row }">
          <span v-if="row.tmdb_id" class="mono">{{ row.tmdb_id }}</span>
          <span v-else class="dim">无（姓名键）</span>
        </template>
        <template #cell-credits_count="{ row }">{{ row.credits_count }}</template>
        <template #cell-works_count="{ row }">{{ row.works_count }}</template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openRename(row)">
            <Pencil :size="13" class="btn-ico" />改名
          </el-button>
          <el-button link type="danger" @click="openMerge(row)">
            <Merge :size="13" class="btn-ico" />合并到…
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <!-- ==================== 改名 ==================== -->
    <el-dialog v-model="renameDialog.visible" title="人物改名" width="min(440px, 92vw)">
      <el-form label-width="76px">
        <el-form-item label="当前名">
          <span>{{ renameDialog.row?.name }}</span>
          <span class="dim key-note">{{ renameDialog.row?.key }}</span>
        </el-form-item>
        <el-form-item label="新名">
          <el-input
            v-model="renameDialog.newName"
            maxlength="200"
            placeholder="输入新的显示名"
            @keyup.enter="submitRename"
          />
        </el-form-item>
        <el-form-item label=" ">
          <span class="dialog-hint">
            只改显示名：该人物的 {{ renameDialog.row?.credits_count }} 条条目关联会一起换名，
            TMDB id 绑定、角色与头像都不动。与另一人物同名会被拒绝——并人请用「合并到…」。
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="renameDialog.saving"
          :disabled="!renameDialog.newName.trim()"
          @click="submitRename"
        >
          确认改名
        </el-button>
      </template>
    </el-dialog>

    <!-- ==================== 合并（破坏性，二次确认） ==================== -->
    <el-dialog v-model="mergeDialog.visible" title="合并人物" width="min(560px, 94vw)">
      <div v-if="mergeDialog.source" class="merge-body">
        <p class="merge-line">
          源人物：<strong>{{ mergeDialog.source.name }}</strong>
          <span class="dim">（{{ mergeDialog.source.key }}，{{ mergeDialog.source.credits_count }} 条关联 / {{ mergeDialog.source.works_count }} 部作品）</span>
        </p>
        <div class="target-search">
          <el-input
            v-model="mergeDialog.targetQ"
            placeholder="搜目标人物（姓名或 TMDB ID）"
            @keyup.enter="searchTargets"
          />
          <el-button :loading="mergeDialog.searching" @click="searchTargets">搜索</el-button>
        </div>
        <div v-if="mergeDialog.candidates.length" class="candidate-list">
          <button
            v-for="cand in mergeDialog.candidates"
            :key="cand.key"
            type="button"
            class="candidate"
            :class="{ selected: mergeDialog.target?.key === cand.key }"
            @click="selectTarget(cand)"
          >
            <span class="avatar avatar-sm">
              <img
                v-if="cand.image && !brokenImages.has(cand.key)"
                :src="cand.image"
                alt=""
                loading="lazy"
                @error="onImageError(cand.key)"
              />
              <template v-else>{{ cand.name.charAt(0) }}</template>
            </span>
            <span class="person-main">
              <span class="person-name">{{ cand.name }}</span>
              <span class="person-key">{{ cand.key }} · {{ cand.credits_count }} 条关联 / {{ cand.works_count }} 部作品</span>
            </span>
          </button>
        </div>

        <div v-if="mergeDialog.previewLoading" class="merge-preview dim">正在计算合并预览…</div>
        <div v-else-if="mergeDialog.preview" class="merge-preview">
          <p class="merge-line">
            目标人物：<strong>{{ mergeDialog.preview.target.name }}</strong>
            <span class="dim">（{{ mergeDialog.preview.target.key }}，{{ mergeDialog.preview.target.credits_count }} 条关联 / {{ mergeDialog.preview.target.works_count }} 部作品）</span>
          </p>
          <ul class="merge-counts">
            <li>源人物的 {{ mergeDialog.preview.source.credits_count }} 条关联中，<strong>{{ mergeDialog.preview.will_migrate }} 条</strong>将改指目标人物；</li>
            <li><strong>{{ mergeDialog.preview.will_drop_duplicates }} 条</strong>与目标已有条目重复，将删除（目标行优先）；</li>
            <li v-if="mergeDialog.preview.favorites_to_migrate">指向源人物的 <strong>{{ mergeDialog.preview.favorites_to_migrate }} 条</strong>用户收藏将改指目标；</li>
            <li>合并后目标人物共 <strong>{{ mergeDialog.preview.result.credits_count }} 条关联 / {{ mergeDialog.preview.result.works_count }} 部作品</strong>，源人物 <strong>{{ mergeDialog.preview.source.name }}</strong> 将消失。</li>
          </ul>
          <el-checkbox v-model="mergeDialog.confirmed" class="merge-confirm">
            我确认把「{{ mergeDialog.preview.source.name }}」的全部关联迁移到「{{ mergeDialog.preview.target.name }}」，此操作不可撤销
          </el-checkbox>
        </div>
      </div>
      <template #footer>
        <el-button @click="mergeDialog.visible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="mergeDialog.merging"
          :disabled="!mergeDialog.preview || !mergeDialog.confirmed"
          @click="submitMerge"
        >
          确认合并
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.people-page { display: flex; flex-direction: column; gap: 16px; }

.filter-actions { display: flex; gap: 8px; margin-top: 12px; }
.btn-ico { margin-right: 4px; }

.person-cell { display: flex; align-items: center; gap: 10px; min-width: 0; }
.avatar {
  flex: none;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--au-bg-soft);
  color: var(--au-text-3);
  font-size: var(--uhd-fs-caption);
  font-weight: 600;
}
.avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
.avatar-sm { width: 30px; height: 30px; }
.person-main { display: flex; flex-direction: column; min-width: 0; }
.person-name { font-weight: 600; color: var(--au-text); }
.person-key { font-size: var(--uhd-fs-aux-sm); color: var(--au-text-3); }

.mono { font-family: var(--font-mono); font-size: var(--uhd-fs-aux-sm); }
.dim { color: var(--au-text-3); }
.key-note { margin-left: 8px; font-size: var(--uhd-fs-aux-sm); }
.dialog-hint { font-size: var(--uhd-fs-aux); color: var(--au-text-3); line-height: 1.7; }

.merge-body { display: flex; flex-direction: column; gap: 12px; }
.merge-line { margin: 0; line-height: 1.7; }
.target-search { display: flex; gap: 8px; }
.candidate-list { display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow: auto; }
.candidate {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--au-border);
  border-radius: var(--au-r-md);
  background: var(--au-surface);
  cursor: pointer;
  text-align: left;
}
.candidate:hover { border-color: var(--au-border-strong); }
.candidate.selected { border-color: var(--au-primary); background: var(--au-primary-soft); }
.merge-preview { border-top: 1px solid var(--au-border); padding-top: 12px; }
.merge-counts { margin: 8px 0 12px; padding-left: 18px; line-height: 1.8; color: var(--au-text-2); }
.merge-confirm { white-space: normal; height: auto; line-height: 1.6; }
</style>
