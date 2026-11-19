<script setup lang="ts">
/**
 * 用户详情抽屉 ·「账号安全」页签：该用户的 Passkey 凭据（大改造二期 · 项 3）。
 *
 * 管理员在这里只做两件事：核对凭据清单（设备名/创建时间/最近使用），
 * 以及在账号恢复场景下吊销单个凭据。吊销走二次确认弹窗，后端写
 * AdminLog；列表数据后端只下发展示字段，不含任何凭据材料。
 */
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { KeyRound, Trash2 } from 'lucide-vue-next'
import { EmptyState } from '@/components/ui'
import {
  fetchUserPasskeys, revokeUserPasskey,
  type AdminUserPasskey, type AdminUserPasskeys,
} from '@/api/admin'
import { parseServerTime } from '@/utils/serverTime'

const props = defineProps<{ userId: number }>()

const loading = ref(false)
const loadError = ref(false)
const data = ref<AdminUserPasskeys | null>(null)
const revokingId = ref<number | null>(null)

function fmtTime(v: string | null, emptyText: string): string {
  if (!v) return emptyText
  const d = parseServerTime(v)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  loadError.value = false
  try {
    data.value = await fetchUserPasskeys(props.userId)
  } catch {
    loadError.value = true
    data.value = null
  } finally {
    loading.value = false
  }
}

async function revoke(pk: AdminUserPasskey) {
  const name = pk.device_name || '未命名设备'
  try {
    await ElMessageBox.confirm(
      `确定要吊销「${name}」这枚 Passkey 吗？吊销后该设备将无法再用它登录，此操作会记入操作日志。`,
      '吊销 Passkey',
      { type: 'warning', confirmButtonText: '吊销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  revokingId.value = pk.id
  try {
    await revokeUserPasskey(props.userId, pk.id)
    ElMessage.success('已吊销')
    await load()
  } catch {
    ElMessage.error('吊销失败，请重试')
  } finally {
    revokingId.value = null
  }
}

onMounted(load)
watch(() => props.userId, load)
</script>

<template>
  <div v-loading="loading" class="pk-panel">
    <EmptyState
      v-if="loadError"
      compact
      :icon="KeyRound"
      title="Passkey 清单读取失败"
      description="不影响上面的资料与操作；关掉抽屉重开可重试。"
    />
    <template v-else-if="data">
      <p class="pk-summary">
        共 {{ data.items.length }} 枚（上限 {{ data.max_per_user }} 枚）。
        用户设备全丢、无法登录时，可在这里吊销其凭据助其恢复账号。
      </p>
      <EmptyState
        v-if="data.items.length === 0"
        compact
        :icon="KeyRound"
        title="还没有绑定 Passkey"
        description="该用户尚未在账号安全页添加任何 Passkey。"
      />
      <div v-else class="pk-list">
        <div v-for="pk in data.items" :key="pk.id" class="pk-row">
          <div class="pk-main">
            <span class="pk-name">{{ pk.device_name || '未命名设备' }}</span>
            <span class="pk-meta">
              创建于 {{ fmtTime(pk.created_at, '—') }} · 最近使用
              {{ fmtTime(pk.last_used_at, '从未使用') }} · 已验证 {{ pk.sign_count }} 次
              <template v-if="pk.transports"> · {{ pk.transports }}</template>
            </span>
          </div>
          <el-button
            size="small"
            type="danger"
            plain
            :loading="revokingId === pk.id"
            @click="revoke(pk)"
          >
            <Trash2 :size="13" class="btn-ico" />吊销
          </el-button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pk-panel { min-height: 120px; }
.pk-summary {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
}
.pk-list { display: flex; flex-direction: column; gap: 8px; }
.pk-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
}
.pk-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.pk-name { font-size: var(--uhd-fs-caption); font-weight: 600; }
.pk-meta { color: var(--el-text-color-secondary); font-size: var(--uhd-fs-aux-sm); }
.btn-ico { margin-right: 4px; vertical-align: -2px; }
</style>
