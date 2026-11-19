<script setup lang="ts">
/**
 * 一起看管理（后台大改造二期）：一起看房间的只读观测 + 强制关房
 * （GET/POST /api/admin/watch-together/*）。
 *
 * 房间里只流转控制信号，这里同样只看控制面：条目、房主、成员、
 * 播放态。不下发任何流地址或令牌；关房只终止同步会话，成员各自
 * 的播放页不受影响。关房是写动作，服务端记审计日志。
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCw } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { toAdminError } from '@/utils/errorMessage'
import {
  closeWtRoom,
  fetchWtRoom,
  fetchWtRooms,
  type WtRoom,
} from '@/api/watch-together'

const rows = ref<WtRoom[]>([])
const loading = ref(false)
const closingCode = ref('')

const { isPhone } = useBreakpoint()

const columns: DataColumn[] = [
  { key: 'code', label: '房间码', width: 100 },
  { key: 'item', label: '条目', minWidth: 180, mobile: 'title' },
  { key: 'host', label: '房主', width: 130 },
  { key: 'members', label: '成员', width: 80 },
  { key: 'state', label: '播放态', width: 150 },
  { key: 'created_at', label: '建房时间', width: 150 },
  { key: 'actions', label: '操作', width: 180 },
]

async function load() {
  loading.value = true
  try {
    const res = await fetchWtRooms()
    rows.value = res.items
  } catch (err) {
    ElMessage.error(toAdminError(err, '一起看房间加载失败'))
    rows.value = []
  } finally {
    loading.value = false
  }
}

function fmtClock(epochSec: number | null): string {
  if (!epochSec) return '—'
  return new Date(epochSec * 1000).toLocaleString()
}

function fmtPosition(sec: number): string {
  const total = Math.max(0, Math.floor(sec || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

// ---------- 详情抽屉 ----------
const detailVisible = ref(false)
const detailRoom = ref<WtRoom | null>(null)
const detailLoading = ref(false)

async function openDetail(row: WtRoom) {
  detailVisible.value = true
  detailLoading.value = true
  detailRoom.value = row
  try {
    detailRoom.value = await fetchWtRoom(row.code)
  } catch (err) {
    ElMessage.error(toAdminError(err, '房间详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

// ---------- 强制关房 ----------
async function forceClose(row: WtRoom) {
  try {
    await ElMessageBox.confirm(
      `关掉房间 ${row.code}？房里 ${row.member_count} 个人会立刻收到结束提示，同步会话终止；他们各自正在播的画面不会被关掉。`,
      '强制关房',
      { type: 'warning', confirmButtonText: '关房', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  closingCode.value = row.code
  try {
    await closeWtRoom(row.code)
    ElMessage.success(`房间 ${row.code} 已关闭`)
    if (detailRoom.value?.code === row.code) detailVisible.value = false
    void load()
  } catch (err) {
    ElMessage.error(toAdminError(err, '关房失败'))
  } finally {
    closingCode.value = ''
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="wt-page">
    <PageHeader
      eyebrow="内容"
      title="一起看管理"
      description="正在进行的一起看房间都在这里。房间里只走同步信号，这里也只看条目、房主、成员和播放进度，不经手任何播放地址。遇到捣乱或僵住的房间可以强制关房，关房会记进操作日志。"
    />

    <SectionCard :title="`房间（共 ${rows.length} 个）`">
      <template #actions>
        <el-button :loading="loading" @click="load">
          <RefreshCw :size="14" /> 刷新
        </el-button>
      </template>
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        row-key="code"
        empty="现在没有一起看房间"
        empty-description="有人建房后会自动出现在这里。"
      >
        <template #cell-item="{ row }">
          <div class="item-cell">
            <span>{{ row.item_name || '（未知条目）' }}</span>
            <span class="mono dim">{{ row.item_id }}</span>
          </div>
        </template>
        <template #cell-host="{ row }">
          {{ row.host_name || `用户 ${row.host_id}` }}
        </template>
        <template #cell-members="{ row }">
          {{ row.member_count }} / {{ row.max_members }}
        </template>
        <template #cell-state="{ row }">
          <span class="state-cell">
            <span :class="row.state.playing ? 'dot dot-on' : 'dot'" />
            {{ row.state.playing ? '播放中' : '暂停' }}
            <span class="mono dim">{{ fmtPosition(row.state.position) }}</span>
          </span>
        </template>
        <template #cell-created_at="{ row }">
          <span class="mono">{{ fmtClock(row.created_at) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          <el-button
            link
            type="danger"
            :loading="closingCode === row.code"
            @click="forceClose(row)"
          >
            关房
          </el-button>
        </template>
      </DataTable>
    </SectionCard>

    <el-drawer
      v-model="detailVisible"
      :title="detailRoom ? `房间详情 · ${detailRoom.code}` : '房间详情'"
      :size="isPhone ? '100%' : '520px'"
    >
      <div v-if="detailRoom" class="detail">
        <div class="detail-row">
          <span class="detail-label">条目</span>
          <span>{{ detailRoom.item_name || '（未知条目）' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">条目 ID</span>
          <span class="mono">{{ detailRoom.item_id }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">房主</span>
          <span>{{ detailRoom.host_name || `用户 ${detailRoom.host_id}` }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">建房时间</span>
          <span class="mono">{{ fmtClock(detailRoom.created_at) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">同步状态</span>
          <span>
            {{ detailRoom.state.playing ? '播放中' : '暂停' }} ·
            进度 {{ fmtPosition(detailRoom.state.position) }} ·
            倍速 {{ detailRoom.state.rate }}x
          </span>
        </div>
        <div class="detail-row">
          <span class="detail-label">人数上限</span>
          <span>{{ detailRoom.max_members }} 人 · 房间最长保留 {{ Math.round(detailRoom.ttl_seconds / 3600) }} 小时</span>
        </div>

        <h4 class="detail-sub">成员（{{ detailRoom.member_count }}）</h4>
        <ul v-if="detailRoom.members.length" class="member-list">
          <li v-for="m in detailRoom.members" :key="m.user_id">
            <span>{{ m.username || `用户 ${m.user_id}` }}</span>
            <span v-if="m.is_host" class="host-tag">房主</span>
          </li>
        </ul>
        <p v-else class="dim">
          当前进程里没有这个房间的连接（可能刚重启过），成员名单看不到；房间记录还在，关房照常有效。
        </p>

        <div class="detail-actions">
          <el-button
            type="danger"
            :loading="closingCode === detailRoom.code"
            @click="forceClose(detailRoom)"
          >
            强制关房
          </el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.wt-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.item-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--uhd-fs-aux-sm);
}

.dim {
  color: var(--au-text-3);
}

.state-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: var(--au-r-full);
  background: var(--au-text-3);
  flex: none;
}

.dot-on {
  background: var(--au-success);
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-row {
  display: flex;
  gap: 12px;
  font-size: var(--uhd-fs-body);
  color: var(--au-text);
}

.detail-label {
  flex: none;
  width: 72px;
  color: var(--au-text-3);
  font-size: var(--uhd-fs-aux);
}

.detail-sub {
  margin: 14px 0 4px;
  font-size: var(--uhd-fs-lead);
  color: var(--au-text);
}

.member-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.member-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--uhd-fs-body);
  color: var(--au-text);
}

.host-tag {
  padding: 1px 8px;
  border-radius: var(--au-r-full);
  background: var(--au-surface-2);
  color: var(--au-text-2);
  font-size: var(--uhd-fs-micro);
}

.detail-actions {
  margin-top: 18px;
}
</style>
