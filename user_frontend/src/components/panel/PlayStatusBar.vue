<!-- 起播状态条：点播前先看清这部片对当前用户是什么状态。
     数据来自只读口 GET /api/play/items/{id}/status（src/api/playStatus.ts）。
     - ready：已就绪（本人盘 / 公共池 / 直读），一行说清；
     - preparing：正在准备——排队/搬运中，有可靠进度才显示百分比，
       每 8 秒自动刷新，页面切到后台或组件卸载即停；
     - failed：人话原因 + 一句能照着做的建议；
     - idle / 加载失败：什么都不渲染（不打扰，首播说明在 sheet 里已有）。
     本组件只展示，不触发任何转存/播放动作。 -->
<template>
  <div v-if="view" class="psb" :class="`psb--${view.state}`" role="status">
    <span class="psb-dot" aria-hidden="true"></span>
    <div class="psb-body">
      <div class="psb-title">{{ view.title }}</div>
      <div v-if="view.sub" class="psb-sub">{{ view.sub }}</div>
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
  fetchPlayStatus,
  type PlayFailReason,
  type PlayStatus,
} from '@/api/playStatus'

const props = defineProps<{ itemId: string | number | null }>()

const POLL_MS = 8000

const status = ref<PlayStatus | null>(null)
let timer = 0
let seq = 0

const FAIL_COPY: Record<PlayFailReason, { title: string; sub: string }> = {
  space: {
    title: '上次没准备成：你的绑定盘空间不足',
    sub: '清一清绑定盘的空间，或者换绑一个更大的盘，再回来点播。',
  },
  auth: {
    title: '上次没准备成：绑定盘的授权失效了',
    sub: '去账号设置里重新绑定一次网盘，再回来点播。',
  },
  source_dead: {
    title: '这部片的源文件已经失效了',
    sub: '可以去许愿池求一下，或者先看看别的片。',
  },
  no_binding: {
    title: '你还没有绑定网盘',
    sub: '先绑定自己的网盘，再回来点播。',
  },
  generic: {
    title: '上次准备这部片失败了',
    sub: '再点一次播放器就是重试；反复失败就在群里喊一下管理员。',
  },
}

interface StatusView {
  state: 'ready' | 'preparing' | 'failed'
  title: string
  sub: string
  pct: number | null
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
    return { state: 'ready', title: '已就绪，可以直接看', sub, pct: null }
  }
  if (s.state === 'preparing') {
    if (s.stage === 'queued') {
      return {
        state: 'preparing',
        title: '正在为你准备这部片',
        sub: '已在排队，轮到就会开始搬运',
        pct: null,
      }
    }
    return {
      state: 'preparing',
      title: '正在为你准备这部片',
      sub:
        s.progress_pct !== null
          ? `正在搬运到你的盘里，已完成 ${s.progress_pct}%`
          : '正在搬运到你的盘里',
      pct: s.progress_pct,
    }
  }
  if (s.state === 'failed') {
    const copy = FAIL_COPY[s.reason ?? 'generic'] ?? FAIL_COPY.generic
    return { state: 'failed', title: copy.title, sub: copy.sub, pct: null }
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
    status.value = s
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
