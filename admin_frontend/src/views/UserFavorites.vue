<script setup lang="ts">
/**
 * 用户收藏查看（二期缺口项 6）：按用户只读查看收藏
 * （GET /api/admin/users/{id}/favorites）。
 *
 * 客服核对/投诉排查用：看某个用户收藏了哪些作品与演职人员。
 * 纯只读——收藏是用户个人数据，这里不提供删除/代改；条目已
 * 下架的收藏标记仍会显示并标注「已下架」（与门户「标记不删、
 * 重绑回来」口径一致）。独立页而非塞进用户页：用户页已很满，
 * 且本页是「先选人再看收藏」的独立排查流。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import { fetchUsers } from '@/api/admin'
import {
  fetchUserFavorites,
  type UserFavoritesResult,
} from '@/api/user-favorites'

interface UserOption {
  id: number
  username: string
}

const userOptions = ref<UserOption[]>([])
const userSearching = ref(false)
const selectedUserId = ref<number | undefined>(undefined)
const result = ref<UserFavoritesResult | null>(null)
const loading = ref(false)

const itemColumns: DataColumn[] = [
  { key: 'name', label: '名称', minWidth: 220, mobile: 'title' },
  { key: 'type', label: '类型', width: 100 },
  { key: 'year', label: '年份', width: 80 },
  { key: 'rating', label: '评分', width: 80 },
  { key: 'favorited_at', label: '收藏时间', width: 168 },
]

const personColumns: DataColumn[] = [
  { key: 'name', label: '姓名', minWidth: 200, mobile: 'title' },
  { key: 'person_tmdb_id', label: 'TMDB 号', width: 120 },
  { key: 'created_at', label: '收藏时间', width: 168 },
]

async function searchUsers(keyword: string) {
  if (!keyword.trim()) {
    userOptions.value = []
    return
  }
  userSearching.value = true
  try {
    const res = await fetchUsers({ search: keyword.trim(), limit: 20 })
    userOptions.value = res.users.map((u) => ({ id: u.id, username: u.username }))
  } catch (err) {
    ElMessage.error(toAdminError(err, '用户搜索失败'))
  } finally {
    userSearching.value = false
  }
}

async function onUserChange(userId: number | undefined) {
  result.value = null
  if (!userId) return
  loading.value = true
  try {
    result.value = await fetchUserFavorites(userId)
  } catch (err) {
    ElMessage.error(toAdminError(err, '收藏加载失败'))
  } finally {
    loading.value = false
  }
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

const TYPE_LABELS: Record<string, string> = {
  movie: '电影',
  series: '剧集',
  season: '季',
  episode: '单集',
}
</script>

<template>
  <div class="fav-page">
    <PageHeader
      eyebrow="用户"
      title="用户收藏"
      description="按用户查看他收藏的作品和演职人员，客服核对、排查投诉时用。这里只看不动：收藏是用户自己的数据，不提供删除或代改。"
    />

    <SectionCard title="选择用户">
      <el-select
        v-model="selectedUserId"
        filterable
        remote
        clearable
        placeholder="输入用户名搜索"
        :remote-method="searchUsers"
        :loading="userSearching"
        class="user-picker"
        @change="onUserChange"
      >
        <el-option
          v-for="u in userOptions"
          :key="u.id"
          :label="`${u.username}（#${u.id}）`"
          :value="u.id"
        />
      </el-select>
    </SectionCard>

    <template v-if="result">
      <SectionCard :title="`作品收藏（共 ${result.counts.items} 条）`">
        <DataTable
          :columns="itemColumns"
          :rows="result.items"
          :loading="loading"
          row-key="item_id"
          empty="这个用户还没有收藏作品"
        >
          <template #cell-name="{ row }">
            {{ row.name || `条目 #${row.item_id}` }}
            <span v-if="row.item_deleted" class="st-deleted">已下架</span>
          </template>
          <template #cell-type="{ row }">
            {{ TYPE_LABELS[row.type] ?? (row.type || '—') }}
          </template>
          <template #cell-year="{ row }">
            {{ row.year ?? '—' }}
          </template>
          <template #cell-rating="{ row }">
            {{ row.rating != null ? row.rating.toFixed(1) : '—' }}
          </template>
          <template #cell-favorited_at="{ row }">
            <span class="mono">{{ fmtTime(row.favorited_at) }}</span>
          </template>
        </DataTable>
      </SectionCard>

      <SectionCard :title="`演职人员收藏（共 ${result.counts.persons} 条）`">
        <DataTable
          :columns="personColumns"
          :rows="result.persons"
          :loading="loading"
          row-key="person_key"
          empty="这个用户还没有收藏演职人员"
        >
          <template #cell-person_tmdb_id="{ row }">
            {{ row.person_tmdb_id || '—' }}
          </template>
          <template #cell-created_at="{ row }">
            <span class="mono">{{ fmtTime(row.created_at) }}</span>
          </template>
        </DataTable>
      </SectionCard>
    </template>
  </div>
</template>

<style scoped>
.fav-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.user-picker {
  width: min(420px, 100%);
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--ax-fs-aux-sm);
}

.st-deleted {
  margin-left: 8px;
  color: var(--au-danger, #e03131);
  font-size: var(--ax-fs-aux-sm);
}
</style>
