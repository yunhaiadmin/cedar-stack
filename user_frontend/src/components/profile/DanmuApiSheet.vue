<script setup lang="ts">
/**
 * DanmuApiSheet —— 弹幕 API 专职弹层（2026-11-18 友站形态 1:1 对齐单）
 *
 * 友站概览点「弹幕 API」行是就地专职弹层（逐帧研究报告
 * reports/danmu-api-frame-study-20261118.md），我方原先跳设置页
 * #api-keys 的通用平台 Key 面板，形态完全不同构，本组件即该弹层。
 * 设置页 ApiKeysPanel 是平台 Key 功能，不动。
 *
 * 状态机四态（照报告 §1）：打开先「查询中」（spinner＋正在查询弹幕
 * Token...）→ 无 Token 为「未生成」（规则文案＋彩虹主钮生成）→ 生成中
 * （主钮原位 spinner＋正在生成...）→「已生成」（到期时刻＋不自动续期
 * ＋删除钮）；已生成后默认不选线路，未选不出现地址区；选线路后按
 * 契约取地址（GET /api/user/danmu/address?line=<id>）展示胶囊地址框。
 * 内容流尾部固定门槛文案（报告 §1.7：它是内容流最后一节）。
 *
 * 推导口径（报告标注量不到、本单按最合理行为定，PR 回报已列明）：
 * - 删除：点「删除」→ 弹层内二次确认行 → 确认后回未生成态；
 * - 深色态 radio 选中环跟随文字色（浅色为黑环，照报告帧）。
 *
 * 成功/失败反馈用弹层内顶部胶囊 toast（层级在卡片之上，照报告 §1.9）：
 * 全站 toast 容器 z-index 120 低于弹层 1200，在弹层里会被遮住，故本
 * 组件自带一条；文案仍走 toUserMessage 统一口径。
 *
 * 主题跟随概览页：dark 由父页按其主题解析值传入（与 ServerLinesSheet
 * 同口径），调色变量定义在同目录 danmu-api-sheet.css。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { Check, Copy, KeyRound, LoaderCircle, Trash2, X } from 'lucide-vue-next'
import UhdButton from '@/components/uhd/UhdButton.vue'
import { toUserMessage } from '@/utils/toUserMessage'
import {
  createDanmuToken,
  deleteDanmuToken,
  fetchDanmuAddress,
  fetchDanmuLines,
  fetchDanmuTokenStatus,
  type DanmuLine,
} from '@/api/danmu'
import './danmu-api-sheet.css'
import '../../styles/modal-glass.css'

const props = defineProps<{
  open: boolean
  dark: boolean
}>()

const emit = defineEmits<{ close: [] }>()

/** 弹层内顶部胶囊 toast（成功约 2 秒，照报告 §1.5/§1.8） */
const sheetToast = ref<{ kind: 'success' | 'error'; text: string } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | undefined
function showToast(kind: 'success' | 'error', text: string) {
  sheetToast.value = { kind, text }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(
    () => {
      sheetToast.value = null
    },
    kind === 'success' ? 2000 : 2600,
  )
}

type Phase = 'loading' | 'none' | 'creating' | 'ready'
const phase = ref<Phase>('loading')
const expiresAt = ref<string | null>(null)
const lines = ref<DanmuLine[]>([])
const selectedLineId = ref<string | null>(null)
const address = ref('')
const addressLoading = ref(false)
const confirmingDelete = ref(false)
const deleting = ref(false)

/** 到期文案：YYYY-MM-DD HH:mm（本地时区，精确到分钟，照报告 §1.5） */
function formatExpiry(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  phase.value = 'loading'
  expiresAt.value = null
  selectedLineId.value = null
  address.value = ''
  confirmingDelete.value = false
  const [statusRes, linesRes] = await Promise.allSettled([
    fetchDanmuTokenStatus(),
    fetchDanmuLines(),
  ])
  if (linesRes.status === 'fulfilled') lines.value = linesRes.value
  if (statusRes.status === 'fulfilled') {
    if (statusRes.value.has_token) {
      expiresAt.value = statusRes.value.expires_at
      phase.value = 'ready'
    } else {
      phase.value = 'none'
    }
  } else {
    phase.value = 'none'
    showToast('error', toUserMessage(statusRes.reason, 'loadFailed'))
  }
}

async function onCreate() {
  if (phase.value !== 'none') return
  phase.value = 'creating'
  try {
    const status = await createDanmuToken()
    expiresAt.value = status.expires_at
    phase.value = 'ready'
    showToast('success', '弹幕 Token 已生成')
  } catch (e) {
    phase.value = 'none'
    showToast('error', toUserMessage(e, 'saveFailed'))
  }
}

async function selectLine(line: DanmuLine) {
  if (selectedLineId.value === line.id && address.value) return
  selectedLineId.value = line.id
  address.value = ''
  addressLoading.value = true
  try {
    address.value = await fetchDanmuAddress(line.id)
  } catch (e) {
    showToast('error', toUserMessage(e, 'loadFailed'))
  } finally {
    addressLoading.value = false
  }
}

async function onCopy() {
  if (!address.value) return
  try {
    await navigator.clipboard.writeText(address.value)
    showToast('success', '弹幕 API 地址已复制')
  } catch {
    showToast('error', '复制失败，请长按地址手动复制')
  }
}

async function onConfirmDelete() {
  if (deleting.value) return
  deleting.value = true
  try {
    await deleteDanmuToken()
    confirmingDelete.value = false
    expiresAt.value = null
    selectedLineId.value = null
    address.value = ''
    phase.value = 'none'
    showToast('success', '弹幕 Token 已删除')
  } catch (e) {
    showToast('error', toUserMessage(e, 'removeFailed'))
  } finally {
    deleting.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
watch(
  () => props.open,
  (open) => {
    if (open) {
      document.addEventListener('keydown', onKeydown)
      void load()
    } else {
      document.removeEventListener('keydown', onKeydown)
    }
  },
)
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  if (toastTimer) clearTimeout(toastTimer)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="dms-overlay"
      :class="{ 'dms--dark': dark }"
      @click.self="emit('close')"
    >
      <div class="dms-card" role="dialog" aria-modal="true" aria-label="弹幕 API">
        <div class="dms-head">
          <h3 class="dms-title">弹幕 API</h3>
          <button type="button" class="dms-close" aria-label="关闭" @click="emit('close')">
            <X :size="18" aria-hidden="true" />
          </button>
        </div>

        <p class="dms-intro">生成专属 Token，选一条线路，把地址填进播放器的弹幕 API 设置。</p>

        <!-- 查询中（报告 §1.2）：标题区先行，加载行随后 -->
        <p v-if="phase === 'loading'" class="dms-loading">
          <LoaderCircle :size="17" class="dms-spin" aria-hidden="true" />
          正在查询弹幕 Token...
        </p>

        <template v-else>
          <!-- Token 区 -->
          <p class="dms-group">Token</p>
          <div class="dms-divider" />

          <!-- 未生成 / 生成中（报告 §1.3 / §1.4） -->
          <template v-if="phase === 'none' || phase === 'creating'">
            <p class="dms-status">尚未生成</p>
            <p class="dms-note">每个账号可持有一个，有效期一年，到期后需重新生成。</p>
            <div
              class="dms-primary-wrap"
              :class="{ 'dms-primary-wrap--busy': phase === 'creating' }"
            >
              <UhdButton
                variant="primary"
                size="mid"
                block
                :dark="dark"
                @click="onCreate"
              >
                <LoaderCircle
                  v-if="phase === 'creating'"
                  :size="17"
                  class="dms-spin"
                  aria-hidden="true"
                />
                <KeyRound v-else :size="17" aria-hidden="true" />
                {{ phase === 'creating' ? '正在生成...' : '生成弹幕 Token' }}
              </UhdButton>
            </div>
          </template>

          <!-- 已生成（报告 §1.5） -->
          <template v-else-if="phase === 'ready'">
            <!-- 删除二次确认（推导口径：弹层内确认行） -->
            <div v-if="confirmingDelete" class="dms-confirm">
              <p class="dms-confirm__text">删除后，现有弹幕地址将立即失效。确定删除？</p>
              <div class="dms-confirm__actions">
                <button
                  type="button"
                  class="dms-confirm__btn"
                  :disabled="deleting"
                  @click="confirmingDelete = false"
                >
                  取消
                </button>
                <button
                  type="button"
                  class="dms-confirm__btn dms-confirm__btn--danger"
                  :disabled="deleting"
                  @click="onConfirmDelete"
                >
                  {{ deleting ? '正在删除...' : '确认删除' }}
                </button>
              </div>
            </div>
            <div v-else class="dms-tokenblock">
              <div class="dms-tokenrow">
                <span class="dms-status">已生成</span>
                <button type="button" class="dms-delete" @click="confirmingDelete = true">
                  <Trash2 :size="16" aria-hidden="true" />
                  删除
                </button>
              </div>
              <p class="dms-note">{{ formatExpiry(expiresAt) }} 到期 · 不自动续期</p>
            </div>

            <!-- 线路区（报告 §1.5/§1.6）：整行可点单选，默认不选 -->
            <p class="dms-group">线路</p>
            <div class="dms-divider" />
            <div v-if="lines.length" class="dms-lines" role="radiogroup" aria-label="弹幕线路">
              <button
                v-for="line in lines"
                :key="line.id"
                type="button"
                class="dms-line"
                role="radio"
                :aria-checked="selectedLineId === line.id"
                @click="selectLine(line)"
              >
                <span
                  class="dms-radio"
                  :class="{ 'dms-radio--on': selectedLineId === line.id }"
                  aria-hidden="true"
                />
                <span class="dms-line__main">
                  <span class="dms-line__name">{{ line.name }}</span>
                  <span v-if="line.note" class="dms-line__sub">{{ line.note }}</span>
                </span>
              </button>
            </div>
            <p v-else class="dms-note dms-note--block">暂无可用线路。</p>

            <!-- API 地址区（报告 §1.6）：选了线路才出现 -->
            <template v-if="selectedLineId !== null">
              <p class="dms-group">API 地址</p>
              <div class="dms-divider" />
              <div class="dms-addr" :title="address">
                <LoaderCircle
                  v-if="addressLoading"
                  :size="15"
                  class="dms-spin"
                  aria-hidden="true"
                />
                <span class="dms-addr__text">{{ address }}</span>
              </div>
              <p class="dms-note dms-note--block">
                地址包含你的专属 Token，请勿分享；切换线路后需重新复制。
              </p>
              <div class="dms-primary-wrap">
                <UhdButton
                  variant="primary"
                  size="mid"
                  block
                  :dark="dark"
                  :disabled="!address"
                  @click="onCopy"
                >
                  <Copy :size="17" aria-hidden="true" />
                  复制弹幕 API 地址
                </UhdButton>
              </div>
            </template>

            <!-- 门槛文案：内容流最后一节（报告 §1.7） -->
            <p class="dms-foot">使用时需有有效套餐及剩余流量，弹幕请求本身不扣流量。</p>
          </template>
        </template>
      </div>

      <!-- 顶部胶囊 toast：层级在卡片之上（报告 §1.9），绿勾圆圈＋文案 -->
      <div
        v-if="sheetToast"
        class="dms-toast"
        :class="{ 'dms-toast--error': sheetToast.kind === 'error' }"
        role="status"
      >
        <span class="dms-toast__icon" aria-hidden="true">
          <Check v-if="sheetToast.kind === 'success'" :size="12" :stroke-width="3.5" />
          <X v-else :size="12" :stroke-width="3.5" />
        </span>
        <span class="dms-toast__text">{{ sheetToast.text }}</span>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 模具数值随全站弹窗口径（说明书 2.8：遮罩＋卡 max 400、圆角 24）；
   弹层几何照报告量测：宽约 92% 屏宽、内容封顶约 74% 屏高内滚动。
   --dms-* 主题变量定义在同目录 danmu-api-sheet.css，本块只引用。 */
.dms-overlay {
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
.dms-card {
  position: relative;
  width: min(400px, calc(100vw - 32px));
  max-height: 74dvh;
  overflow-y: auto;
  box-sizing: border-box;
  /* 实色面（2026-11-18 用户令：弹窗不用玻璃）：浅色白面、深色实色深面，
     边框/阴影走普通卡片口径，与团队购买 .tm-sheet 同改法 */
  background: var(--uhd-l-surface);
  border: 1px solid var(--uhd-l-border);
  color: var(--dms-text);
  border-radius: var(--uhd-radius-modal);
  box-shadow: var(--uhd-shadow-card-l);
  padding: 28px;
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
}
.dms--dark .dms-card {
  background: var(--uhd-d-surface);
  border-color: var(--uhd-d-border);
  box-shadow: var(--uhd-shadow-card-d);
}
.dms-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.dms-title {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
  color: var(--dms-text);
}
.dms-close {
  flex: none;
  width: 30px;
  height: 30px;
  min-height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dms-sub);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.dms-close:hover {
  background: var(--dms-close-hover);
}
.dms-intro {
  margin: 10px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--dms-sub);
}
.dms-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 22px 0 4px;
  font-size: var(--uhd-fs-caption);
  color: var(--dms-sub);
}
.dms-spin {
  flex: none;
  animation: dms-spin 0.9s linear infinite;
}
@keyframes dms-spin {
  to {
    transform: rotate(360deg);
  }
}
.dms-group {
  margin: 22px 0 0;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  color: var(--dms-sub);
}
.dms-divider {
  margin-top: 8px;
  border-top: 1px solid var(--dms-divider);
}
.dms-status {
  margin: 14px 0 0;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  color: var(--dms-text);
}
.dms-note {
  margin: 6px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--dms-sub);
}
.dms-note--block {
  margin-top: 10px;
}
.dms-primary-wrap {
  margin-top: 18px;
}
/* 生成中：主钮原位保留彩虹、整体变淡（报告 §1.4），不用禁用灰样式 */
.dms-primary-wrap--busy {
  opacity: 0.55;
  pointer-events: none;
}
.dms-tokenblock {
  margin-top: 14px;
}
.dms-tokenrow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.dms-tokenrow .dms-status {
  margin: 0;
}
.dms-delete {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  min-height: 30px;
  padding: 0 6px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--dms-sub);
  font-family: inherit;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.dms-delete:hover {
  color: var(--dms-danger);
}
.dms-confirm {
  margin-top: 14px;
}
.dms-confirm__text {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--dms-text);
}
.dms-confirm__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.dms-confirm__btn {
  height: 34px;
  min-height: 34px;
  padding: 0 16px;
  border: 1px solid var(--dms-addr-border);
  border-radius: 999px;
  background: transparent;
  color: var(--dms-text);
  font-family: inherit;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.dms-confirm__btn--danger {
  border-color: transparent;
  color: var(--dms-danger);
}
.dms-confirm__btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.dms-lines {
  display: flex;
  flex-direction: column;
}
.dms-line {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 0;
  border: none;
  border-bottom: 1px solid var(--dms-divider);
  background: transparent;
  color: var(--dms-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.dms-line:last-child {
  border-bottom: none;
}
/* radio：未选细空心圆；选中粗环＋中心实点（报告 §1.6，环色随文字色） */
.dms-radio {
  flex: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1.5px solid var(--dms-radio-idle);
  box-sizing: border-box;
  position: relative;
  transition: border-color var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.dms-radio--on {
  border: 2.5px solid var(--dms-radio-ring);
}
.dms-radio--on::after {
  content: '';
  position: absolute;
  inset: 0;
  margin: auto;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--dms-radio-ring);
}
.dms-line__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.dms-line__name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--dms-text);
}
.dms-line__sub {
  font-size: var(--uhd-fs-aux);
  color: var(--dms-sub);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dms-addr {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  height: 44px;
  padding: 0 16px;
  border: 1px solid var(--dms-addr-border);
  border-radius: 999px;
  color: var(--dms-text);
}
.dms-addr__text {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--uhd-font-mono);
  font-size: var(--uhd-fs-aux);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dms-foot {
  margin: 20px 0 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.7;
  color: var(--dms-sub);
}
/* 顶部胶囊 toast（报告 §1.5 量测：x 约 3.8%–96%、贴近状态栏下方） */
.dms-toast {
  position: fixed;
  top: calc(10px + env(safe-area-inset-top, 0px));
  left: 16px;
  right: 16px;
  max-width: 432px;
  margin: 0 auto;
  z-index: 1300;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 18px;
  border-radius: 999px;
  background: var(--dms-toast-bg);
  color: var(--dms-toast-text);
  box-shadow: var(--dms-toast-shadow);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  animation: dms-toast-in 0.3s var(--uhd-ease-standard);
  pointer-events: none;
}
@keyframes dms-toast-in {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
}
.dms-toast__icon {
  flex: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #34c759;
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.dms-toast--error .dms-toast__icon {
  background: #e5484d;
}
.dms-toast__text {
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  line-height: 1.4;
}
@media (prefers-reduced-motion: reduce) {
  .dms-toast {
    animation: none;
  }
  .dms-spin {
    animation-duration: 2.4s;
  }
}
</style>
