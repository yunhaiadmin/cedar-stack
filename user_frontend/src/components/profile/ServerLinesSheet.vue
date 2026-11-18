<script setup lang="ts">
/**
 * ServerLinesSheet —— Emby 线路弹层（2026-11-13 形态对齐单）
 *
 * 友站概览点「服务器地址」是居中弹层（标题「Emby 线路」＋线路分组
 * ＋主/备线路行＋客户端区＋底部提示），我方原先点开跳播放账号
 * 整页；用户 2026-11-13 23:15 亲点改弹层，本组件即该弹层。
 * （播放账号页已随 2026-11-18 删除单整页删除。）
 *
 * 线路数据只用账号卡现行口径（与线路测速页同源）：两层形态——
 * 大类 = realms[]（服），每个大类下挂多条线路（realms[].lines，
 * 主线路在前）；按大类分组渲染（组标题 = 大类名），组内主、副
 * 依次。没下发线路的大类用它的 base_url 补一条主线路；realms
 * 为空时回退顶层 base_url。够格看到几条渲染几条：只有一条就只
 * 显示一条，不假造备用线路。主/副徽标按后端下发的 is_primary。
 * 客户端区只指站内已有去处（安装应用页含客户端下载与分步教程），
 * 不编造外链；底部提示带线路测速链接（现有测速页）。
 *
 * 文案对齐（2026-11-18，差异清单 G-21/22/23/27/28）：标题、说明
 * 两行、分组头、客户端副文与底部说明均照友站原文；「线路」组头
 * 右侧刷新图标经 refresh 事件请父页重拉账号卡，本组件不自拉数据。
 *
 * 主题跟随概览页：dark 由父页按其主题解析值传入，弹层调色与概览页
 * 本地令牌同值（深 #17181c 卡 / 浅白卡），不随全局用户中心浅色面走。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowUpRight, Check, RefreshCw, X } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import './server-lines-sheet.css'
import '../../styles/modal-glass.css'

/**
 * 弹层只声明真正用到的账号卡字段子集，不直接收整个 AccountCard：
 * 账号卡经 ref 解包后，其纯 null 类型字段（emby_password）在 Vue 的
 * UnwrapRef 映射下会降级成 unknown，整卡传 prop 与原接口对不上
 * （代码库既有怪癖，此前没人整卡传过 prop 才未暴露）；子集字段均为
 * string/number/boolean，不受该降级影响。
 */
export interface ServerLinesLine {
  id: number
  name: string
  url: string
  is_primary: boolean
  /** 线路流量配额已用尽：显示「暂停服务」、地址不可复制 */
  quota_exhausted?: boolean
}

export interface ServerLinesRealm {
  id: number
  name: string
  base_url: string
  is_default: boolean
  is_free?: boolean
  /** 这个大类下的线路（主线路在前）；缺省时用 base_url 补一条 */
  lines?: ServerLinesLine[]
  /** 主线路的配额用尽标记（补行时用） */
  quota_exhausted?: boolean
}

export interface ServerLinesCard {
  base_url: string
  server_name: string
  realm_name?: string | null
  is_free?: boolean
  quota_exhausted?: boolean
  realms?: ServerLinesRealm[]
}

const props = defineProps<{
  open: boolean
  card: ServerLinesCard | null
  dark: boolean
}>()

const emit = defineEmits<{ close: []; refresh: [] }>()

const toast = useToast()

interface ServerLine {
  key: string
  name: string
  url: string
  primary: boolean
  exhausted: boolean
}

interface ServerLineGroup {
  key: string
  /** 组标题 = 大类（服）名 */
  title: string
  free: boolean
  lines: ServerLine[]
}

/** 线路分组：按大类（服）分组、组内主副依次，默认服在前、按地址去重 */
const groups = computed<ServerLineGroup[]>(() => {
  const card = props.card
  if (!card) return []
  const out: ServerLineGroup[] = []
  const seen = new Set<string>()
  const realms = [...(card.realms ?? [])].sort(
    (a, b) => Number(b.is_default) - Number(a.is_default),
  )
  for (const r of realms) {
    const rows: ServerLine[] = []
    const realmLines = r.lines ?? []
    if (realmLines.length) {
      for (const l of realmLines) {
        const url = (l.url ?? '').trim()
        if (!url || seen.has(url)) continue
        seen.add(url)
        const primary = !!l.is_primary
        const rawName = (l.name || '').trim()
        rows.push({
          key: `line-${l.id}`,
          // 默认名与主/副徽标同文时不重复展示（徽标已表达主副）
          name: rawName === (primary ? '主线路' : '副线路') ? '' : rawName,
          url,
          primary,
          exhausted: !!l.quota_exhausted,
        })
      }
    } else {
      const url = (r.base_url ?? '').trim()
      if (url && !seen.has(url)) {
        seen.add(url)
        rows.push({
          key: `realm-${r.id}`,
          name: '',
          url,
          primary: true,
          exhausted: !!r.quota_exhausted,
        })
      }
    }
    if (rows.length) {
      out.push({ key: `realm-${r.id}`, title: r.name || '', free: !!r.is_free, lines: rows })
    }
  }
  if (!out.length) {
    const top = (card.base_url ?? '').trim()
    if (top) {
      out.push({
        key: 'top',
        title: card.realm_name || card.server_name || '',
        free: !!card.is_free,
        lines: [{
          key: 'top',
          name: '',
          url: top,
          primary: true,
          exhausted: !!card.quota_exhausted,
        }],
      })
    }
  }
  return out
})

const copiedKey = ref('')
let copiedTimer: ReturnType<typeof setTimeout> | undefined
async function copyLine(line: ServerLine) {
  if (line.exhausted) return // 用尽线路不可选（钮已禁用，这里兜底）
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

/** 组头刷新：只发事件请父页重拉账号卡；新卡到达（prop 变更）即停转，
 *  3 秒兜底防父页重拉失败时图标永转。 */
const refreshing = ref(false)
let refreshTimer: ReturnType<typeof setTimeout> | undefined
function onRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  emit('refresh')
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => { refreshing.value = false }, 3000)
}
watch(
  () => props.card,
  () => {
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshing.value = false
  },
)

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
  if (refreshTimer) clearTimeout(refreshTimer)
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
      <div class="sls-card" role="dialog" aria-modal="true" aria-label="Emby 线路">
        <div class="sls-head">
          <h3 class="sls-title">Emby 线路</h3>
          <button type="button" class="sls-close" aria-label="关闭" @click="emit('close')">
            <X :size="18" aria-hidden="true" />
          </button>
        </div>

        <p class="sls-intro">
          在兼容 Emby 的第三方客户端里添加服务器，<br />
          填入线路地址即可。
        </p>

        <div class="sls-grouprow">
          <p class="sls-group sls-group--inrow">线路</p>
          <button
            type="button"
            class="sls-refresh"
            :class="{ 'sls-refresh--busy': refreshing }"
            aria-label="刷新线路"
            @click="onRefresh"
          >
            <RefreshCw :size="16" aria-hidden="true" />
          </button>
        </div>
        <div v-if="groups.length" class="sls-groups">
          <div v-for="g in groups" :key="g.key" class="sls-group-block">
            <p v-if="g.title" class="sls-group-name">
              {{ g.title }}
              <span v-if="g.free" class="sls-line__free">公益服</span>
            </p>
            <div class="sls-lines">
              <div v-for="line in g.lines" :key="line.key" class="sls-line" :class="{ 'sls-line--off': line.exhausted }">
                <span class="sls-line__main">
                  <span class="sls-line__tags">
                    <span
                      class="sls-badge"
                      :class="line.primary ? 'sls-badge--primary' : 'sls-badge--backup'"
                    >{{ line.primary ? '主线路' : '副线路' }}</span>
                    <span v-if="line.name" class="sls-line__name">{{ line.name }}</span>
                    <!-- 线路流量配额用尽（2026-11-19）：与弹幕线路暂停同口径，
                         灰态＋「暂停服务」，地址不许复制选用 -->
                    <span v-if="line.exhausted" class="sls-line__paused">暂停服务</span>
                  </span>
                  <span class="sls-line__url">{{ line.url }}</span>
                </span>
                <!-- 复制钮照友站 f0004：纯文字深色玻璃胶囊（无常驻图标）；
                     已复制态才换 ✓ 图标＋「已复制」（toast 同步提示） -->
                <button type="button" class="sls-copy" :disabled="line.exhausted" @click="copyLine(line)">
                  <Check v-if="copiedKey === line.key" :size="13" aria-hidden="true" />
                  {{ copiedKey === line.key ? '已复制' : '复制' }}
                </button>
              </div>
            </div>
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
              <span class="sls-link__sub">各平台可用的第三方客户端</span>
            </span>
            <ArrowUpRight :size="18" class="sls-link__chev" aria-hidden="true" />
          </RouterLink>
          <RouterLink class="sls-link" to="/game/install" @click="emit('close')">
            <span class="sls-link__main">
              <span class="sls-link__name">配置教程</span>
              <span class="sls-link__sub">添加服务器与登录步骤</span>
            </span>
            <ArrowUpRight :size="18" class="sls-link__chev" aria-hidden="true" />
          </RouterLink>
        </div>

        <p class="sls-foot">
          没有特别标注端口的线路，都使用 HTTPS 443 端口。<br />
          不确定选哪条，可以先去<RouterLink class="sls-foot__link" to="/game/speedtest" @click="emit('close')">线路测速</RouterLink>。
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
  background: var(--modal-scrim-dark);
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
  /* 面板实色（2026-11-18 用户亲令；2026-11-15 曾玻璃化）：底用
     实色面；--modal-glass-bg/edge/shadow 在本弹层作用域由
     server-lines-sheet.css 按双态钉值（口径同 styles/modal-glass.css），
     去 backdrop 与内高光；尺寸/圆角/内边距不动。 */
  background: var(--modal-glass-bg);
  border: 1px solid var(--modal-glass-edge);
  color: var(--sls-text);
  border-radius: var(--uhd-radius-modal);
  box-shadow: var(--modal-glass-shadow);
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
.sls-intro {
  margin: 10px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--sls-sub);
}
/* 「线路」组头行：标签在左、刷新图标在右，间距与 .sls-group 同口径 */
.sls-grouprow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 22px 0 4px;
}
.sls-group--inrow {
  margin: 0;
}
.sls-refresh {
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
.sls-refresh:hover {
  background: var(--sls-link-hover);
}
.sls-refresh--busy svg {
  animation: sls-spin 0.9s linear infinite;
}
@keyframes sls-spin {
  to {
    transform: rotate(360deg);
  }
}
.sls-groups {
  display: flex;
  flex-direction: column;
}
.sls-group-name {
  margin: 14px 0 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-bold);
  color: var(--sls-text);
}
.sls-group-block:first-child .sls-group-name {
  margin-top: 2px;
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
  position: relative;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d));
  color: #ffffff;
}
/* 彩虹环切（texture-tokens-3）：彩虹只走 1.5px 细环（::before mask 环切），
   徽章面只留玻璃层；浅色基规则不动。 */
.sls--dark .sls-badge--primary::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--uhd-rainbow-border-w);
  background: var(--uhd-rainbow);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
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
/* 配额用尽：整行灰态、暂停服务标记、复制钮禁用 */
.sls-line--off .sls-line__main {
  opacity: 0.55;
}
.sls-line__paused {
  flex: none;
  font-size: var(--uhd-fs-micro);
  font-weight: var(--uhd-fw-semibold);
  color: var(--sls-sub);
}
.sls-copy:disabled {
  opacity: 0.45;
  cursor: not-allowed;
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
  /* 2026-11-18 自查 O-5：友站 f0004 原帧逐行实测复制钮 71×41 帧px，
     ÷1.3846（390pt 机 3x 帧）≈ 51×30 CSS px；深色玻璃胶囊（半透底＋
     细边＋模糊）取代旧黑/白实心钮；min-height 中和 mobile.css 全局
     button{min-height:44px}，只改 height 会被顶回 44px */
  height: 30px;
  min-height: 30px;
  padding: 0 12px;
  border: 1px solid var(--sls-copy-border);
  border-radius: 999px;
  background: var(--sls-copy-bg);
  backdrop-filter: blur(12px) saturate(150%);
  -webkit-backdrop-filter: blur(12px) saturate(150%);
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
