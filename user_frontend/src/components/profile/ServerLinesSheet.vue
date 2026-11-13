<script setup lang="ts">
/**
 * ServerLinesSheet —— Emby 服务线路弹层（2026-11-13 形态对齐单）
 *
 * 友站概览点「服务器地址」是居中弹层（标题「Emby 服务线路」＋线路分组
 * ＋主/备线路行＋客户端区＋底部提示），我方原先点开跳 /game/operator
 * 整页；用户 2026-11-13 23:15 亲点改弹层，本组件即该弹层，播放账号页
 * 本体与菜单入口不变。
 *
 * 线路数据只用账号卡现行口径（与线路测速页「播放线路」组同源）：
 * realms[].base_url（没查看权限的服后端置空、自动滤掉），按默认服在前
 * 排序、按地址去重；realms 为空时回退顶层 base_url。够格看到几条渲染
 * 几条：只有一条就只显示一条，不假造备用线路。主/备徽标按排序口径：
 * 首条（默认服）为主线路，其后为备用线路；服名以标签原样展示。
 * 客户端区只指站内已有去处（安装应用页含客户端下载与分步教程），
 * 不编造外链；底部提示带线路测速链接（现有测速页）。
 *
 * 主题跟随概览页：dark 由父页按其主题解析值传入，弹层调色与概览页
 * 本地令牌同值（深 #17181c 卡 / 浅白卡），不随全局用户中心浅色面走。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, ChevronRight, Copy, X } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import './server-lines-sheet.css'

/**
 * 弹层只声明真正用到的账号卡字段子集，不直接收整个 AccountCard：
 * 账号卡经 ref 解包后，其纯 null 类型字段（emby_password）在 Vue 的
 * UnwrapRef 映射下会降级成 unknown，整卡传 prop 与原接口对不上
 * （代码库既有怪癖，此前没人整卡传过 prop 才未暴露）；子集字段均为
 * string/number/boolean，不受该降级影响。
 */
export interface ServerLinesRealm {
  id: number
  name: string
  base_url: string
  is_default: boolean
  is_free?: boolean
}

export interface ServerLinesCard {
  base_url: string
  server_name: string
  realm_name?: string | null
  is_free?: boolean
  realms?: ServerLinesRealm[]
}

const props = defineProps<{
  open: boolean
  card: ServerLinesCard | null
  dark: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const toast = useToast()

interface ServerLine {
  key: string
  name: string
  url: string
  primary: boolean
  free: boolean
}

/** 线路清单：账号卡 realms 现行口径（与测速页同源），默认服在前、按地址去重 */
const lines = computed<ServerLine[]>(() => {
  const card = props.card
  if (!card) return []
  const rows: ServerLine[] = []
  const seen = new Set<string>()
  const realms = [...(card.realms ?? [])].sort(
    (a, b) => Number(b.is_default) - Number(a.is_default),
  )
  for (const r of realms) {
    const url = (r.base_url ?? '').trim()
    if (!url || seen.has(url)) continue
    seen.add(url)
    rows.push({
      key: `realm-${r.id}`,
      name: r.name || '',
      url,
      primary: rows.length === 0,
      free: !!r.is_free,
    })
  }
  if (!rows.length) {
    const top = (card.base_url ?? '').trim()
    if (top) {
      rows.push({
        key: 'top',
        name: card.realm_name || card.server_name || '',
        url: top,
        primary: true,
        free: !!card.is_free,
      })
    }
  }
  return rows
})

const copiedKey = ref('')
let copiedTimer: ReturnType<typeof setTimeout> | undefined
async function copyLine(line: ServerLine) {
  try {
    await navigator.clipboard.writeText(line.url)
    copiedKey.value = line.key
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copiedKey.value = '' }, 1600)
    toast.success('线路地址已复制，去播放器里添加服务器时粘贴它')
  } catch {
    toast.error('复制失败，请长按地址手动复制')
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
watch(
  () => props.open,
  (open) => {
    if (open) document.addEventListener('keydown', onKeydown)
    else document.removeEventListener('keydown', onKeydown)
  },
)
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="sls-overlay"
      :class="{ 'sls--dark': dark }"
      @click.self="emit('close')"
    >
      <div class="sls-card" role="dialog" aria-modal="true" aria-label="Emby 服务线路">
        <div class="sls-head">
          <h3 class="sls-title">Emby 服务线路</h3>
          <button type="button" class="sls-close" aria-label="关闭" @click="emit('close')">
            <X :size="18" aria-hidden="true" />
          </button>
        </div>

        <p class="sls-group">播放线路</p>
        <div v-if="lines.length" class="sls-lines">
          <div v-for="line in lines" :key="line.key" class="sls-line">
            <span class="sls-line__main">
              <span class="sls-line__tags">
                <span
                  class="sls-badge"
                  :class="line.primary ? 'sls-badge--primary' : 'sls-badge--backup'"
                >{{ line.primary ? '主线路' : '备用线路' }}</span>
                <span v-if="line.name" class="sls-line__name">{{ line.name }}</span>
                <span v-if="line.free" class="sls-line__free">公益服</span>
              </span>
              <span class="sls-line__url">{{ line.url }}</span>
            </span>
            <button type="button" class="sls-copy" @click="copyLine(line)">
              <Check v-if="copiedKey === line.key" :size="14" aria-hidden="true" />
              <Copy v-else :size="14" aria-hidden="true" />
              {{ copiedKey === line.key ? '已复制' : '复制' }}
            </button>
          </div>
        </div>
        <p v-else class="sls-empty">
          暂时没有可用的服务器地址，开通会员后回到这里查看。
        </p>

        <p class="sls-group">客户端</p>
        <div class="sls-links">
          <RouterLink class="sls-link" to="/game/install" @click="emit('close')">
            <span class="sls-link__main">
              <span class="sls-link__name">支持的客户端</span>
              <span class="sls-link__sub">手机、电视、电脑客户端下载</span>
            </span>
            <ChevronRight :size="18" class="sls-link__chev" aria-hidden="true" />
          </RouterLink>
          <RouterLink class="sls-link" to="/game/install" @click="emit('close')">
            <span class="sls-link__main">
              <span class="sls-link__name">配置教程</span>
              <span class="sls-link__sub">下载、添加服务器、登录，分步说明</span>
            </span>
            <ChevronRight :size="18" class="sls-link__chev" aria-hidden="true" />
          </RouterLink>
        </div>

        <p class="sls-foot">
          在播放器里添加服务器时填上面的地址，用 https 连接（端口 443，可留空）。
          哪条快用哪条拿不准就先
          <RouterLink class="sls-foot__link" to="/game/speedtest" @click="emit('close')">线路测速</RouterLink>
          看一眼。
        </p>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 模具数值随全站弹窗口径（说明书 2.8：遮罩黑 40%＋模糊 6px、卡 max 400、
   圆角 24、标题 24、右上 × 关闭）；--sls-* 主题变量定义在同目录
   server-lines-sheet.css（契约只认被引入的 .css），本块只引用。 */
.sls-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.sls-card {
  position: relative;
  width: min(400px, calc(100vw - 32px));
  max-height: calc(100dvh - 48px);
  overflow-y: auto;
  box-sizing: border-box;
  background: var(--sls-card);
  color: var(--sls-text);
  border-radius: var(--uhd-radius-modal);
  box-shadow: var(--uhd-shadow-modal);
  padding: 28px;
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
}
.sls-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.sls-title {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
  color: var(--sls-text);
}
.sls-close {
  flex: none;
  width: 30px;
  height: 30px;
  min-height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--sls-sub);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.sls-close:hover {
  background: var(--sls-link-hover);
}
.sls-group {
  margin: 22px 0 4px;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  color: var(--sls-sub);
}
.sls-lines {
  display: flex;
  flex-direction: column;
}
.sls-line {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--sls-divider);
}
.sls-line:last-child {
  border-bottom: none;
}
.sls-line__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.sls-line__tags {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
/* 主线路徽标：招牌口径（彩虹边框＋粉彩/深玻璃底，与概览主钮同一套） */
.sls-badge {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--uhd-fs-micro);
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
}
.sls-badge--primary {
  border: 1.5px solid transparent;
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
}
.sls--dark .sls-badge--primary {
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  color: #ffffff;
}
.sls-badge--backup {
  border: 1px solid var(--sls-badge-border);
  color: var(--sls-badge-text);
}
.sls-line__name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--sls-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sls-line__free {
  flex: none;
  font-size: var(--uhd-fs-micro);
  color: var(--sls-sub);
}
.sls-line__url {
  font-family: var(--uhd-font-mono);
  font-size: var(--uhd-fs-aux);
  color: var(--sls-sub);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sls-copy {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  border: none;
  border-radius: 999px;
  background: var(--sls-copy-bg);
  color: var(--sls-copy-text);
  font-family: inherit;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.sls-copy:active {
  opacity: 0.8;
}
.sls-empty {
  margin: 0;
  padding: 12px 0 4px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--sls-sub);
}
.sls-links {
  display: flex;
  flex-direction: column;
}
.sls-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--sls-divider);
  color: var(--sls-text);
  text-decoration: none;
}
.sls-link:last-child {
  border-bottom: none;
}
.sls-link__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sls-link__name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--sls-text);
}
.sls-link__sub {
  font-size: var(--uhd-fs-aux);
  color: var(--sls-sub);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sls-link__chev {
  flex: none;
  color: var(--sls-sub);
}
.sls-foot {
  margin: 20px 0 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.7;
  color: var(--sls-sub);
}
.sls-foot__link {
  color: var(--sls-text);
  font-weight: var(--uhd-fw-semibold);
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
