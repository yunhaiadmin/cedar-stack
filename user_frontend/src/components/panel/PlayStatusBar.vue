<!-- 起播状态条：点播前先看清这部片对当前用户是什么状态。
     数据来自只读口 GET /api/play/items/{id}/status（src/api/playStatus.ts）。
     - ready：已就绪（本人盘 / 公共池 / 直读），一行说清；
     - preparing：正在准备——排队/搬运中，有可靠进度才显示百分比，
       每 8 秒自动刷新，页面切到后台或组件卸载即停；无字节进度时
       给时间型预计（预计还需约 X 秒，已等 Y 秒），slow 时改慢文案，
       绝不画假进度条；
     - failed：人话原因 + 一句能照着做的建议（按 route 分流：池路
       走池版文案，space 的本人路文案只许 route=own 时出现）；
     - idle / 加载失败：什么都不渲染（不打扰，首播说明在 sheet 里已有）。
     preparing→ready 翻转时发 ready 事件（首载直接 ready 不发），
     供选播器弹层自动开播；autoOpening 为真时 ready 行显示
     「已备好，正在为你打开…」。
     本组件只展示，不触发任何转存/播放动作。 -->
<template>
  <div v-if="view" class="psb" :class="`psb--${view.state}`" role="status">
    <span class="psb-dot" aria-hidden="true"></span>
    <div class="psb-body">
      <div class="psb-title">{{ view.title }}</div>
      <div v-if="view.sub" class="psb-sub">{{ view.sub }}</div>
      <div v-if="view.eta" class="psb-eta">{{ view.eta }}</div>
      <div
        v-if="view.state === 'preparing' && view.pct !== null"
        class="psb-track"
        aria-hidden="true"
      >
        <div class="psb-fill" :style="{ width: `${view.pct}%` }"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  failCopyFor,
  fetchPlayStatus,
  type PlayState,
  type PlayStatus,
} from '@/api/playStatus'

const props = defineProps<{
  itemId: string | number | null
  /** 弹层正在自动开播（600ms 窗口内）：ready 行改显示已备好提示 */
  autoOpening?: boolean
}>()

const emit = defineEmits<{ ready: [] }>()

const POLL_MS = 8000

const status = ref<PlayStatus | null>(null)
/** 上一次轮询到的 state：只认 preparing→ready 的翻转发 ready，
 *  首载直接 ready 不发。 */
const prevState = ref<PlayState | null>(null)
let timer = 0
let seq = 0

interface StatusView {
  state: 'ready' | 'preparing' | 'failed'
  title: string
  sub: string
  pct: number | null
  eta: string | null
}

const view = computed<StatusView | null>(() => {
  const s = status.value
  if (!s) return null
  if (s.state === 'ready') {
    const sub =
      s.source === 'own'
        ? '副本就在你的盘里，打开播放器直接看'
        : s.source === 'pool'
          ? '公共池里有现成副本，打开播放器直接看'
          : '打开播放器直接看'
    return {
      state: 'ready',
      title: props.autoOpening ? '已备好，正在为你打开…' : '已就绪，可以直接看',
      sub,
      pct: null,
      eta: null,
    }
  }
  if (s.state === 'preparing') {
    // 副行按 route 分流：池路是往公共池里备，本人路按阶段说。
    const sub =
      s.route === 'pool'
        ? '正在往公共池里备这部片'
        : s.stage === 'queued'
          ? '已在排队，轮到就会开始搬运'
          : s.progress_pct !== null
            ? `正在搬运到你的盘里，已完成 ${s.progress_pct}%`
            : '正在搬运到你的盘里'
    // ETA 行：每次轮询结果直接渲染，不在前端倒计时递减；eta 为
    // null（已超 p90）时不显示倒计时，改 slow 文案。
    let eta: string | null = null
    if (s.slow) {
      eta = '比平时慢一点，还在努力备，请再等一会儿'
    } else if (s.eta_seconds !== null && s.eta_seconds !== undefined) {
      eta =
        s.elapsed_seconds !== null && s.elapsed_seconds !== undefined
          ? `预计还需约 ${s.eta_seconds} 秒（已等 ${s.elapsed_seconds} 秒）`
          : `预计还需约 ${s.eta_seconds} 秒`
    }
    return {
      state: 'preparing',
      title: '正在为你准备这部片',
      sub,
      pct: s.progress_pct,
      eta,
    }
  }
  if (s.state === 'failed') {
    const copy = failCopyFor(s.reason, s.route)
    return { state: 'failed', title: copy.title, sub: copy.sub, pct: null, eta: null }
  }
  return null
})

function stopPoll() {
  if (timer) {
    window.clearInterval(timer)
    timer = 0
  }
}

function startPoll() {
  stopPoll()
  timer = window.setInterval(() => {
    if (document.hidden) return
    void load()
  }, POLL_MS)
}

async function load() {
  const id = props.itemId
  if (id === null || id === undefined || id === '') return
  const my = ++seq
  try {
    const s = await fetchPlayStatus(id)
    if (my !== seq) return
    const prev = prevState.value
    prevState.value = s.state
    status.value = s
    // 只认 preparing→ready 的翻转：首载直接 ready 不发，弹层不
    // 自动开播（用户还没等过，自动进播放器是惊吓不是方便）。
    if (prev === 'preparing' && s.state === 'ready') emit('ready')
    if (s.state === 'preparing') startPoll()
    else stopPoll()
  } catch {
    // 状态只是展示：拉不到就保持安静，不打扰点播流程
    if (my === seq) stopPoll()
  }
}

watch(
  () => props.itemId,
  (now) => {
    status.value = null
    prevState.value = null
    stopPoll()
    if (now !== null && now !== undefined && now !== '') void load()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  seq++
  stopPoll()
})
</script>

<style scoped>
.psb {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1.5px solid var(--ink, #2a2620);
  border-radius: var(--paper-r-lg, 12px);
  background: var(--paper-card, #fffdf6);
}
.psb-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: var(--paper-r-full, 50%);
  background: var(--paper-primary, #016a71);
}
.psb--preparing .psb-dot {
  animation: psb-pulse 1.4s ease-in-out infinite;
}
.psb--failed {
  border-color: var(--red, #c23b22);
}
.psb--failed .psb-dot {
  background: var(--red, #c23b22);
}
.psb-body {
  min-width: 0;
  flex: 1;
}
.psb-title {
  font-size: 13px;
  font-weight: 700;
  line-height: 1.45;
}
.psb-sub {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink2, #6b6257);
}
.psb-eta {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink2, #6b6257);
}
.psb-track {
  margin-top: 8px;
  height: 6px;
  border-radius: var(--paper-r-full, 999px);
  background: var(--paper-card-2, #f0ebe3);
  overflow: hidden;
}
.psb-fill {
  height: 100%;
  border-radius: inherit;
  background: var(--paper-primary, #016a71);
  transition: width 0.6s ease;
}
@keyframes psb-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.8);
  }
}
@media (prefers-reduced-motion: reduce) {
  .psb--preparing .psb-dot {
    animation: none;
  }
  .psb-fill {
    transition: none;
  }
}
</style>
