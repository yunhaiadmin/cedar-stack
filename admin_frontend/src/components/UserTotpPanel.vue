<script setup lang="ts">
/**
 * 用户详情抽屉 ·「动态验证码」页签：该用户的 TOTP 状态与强制重置
 * （大改造二期 · 项 4）。
 *
 * 管理员在这里只看状态（是否已启用/是否设置中/启用时间），绝不接触
 * 密钥本身；用户丢失验证器设备时可强制重置——清除绑定后用户能从零
 * 重新设置。重置走二次确认弹窗，后端写 AdminLog。
 */
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ShieldCheck, Trash2 } from 'lucide-vue-next'
import { EmptyState } from '@/components/ui'
import {
  fetchUserTotp, resetUserTotp, type AdminUserTotpStatus,
} from '@/api/admin'
import { parseServerTime } from '@/utils/serverTime'

const props = defineProps<{ userId: number; username: string }>()

const loading = ref(false)
const loadError = ref(false)
const data = ref<AdminUserTotpStatus | null>(null)
const resetting = ref(false)

function fmtTime(v: string | null): string {
  if (!v) return '—'
  const d = parseServerTime(v)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  loadError.value = false
  try {
    data.value = await fetchUserTotp(props.userId)
  } catch {
    loadError.value = true
    data.value = null
  } finally {
    loading.value = false
  }
}

async function reset() {
  try {
    await ElMessageBox.confirm(
      `确定要强制重置「${props.username}」的动态验证码吗？重置后其现有绑定立即失效，` +
      '下次登录不再要求验证码，直到他重新设置。此操作会记入操作日志。',
      '强制重置动态验证码',
      { type: 'warning', confirmButtonText: '强制重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  resetting.value = true
  try {
    await resetUserTotp(props.userId)
    ElMessage.success('已重置，该用户可重新设置动态验证码')
    await load()
  } catch {
    ElMessage.error('重置失败，请重试')
  } finally {
    resetting.value = false
  }
}

onMounted(load)
watch(() => props.userId, load)
</script>

<template>
  <div v-loading="loading" class="totp-panel">
    <EmptyState
      v-if="loadError"
      compact
      :icon="ShieldCheck"
      title="动态验证码状态读取失败"
      description="不影响上面的资料与操作；关掉抽屉重开可重试。"
    />
    <template v-else-if="data">
      <div class="totp-status">
        <span
          class="au-badge"
          :class="data.enabled ? 'au-badge-green' : data.pending_setup ? 'au-badge-amber' : 'au-badge-muted'"
        >
          {{ data.enabled ? '已启用' : data.pending_setup ? '设置中（未确认）' : '未设置' }}
        </span>
        <span class="totp-note">
          <template v-if="data.enabled">
            启用时间 {{ fmtTime(data.confirmed_at) }}。用户丢失验证器设备、无法登录时，
            可强制重置其绑定，重置后他能从零重新设置。
          </template>
          <template v-else-if="data.pending_setup">
            该用户已开始设置但尚未输码确认（开始于 {{ fmtTime(data.confirmed_at ?? data.created_at) }}），
            登录暂不要求验证码；如需清理这次未完成的设置，可强制重置。
          </template>
          <template v-else>
            该用户没有绑定动态验证码，无需处理。
          </template>
        </span>
      </div>
      <div v-if="data.enabled || data.pending_setup" class="totp-actions">
        <el-button
          type="danger"
          plain
          size="small"
          :loading="resetting"
          @click="reset"
        >
          <Trash2 :size="13" class="btn-ico" />强制重置
        </el-button>
        <span class="totp-meta">
          创建于 {{ fmtTime(data.created_at) }} · 最近更新 {{ fmtTime(data.updated_at) }}
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.totp-panel { min-height: 120px; }
.totp-status { display: flex; align-items: flex-start; gap: 10px; }
.totp-status .au-badge { flex: none; margin-top: 2px; }
.totp-note {
  color: var(--el-text-color-secondary);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
}
.totp-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
}
.totp-meta { color: var(--el-text-color-secondary); font-size: var(--uhd-fs-aux-sm); }
.btn-ico { margin-right: 4px; vertical-align: -2px; }
</style>
