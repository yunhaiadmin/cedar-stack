<script setup lang="ts">
/**
 * 4K MSE 探针页（一次性真机诊断，2026-11-17）：只对 staff 渲染，
 * 不进任何导航。用户在真机上打开本页点一次「开始探针」，页面以
 * libmedia MSE 管线逐轨试播样本集（默认《多喜一家人》第 1 集，
 * 可用 ?item=<guid> 换样本），每次尝试的结果实时 POST 到
 * /api/user/probe-4k/result 落库，服务端侧直接查表读结论。
 * 逻辑本体在 utils/probe4k.ts；本页只做编排与展示。
 * 路线定音后本页与端点一并退役。
 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api'
import { fetchPlaybackInfo } from '@/api/webPlayer'
import { pickSoftRelayUrl } from '@/utils/softDecode'
import {
  PROBE_4K_SAMPLE_GUID,
  gateForAttempt,
  outcomeOf,
  planProbeAttempts,
  runMsePreflight,
  runProbeAttempt,
  type ProbeAttemptResult,
  type ProbeSourceLike,
  type ProbeStreamLike,
} from '@/utils/probe4k'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const userStore = useUserStore()
const isStaff = computed(() => !!userStore.user?.is_staff)

type Phase = 'ready' | 'running' | 'done'
const phase = ref<Phase>('ready')
const logs = ref<string[]>([])
const verdict = ref('')
const stageRef = ref<HTMLDivElement | null>(null)

function log(line: string) {
  const now = new Date()
  const ts = now.toTimeString().slice(0, 8)
  logs.value.push(`${ts} ${line}`)
}

async function postResult(itemGuid: string, attempt: string, outcome: string, payload: unknown) {
  try {
    await api.post<never, { ok: boolean }>('/api/user/probe-4k/result', {
      item_guid: itemGuid,
      attempt,
      outcome,
      payload: JSON.stringify(payload).slice(0, 19000),
    })
    log(`已回传：${attempt} → ${outcome}`)
  } catch (e) {
    log(`回传失败（${attempt}）：${e instanceof Error ? e.message : String(e)}`)
  }
}

async function start() {
  if (phase.value === 'running') return
  phase.value = 'running'
  verdict.value = ''
  const guid = (route.query.item as string) || PROBE_4K_SAMPLE_GUID
  log(`样本条目：${guid}`)
  log(`UA：${navigator.userAgent}`)
  try {
    const info = await fetchPlaybackInfo(guid)
    const sources = info.MediaSources || []
    const source = sources.find((s) => s.DirectStreamUrl) || sources[0]
    if (!source?.DirectStreamUrl) {
      log('PlaybackInfo 没给可播的源，探针终止')
      await postResult(guid, 'preflight', 'playback-info-failed', {
        ua: navigator.userAgent,
        sources: sources.length,
      })
      verdict.value = '取不到播放源（门禁未过或源未就绪），结论已回传'
      phase.value = 'done'
      return
    }
    const streams: ProbeStreamLike[] = (source.MediaStreams || []).map((s) => ({
      Index: s.Index,
      Type: s.Type,
      Codec: s.Codec,
      Width: s.Width,
      Height: s.Height,
      Channels: s.Channels,
      BitDepth: s.BitDepth,
    }))
    const sourceLike: ProbeSourceLike = {
      Id: source.Id,
      Container: source.Container || undefined,
      MediaStreams: streams,
    }
    log(`源：容器 ${source.Container || '?'}，流 ${streams.map((s) => `#${s.Index}:${s.Type}:${s.Codec || '?'}`).join(' ')}`)

    const preflight = await runMsePreflight(sourceLike)
    log(`MSE 接口：${preflight.mseApi || '无'}；跨域隔离：${preflight.crossOriginIsolated ? '是' : '否'}`)
    for (const e of preflight.entries) {
      log(`isTypeSupported ${e.mime} → ${e.supported === null ? '问不出' : e.supported ? '认' : '不认'}`)
    }
    for (const w of preflight.webcodecs) {
      log(`WebCodecs 真实参数 ${w.codec} → ${w.supported === null ? '问不出' : w.supported ? '支持' : '不支持'}`)
    }
    await postResult(guid, 'preflight', 'done', {
      ua: navigator.userAgent,
      streams,
      preflight,
    })

    const url = pickSoftRelayUrl(sources, source.Id) || source.DirectStreamUrl
    const plans = planProbeAttempts(sourceLike)
    const results: ProbeAttemptResult[] = []
    let successKey = ''
    for (const plan of plans) {
      log(`尝试 ${plan.key}：${plan.label}（闸门 ${gateForAttempt(preflight, plan)}）`)
      if (stageRef.value) stageRef.value.innerHTML = ''
      const r = await runProbeAttempt(stageRef.value as HTMLDivElement, url, sourceLike, plan, preflight)
      results.push(r)
      const outcome = outcomeOf(r)
      log(
        `结果 ${plan.key}：${outcome}；useMSE=${r.useMSE}；出画=${r.firstVideoRendered || r.videoObserved}；出声=${r.firstAudioRendered}；起播=${r.startMs ?? '—'}ms${r.error ? `；报错=${r.error}` : ''}`,
      )
      await postResult(guid, plan.key, outcome, r)
      if (outcome === 'mse-playing') {
        successKey = plan.key
        break
      }
    }
    await postResult(guid, 'summary', successKey ? `mse-ok-${successKey}` : 'mse-not-reached', {
      successAttempt: successKey || null,
      outcomes: results.map((r) => ({ attempt: r.attempt, outcome: outcomeOf(r) })),
    })
    verdict.value = successKey
      ? `MSE 通了：${successKey} 尝试出画成功，结论已回传`
      : 'MSE 没走通（各尝试结果已回传，服务端查表看细节）'
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    log(`探针异常终止：${msg}`)
    await postResult(guid, 'preflight', 'probe-crashed', { ua: navigator.userAgent, error: msg })
    verdict.value = '探针异常终止，错误已回传'
  } finally {
    phase.value = 'done'
  }
}
</script>

<template>
  <div class="probe4k">
    <h1 class="probe4k-title">4K MSE 探针</h1>
    <template v-if="!isStaff">
      <p class="probe4k-note">本页仅管理员可用。</p>
    </template>
    <template v-else>
      <p class="probe4k-note">
        点一次开始，页面会自动逐轨试播样本集（4K），全程别切走本页；
        结果自动回传服务器，不用截图。
      </p>
      <button
        class="probe4k-btn"
        :disabled="phase === 'running'"
        @click="start"
      >
        {{ phase === 'running' ? '探针运行中…' : phase === 'done' ? '再跑一次' : '开始探针' }}
      </button>
      <div ref="stageRef" class="probe4k-stage"></div>
      <p v-if="verdict" class="probe4k-verdict">{{ verdict }}</p>
      <pre class="probe4k-log">{{ logs.join('\n') }}</pre>
    </template>
  </div>
</template>

<style scoped>
.probe4k {
  min-height: 100dvh;
  background: #0c0a09;
  color: #f5f1ea;
  padding: 20px 16px 40px;
  box-sizing: border-box;
}
.probe4k-title {
  font-size: 20px;
  margin: 0 0 8px;
}
.probe4k-note {
  font-size: 14px;
  opacity: 0.8;
  margin: 0 0 16px;
  line-height: 1.5;
}
.probe4k-btn {
  display: block;
  width: 100%;
  padding: 14px;
  font-size: 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  background: rgba(255, 255, 255, 0.08);
  color: inherit;
  margin-bottom: 16px;
}
.probe4k-btn:disabled {
  opacity: 0.5;
}
.probe4k-stage {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 16px;
}
.probe4k-stage :deep(video),
.probe4k-stage :deep(canvas) {
  width: 100%;
  height: 100%;
}
.probe4k-verdict {
  font-size: 15px;
  line-height: 1.5;
  margin: 0 0 12px;
}
.probe4k-log {
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  opacity: 0.85;
  margin: 0;
}
</style>
