/**
 * 账号安全三件套共享状态（原 SecurityTrioPanel 拆分后共用）：
 * 动态验证码（TotpPanel）/ Passkeys（PasskeysPanel）/ 平台 API Key
 * （ApiKeysPanel）分属设置页三个分组，但数据同走 securityApi.overview
 * 一次拉齐。此处以模块级单例持有 overview 与跨面板共用的 busy，
 * 三个面板挂载时调 loadOverview，并发调用自动去重为一次请求；
 * 任一面板动作成功后调 loadOverview 刷新，其余面板同步更新。
 */
import { ref } from 'vue'
import { securityApi, type SecurityOverview } from '@/api/security'
import { humanizeError } from '@/composables/usePanelApi'
import { parseServerTime } from '@/utils/serverTime'

const overview = ref<SecurityOverview | null>(null)
const loadError = ref('')
const busy = ref(false)
let inflight: Promise<void> | null = null

async function loadOverview(): Promise<void> {
  if (inflight) return inflight
  loadError.value = ''
  inflight = (async () => {
    try {
      overview.value = await securityApi.overview()
    } catch (e) {
      loadError.value = humanizeError(e)
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** 安全面板统一日期格式（原 SecurityTrioPanel 口径，行为不变） */
export function fmtSecurityDate(iso?: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export function useSecurityOverview() {
  return { overview, loadError, busy, loadOverview }
}
