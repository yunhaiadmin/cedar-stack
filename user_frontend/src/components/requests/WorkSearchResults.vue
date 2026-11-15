<template>
  <!-- 作品搜索结果（求片/追新共用，照友站 2.2 形态）：单容器卡＋分隔线行；
       行＝海报＋片名＋类型胶囊＋状态 pill＋「原名 / 年份」＋右侧行动区。
       行动分化（2.2/2.5）：求片态——在库纯文字「媒体库已有」、已有未完成
       求片描边「查看已有请求」、其余彩虹「发起求片」；追新态——暂无纯
       文字、在库彩虹「发起追新」、已有未完成追新描边「查看已有请求 ›」
       并在行下写明已有请求。搜索中出同容器骨架屏。 -->
  <div class="wsr">
    <p v-if="status === 'done' && degraded" class="wsr-degraded">
      搜索源暂时连不上，这次只按站内有没有来标状态。
    </p>

    <div v-if="searching && candidates.length === 0" class="wsr-box wsr-sk" aria-hidden="true">
      <div v-for="i in 3" :key="i" class="wsr-sk__row">
        <span class="wsr-sk__poster"></span>
        <span class="wsr-sk__lines">
          <span class="wsr-sk__bar wsr-sk__bar--long"></span>
          <span class="wsr-sk__bar wsr-sk__bar--short"></span>
        </span>
      </div>
    </div>

    <div v-else-if="status === 'done' && candidates.length > 0" class="wsr-box">
      <div v-for="c in candidates" :key="c.tmdb_id" class="wsr-row">
        <div class="wsr-row__main">
          <img v-if="c.poster_url" :src="imageAuthUrl(c.poster_url, 120)" :alt="c.name" class="wsr-poster" />
          <span v-else class="wsr-poster wsr-poster--ph" aria-hidden="true">{{ c.name.slice(0, 1) }}</span>
          <div class="wsr-info">
            <div class="wsr-title-line">
              <span class="wsr-name">{{ c.name }}</span>
              <span class="wsr-kind">{{ c.kind === 'movie' ? '电影' : '剧集' }}</span>
              <span class="wsr-status" :class="{ on: c.in_library }">
                {{ c.in_library ? '✓ 库内已有' : '库内暂无' }}
              </span>
            </div>
            <span class="wsr-sub">{{ subLine(c) }}</span>
          </div>
          <div class="wsr-act">
            <span v-if="rowState(c) === 'have'" class="wsr-act__text wsr-act__text--have">媒体库已有</span>
            <span v-else-if="rowState(c) === 'absent'" class="wsr-act__text">媒体库暂无</span>
            <button
              v-else-if="rowState(c) === 'existing'"
              type="button"
              class="wsr-btn wsr-btn--ghost"
              @click="emit('existing', c)"
            >查看已有请求 <span aria-hidden="true">›</span></button>
            <button
              v-else
              type="button"
              class="wsr-btn wsr-btn--go"
              @click="emit('pick', toWork(c))"
            >{{ mode === 'seek' ? '发起求片' : '发起追新' }}</button>
          </div>
        </div>
        <p v-if="rowState(c) === 'existing' && mode === 'chase'" class="wsr-row__note">
          已有请求，先看看处理回复再决定要不要补充。
        </p>
      </div>
    </div>

    <div v-else-if="status === 'done' && query" class="wsr-box wsr-empty">
      <p class="wsr-empty__t">没有搜到「{{ query }}」</p>
      <p class="wsr-empty__s">可以换个片名再试，或用原名搜索。</p>
      <button v-if="mode === 'seek'" type="button" class="wsr-btn wsr-btn--go" @click="emit('fallback')">
        就按「{{ query }}」这个名字提交
      </button>
    </div>

    <div v-else-if="status === 'unconfigured'" class="wsr-box wsr-empty">
      <p class="wsr-empty__t">还没连上片源搜索</p>
      <p class="wsr-empty__s">暂时搜不了，可以先按片名提交，会人工确认。</p>
      <button v-if="mode === 'seek' && query" type="button" class="wsr-btn wsr-btn--go" @click="emit('fallback')">
        就按「{{ query }}」这个名字提交
      </button>
    </div>

    <div v-else-if="status === 'error'" class="wsr-box wsr-empty">
      <p class="wsr-empty__t">搜索失败：{{ error }}</p>
      <p class="wsr-empty__s">可以再试一次；急的话先按片名提交，会人工确认。</p>
      <button v-if="mode === 'seek' && query" type="button" class="wsr-btn wsr-btn--go" @click="emit('fallback')">
        就按「{{ query }}」这个名字提交
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { MediaSeekCandidate } from '@/api'
import { imageAuthUrl } from '@/api/emby'
import type { SubmitWork } from './RequestSubmitModal.vue'

const props = defineProps<{
  mode: 'seek' | 'chase'
  candidates: MediaSeekCandidate[]
  searching: boolean
  status: 'idle' | 'loading' | 'done' | 'unconfigured' | 'error'
  error: string
  degraded: boolean
  query: string
}>()
const emit = defineEmits<{
  pick: [work: SubmitWork]
  existing: [candidate: MediaSeekCandidate]
  fallback: []
}>()

type RowState = 'have' | 'absent' | 'existing' | 'go'

/** 行动分化（2.2/2.5）：先看库内状态，再看有没有未完成的同类请求 */
function rowState(c: MediaSeekCandidate): RowState {
  if (props.mode === 'seek') {
    if (c.in_library) return 'have'
    if (c.open_seek) return 'existing'
    return 'go'
  }
  if (!c.in_library) return 'absent'
  if (c.open_chase) return 'existing'
  return 'go'
}

function subLine(c: MediaSeekCandidate): string {
  const parts: string[] = []
  if (c.original_name) parts.push(c.original_name)
  if (c.year) parts.push(c.year)
  return parts.join(' / ')
}

function toWork(c: MediaSeekCandidate): SubmitWork {
  return {
    tmdb_id: c.tmdb_id,
    name: c.name,
    year: c.year,
    kind: c.kind,
    poster_url: c.poster_url,
  }
}
</script>

<style scoped>
/* 搜索结果：单容器＋行内分隔线（照友站），海报约 43×60 CSS px；
   色板与求片反馈页一致（缺省深色、浅色换浅）。 */
.wsr { display: flex; flex-direction: column; gap: 10px; }
.wsr-degraded { margin: 0; font-size: 12px; color: #d4d4d8; }
.wsr-box {
  background: #17181a; border: 1px solid #26262a; border-radius: 16px;
  padding: 4px 16px;
}
.wsr-row { padding: 12px 0; }
.wsr-row + .wsr-row { border-top: 1px solid #26262a; }
.wsr-row__main { display: flex; align-items: center; gap: 12px; }
.wsr-poster { flex: none; width: 43px; height: 60px; object-fit: cover; border-radius: 8px; background: #1e1f21; }
.wsr-poster--ph {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 17px; font-weight: 800; color: #55575c;
}
.wsr-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.wsr-title-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.wsr-name { font-size: 15px; font-weight: 700; word-break: break-word; }
.wsr-kind {
  flex: none; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 999px;
  background: #26262a; color: #d4d4d8;
}
.wsr-status { flex: none; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 999px; background: #26262a; color: #9ca3af; }
.wsr-status.on { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
.wsr-sub { font-size: 12px; color: #9ca3af; word-break: break-word; }
.wsr-act { flex: none; }
.wsr-act__text { font-size: 13px; color: #9ca3af; }
.wsr-act__text--have { color: #4ade80; font-weight: 700; }
.wsr-btn {
  min-height: 34px; padding: 6px 14px; border-radius: 999px; cursor: pointer;
  font-size: 13px; font-weight: 700; font-family: inherit; white-space: nowrap;
}
.wsr-btn--go {
  color: #ffffff; border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 10px 22px -12px rgba(0, 0, 0, 0.55);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.wsr-btn--ghost {
  color: #fbfcfd; background: transparent; border: 1.5px solid #4b5563;
}
.wsr-row__note { margin: 8px 0 0 55px; font-size: 12px; color: #9ca3af; }
.wsr-empty { padding: 16px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.wsr-empty__t { margin: 0; font-size: 14px; font-weight: 700; }
.wsr-empty__s { margin: 0; font-size: 12px; color: #9ca3af; line-height: 1.6; }
.wsr-sk__row { display: flex; align-items: center; gap: 12px; padding: 12px 0; }
.wsr-sk__row + .wsr-sk__row { border-top: 1px solid #26262a; }
.wsr-sk__poster { flex: none; width: 43px; height: 60px; border-radius: 8px; background: #232326; }
.wsr-sk__lines { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.wsr-sk__bar { height: 12px; border-radius: 6px; background: #232326; }
.wsr-sk__bar--long { width: 62%; }
.wsr-sk__bar--short { width: 38%; }
.wsr-sk__poster, .wsr-sk__bar { animation: wsr-pulse 1.2s ease-in-out infinite; }
@keyframes wsr-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }

html[data-theme='light'] .wsr-degraded { color: #55575c; }
html[data-theme='light'] .wsr-box { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .wsr-row + .wsr-row, html[data-theme='light'] .wsr-sk__row + .wsr-sk__row { border-top-color: #e5e5e5; }
html[data-theme='light'] .wsr-poster { background: #f0f0f1; }
html[data-theme='light'] .wsr-poster--ph { color: #9ca3af; }
html[data-theme='light'] .wsr-kind { background: #f0f0f1; color: #55575c; }
html[data-theme='light'] .wsr-status { background: #f0f0f1; color: #6b7280; }
html[data-theme='light'] .wsr-status.on { background: rgba(22, 163, 74, 0.12); color: #16a34a; }
html[data-theme='light'] .wsr-sub, html[data-theme='light'] .wsr-act__text, html[data-theme='light'] .wsr-row__note, html[data-theme='light'] .wsr-empty__s { color: #6b7280; }
html[data-theme='light'] .wsr-act__text--have { color: #16a34a; }
html[data-theme='light'] .wsr-btn--go {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .wsr-btn--ghost { color: #111111; border-color: #c9ccd1; }
html[data-theme='light'] .wsr-sk__poster, html[data-theme='light'] .wsr-sk__bar { background: #ececec; }
</style>
