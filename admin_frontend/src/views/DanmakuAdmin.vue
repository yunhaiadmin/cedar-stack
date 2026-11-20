<script setup lang="ts">
/**
 * 弹幕治理（内容治理面，2026-11-19）：站内自发弹幕的管理员处置页
 * （GET/DELETE /api/admin/danmaku/comments、GET/POST/DELETE /api/admin/danmaku/mutes）。
 *
 * 与「弹幕设置」（系统设置页的弹幕设置组：开关/敏感词/间隔/字数）分工：
 * 设置管规则，这里管内容——看全站弹幕、删违规单条、给用户禁言。
 * 删除是硬删（danmaku_comments 无软删字段），删除快照记操作日志；
 * 禁言在发送口真生效（被禁言用户发送回 403），到期自动失效。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import {
  createDanmakuMute,
  deleteDanmakuComment,
  deleteDanmakuMute,
  fetchDanmakuComments,
  fetchDanmakuMutes,
  type DanmakuCommentRow,
  type DanmakuMuteRow,
} from '@/api/danmaku-admin'

const PAGE_SIZE = 50

const filters = reactive({
  keyword: '',
  userId: null as number | null,
  itemId: null as number | null,
})

const rows = ref<DanmakuCommentRow[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const deletingId = ref<number | null>(null)

const muteRows = ref<DanmakuMuteRow[]>([])
const muteTotal = ref(0)
const muteOffset = ref(0)
const muteLoading = ref(false)
const unmutingId = ref<number | null>(null)

const commentColumns: DataColumn[] = [
  { key: 'text', label: '弹幕正文', minWidth: 220, mobile: 'title' },
  { key: 'username', label: '发送用户', width: 140 },
  { key: 'item_name', label: '条目', minWidth: 160 },
  { key: 'position_seconds', label: '时间点', width: 90 },
  { key: 'created_at', label: '发送时间', width: 168 },
  { key: 'actions', label: '操作', width: 130 },
]

const muteColumns: DataColumn[] = [
  { key: 'username', label: '被禁言用户', width: 160, mobile: 'title' },
  { key: 'muted_until', label: '禁言到期', width: 170 },
  { key: 'reason', label: '理由', minWidth: 160 },
  { key: 'updated_at', label: '最近设置', width: 168 },
  { key: 'actions', label: '操作', width: 100 },
]

const pageNum = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)
const mutePageNum = computed(() => Math.floor(muteOffset.value / PAGE_SIZE) + 1)

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function fmtPosition(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

async function load() {
  loading.value = true
  try {
    const res = await fetchDanmakuComments({
      limit: PAGE_SIZE,
      offset: offset.value,
      keyword: filters.keyword.trim() || undefined,
      user_id: filters.userId ?? undefined,
      item_id: filters.itemId ?? undefined,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '弹幕列表加载失败'))
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function loadMutes() {
  muteLoading.value = true
  try {
    const res = await fetchDanmakuMutes({ limit: PAGE_SIZE, offset: muteOffset.value })
    muteRows.value = res.items
    muteTotal.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '禁言名单加载失败'))
    muteRows.value = []
    muteTotal.value = 0
  } finally {
    muteLoading.value = false
  }
}

function search() {
  offset.value = 0
  void load()
}

function resetFilters() {
  filters.keyword = ''
  filters.userId = null
  filters.itemId = null
  search()
}

function onPageChange(page: number) {
  offset.value = (page - 1) * PAGE_SIZE
  void load()
}

function onMutePageChange(page: number) {
  muteOffset.value = (page - 1) * PAGE_SIZE
  void loadMutes()
}

async function onDelete(row: DanmakuCommentRow) {
  try {
    await ElMessageBox.confirm(
      `删除用户「${row.username || row.user_id}」在《${row.item_name || row.item_id}》` +
      `发的弹幕「${row.text}」？删除后立即从播放器消失，操作会记入操作日志。`,
      '删除弹幕',
      { confirmButtonText: '确认删除', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  deletingId.value = row.id
  try {
    await deleteDanmakuComment(row.id)
    ElMessage.success('弹幕已删除')
    void load()
  } catch (err) {
    ElMessage.error(toAdminError(err, '删除弹幕失败'))
  } finally {
    deletingId.value = null
  }
}

// ---------------- 禁言弹窗 ----------------
const muteDialog = reactive({
  visible: false,
  saving: false,
  userId: 0,
  username: '',
  mode: '168' as '24' | '168' | '720' | 'permanent',
  reason: '',
})

function openMute(row: DanmakuCommentRow) {
  muteDialog.userId = row.user_id
  muteDialog.username = row.username || `用户 #${row.user_id}`
  muteDialog.mode = '168'
  muteDialog.reason = ''
  muteDialog.visible = true
}

async function submitMute() {
  muteDialog.saving = true
  try {
    const permanent = muteDialog.mode === 'permanent'
    await createDanmakuMute({
      user_id: muteDialog.userId,
      permanent,
      duration_hours: permanent ? null : Number(muteDialog.mode),
      reason: muteDialog.reason.trim() || undefined,
    })
    ElMessage.success(`已禁言「${muteDialog.username}」`)
    muteDialog.visible = false
    void loadMutes()
  } catch (err) {
    ElMessage.error(toAdminError(err, '禁言失败'))
  } finally {
    muteDialog.saving = false
  }
}

async function onUnmute(row: DanmakuMuteRow) {
  try {
    await ElMessageBox.confirm(
      `解除用户「${row.username || row.user_id}」的弹幕禁言？解除后立即可以发送弹幕。`,
      '解除禁言',
      { confirmButtonText: '确认解禁', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  unmutingId.value = row.user_id
  try {
    await deleteDanmakuMute(row.user_id)
    ElMessage.success('已解除禁言')
    void loadMutes()
  } catch (err) {
    ElMessage.error(toAdminError(err, '解禁失败'))
  } finally {
    unmutingId.value = null
  }
}

onMounted(() => {
  void load()
  void loadMutes()
})
</script>

<template>
  <div class="danmaku-page">
    <PageHeader
      eyebrow="内容"
      title="弹幕治理"
      description="站内弹幕的内容处置面：看全站弹幕、删违规单条、给用户禁言（发送时真生效，到期自动失效）。规则类设置（开关、敏感词、发送间隔、字数上限）在「系统设置 → 弹幕设置」里，这里不重复。删除与禁言都会记入操作日志。"
    />

    <SectionCard title="筛选">
      <FilterBar
        :search="{ value: filters.keyword, placeholder: '按正文、用户名或条目名搜', mode: 'submit' }"
        :loading="loading"
        @update:search="filters.keyword = $event"
        @search-submit="search"
        @query="search"
      />
      <div class="filter-extra">
        <el-input-number
          v-model="filters.userId"
          :min="1"
          controls-position="right"
          placeholder="用户 ID"
          class="id-input"
        />
        <el-input-number
          v-model="filters.itemId"
          :min="1"
          controls-position="right"
          placeholder="条目 ID"
          class="id-input"
        />
        <el-button type="primary" @click="search">按 ID 查询</el-button>
        <el-button @click="resetFilters">
          <RefreshCw :size="14" /> 重置
        </el-button>
      </div>
    </SectionCard>

    <SectionCard :title="`弹幕列表（共 ${total} 条）`">
      <DataTable
        :columns="commentColumns"
        :rows="rows"
        :loading="loading"
        row-key="id"
        empty="没有符合条件的弹幕"
        empty-description="用户在网页播放器发送的弹幕会出现在这里。"
        :pagination="{ total, page: pageNum, pageSize: PAGE_SIZE }"
        @page-change="onPageChange"
      >
        <template #cell-username="{ row }">
          {{ row.username || `用户 #${row.user_id}` }}
        </template>
        <template #cell-item_name="{ row }">
          {{ row.item_name || `条目 #${row.item_id}` }}
        </template>
        <template #cell-position_seconds="{ row }">
          <span class="mono">{{ fmtPosition(row.position_seconds) }}</span>
        </template>
        <template #cell-created_at="{ row }">
          <span class="mono">{{ fmtTime(row.created_at) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="danger" :loading="deletingId === row.id" @click="onDelete(row)">
            删除
          </el-button>
          <el-button link type="warning" @click="openMute(row)">
            禁言
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <SectionCard :title="`禁言名单（共 ${muteTotal} 条）`">
      <DataTable
        :columns="muteColumns"
        :rows="muteRows"
        :loading="muteLoading"
        row-key="id"
        empty="当前没有禁言记录"
        empty-description="在上方弹幕列表里对某位用户点「禁言」即可新增；到期会自动失效，记录保留在这里。"
        :pagination="{ total: muteTotal, page: mutePageNum, pageSize: PAGE_SIZE }"
        @page-change="onMutePageChange"
      >
        <template #cell-username="{ row }">
          {{ row.username || `用户 #${row.user_id}` }}
        </template>
        <template #cell-muted_until="{ row }">
          <span v-if="row.permanent" class="st-permanent">永久</span>
          <span v-else-if="row.active" class="mono">{{ fmtTime(row.muted_until) }}</span>
          <span v-else class="dim">{{ fmtTime(row.muted_until) }}（已过期）</span>
        </template>
        <template #cell-reason="{ row }">
          {{ row.reason || '—' }}
        </template>
        <template #cell-updated_at="{ row }">
          <span class="mono">{{ fmtTime(row.updated_at) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="danger" :loading="unmutingId === row.user_id" @click="onUnmute(row)">
            解禁
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <el-dialog v-model="muteDialog.visible" title="设置弹幕禁言" width="min(460px, 92vw)">
      <el-form label-width="76px">
        <el-form-item label="用户">
          <span>{{ muteDialog.username }}</span>
        </el-form-item>
        <el-form-item label="时长">
          <el-radio-group v-model="muteDialog.mode">
            <el-radio-button value="24">1 天</el-radio-button>
            <el-radio-button value="168">7 天</el-radio-button>
            <el-radio-button value="720">30 天</el-radio-button>
            <el-radio-button value="permanent">永久</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="理由">
          <el-input
            v-model="muteDialog.reason"
            maxlength="255"
            placeholder="可选，记入操作日志"
          />
        </el-form-item>
      </el-form>
      <p class="dialog-hint">
        禁言只限制发送弹幕，不影响观看与其它功能；禁言期间该用户发送弹幕会被明确拒绝。已在禁言中的用户再次设置会覆盖原到期时间。
      </p>
      <template #footer>
        <el-button @click="muteDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="muteDialog.saving" @click="submitMute">
          确认禁言
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.danmaku-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.filter-extra {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
  align-items: center;
}

.id-input {
  width: 160px;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--ax-fs-aux-sm);
}

.dim {
  color: var(--au-text-3);
}

.st-permanent {
  color: var(--au-danger, #d4380d);
  font-weight: 600;
}

.dialog-hint {
  margin: 4px 0 0;
  color: var(--au-text-3);
  font-size: var(--ax-fs-aux-sm);
  line-height: 1.6;
}
</style>
