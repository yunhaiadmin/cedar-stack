<template>
  <UhdAccountLayout>
    <!-- 标题详情（2026-11-14 新增，照友站《谷雨》详情态）：从搜索结果/广场点进单片——
         媒体卡（海报/库内 pill/片名/类型·年份/TMDB 外链/站内观看）＋「请求」计数与
         防重复提示＋该片请求卡列表。数据全走真实接口：广场按片名聚合（组信息＋
         pending 明细）＋我的请求（本人全部状态行）合并去重；他人已完成/已拒绝的
         明细接口不下发，列表口径见脚本注释。页面跟随用户外观。 -->
    <div class="rmd-page">
      <div class="rmd-inner">
        <div class="rmd-topbar">
          <button type="button" class="rmd-back" aria-label="返回" @click="goBack">
            <ChevronLeft :size="24" aria-hidden="true" />
          </button>
          <span class="rmd-topbar__title">求片反馈</span>
          <RouterLink to="/library" class="rmd-watch">
            <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
              <defs>
                <linearGradient id="wp-g-rmd" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stop-color="#2f8fe0" />
                  <stop offset="0.5" stop-color="#e8766f" />
                  <stop offset="1" stop-color="#ecd04f" />
                </linearGradient>
              </defs>
              <path d="M7 4.2v15.6c0 .8.9 1.3 1.6.9l11.6-7.8c.6-.4.6-1.3 0-1.7L8.6 3.3C7.9 2.9 7 3.4 7 4.2Z" fill="url(#wp-g-rmd)" />
            </svg>
            网页观影
          </RouterLink>
        </div>

        <button type="button" class="rmd-backpill" @click="goBack">
          <ChevronLeft :size="16" aria-hidden="true" /> 返回
        </button>

        <section class="rmd-media" aria-label="影片信息">
          <img
            v-if="posterUrl"
            class="rmd-media__poster"
            :src="imageAuthUrl(posterUrl, 300)"
            :alt="mediaName"
            loading="lazy"
          />
          <span v-else class="rmd-media__ph" aria-hidden="true">{{ (mediaName || '片').slice(0, 1) }}</span>
          <div class="rmd-media__info">
            <span v-if="inLibrary" class="rmd-pill rmd-pill--green">✓ 库内已有</span>
            <h1 class="rmd-media__name">{{ mediaName }}</h1>
            <p class="rmd-media__meta">{{ kindLabel }}<template v-if="mediaYear"> / {{ mediaYear }}</template></p>
            <div class="rmd-media__actions">
              <a
                v-if="tmdbUrl"
                class="rmd-media__tmdb"
                :href="tmdbUrl"
                target="_blank"
                rel="noopener noreferrer"
              >TMDB ↗</a>
              <button
                v-if="inLibrary && embyItemId"
                type="button"
                class="rmd-media__watch"
                @click="goWatch"
              >站内观看</button>
            </div>
          </div>
        </section>

        <div class="rmd-reqhead">
          <h2 class="rmd-reqhead__title">请求</h2>
          <span class="rmd-reqhead__count">{{ totalCount }} 条</span>
        </div>
        <p class="rmd-hint">同一部片已经有人在求时，不用重复提交，给已有请求 +1 附议就行；处理进度都会在这里更新。</p>

        <div v-if="loading" class="rmd-loading">
          <div v-for="i in 2" :key="i" class="rmd-skeleton" style="height: 92px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="loadError" class="rmd-err">
          请求记录读取失败：{{ loadError }}<br />
          <button class="rmd-retry" @click="loadAll">重试</button>
        </div>
        <div v-else-if="rows.length === 0" class="rmd-empty">
          <p>这部片还没有人提交过请求</p>
          <RouterLink class="rmd-empty__go" :to="{ path: '/game/requests', query: { tab: 'create', q: mediaName } }">去发起求片</RouterLink>
        </div>
        <div v-else class="rmd-rows">
          <article v-for="row in rows" :key="row.id" class="rmd-card">
            <div class="rmd-card__head">
              <span class="rmd-pill rmd-pill--type">{{ row.kindLabel }}</span>
              <span class="rmd-pill" :class="'rmd-pill--' + row.state">{{ stateLabel(row.state) }}</span>
              <span v-if="row.mine" class="rmd-pill rmd-pill--mine">我提交的</span>
              <span v-else-if="row.requesterName" class="rmd-card__requester">{{ maskName(row.requesterName) }}</span>
              <span class="rmd-card__time">{{ formatDate(row.created_at) }}</span>
            </div>
            <p class="rmd-card__body">
              {{ row.title }}<template v-if="row.seasonLabel"> · {{ row.seasonLabel }}</template>
            </p>
            <p v-if="row.missingText" class="rmd-card__sub">{{ row.missingText }}</p>
            <p v-if="row.adminNote" class="rmd-card__note">管理员备注：{{ row.adminNote }}</p>
            <div class="rmd-card__foot">
              <span class="rmd-card__votes">{{ row.voteCount > 0 ? row.voteCount + ' 人想看' : '还没有人附议' }}</span>
              <button
                v-if="row.state === 'pending' || row.state === 'doing'"
                class="rmd-vote"
                :class="{ 'rmd-vote--done': row.voted }"
                :disabled="row.voted || voting !== null"
                @click="upvote(row)"
              >{{ row.voted ? '✓ 已附议' : voting === row.id ? '附议中' : '+1 附议' }}</button>
              <button
                v-if="row.state === 'done' && row.embyItemId"
                type="button"
                class="rmd-link"
                @click="goItem(row.embyItemId)"
              >去看这部</button>
            </div>
          </article>
        </div>
      </div>
    </div>
  </UhdAccountLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft } from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import {
  mediaSeekApi,
  type HotMediaSeekRequest, type MediaSeekRequest, type PlazaGroup,
} from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

type UnifiedState = 'pending' | 'doing' | 'done' | 'rejected' | 'withdrawn'

interface DetailRow {
  id: number
  kindLabel: string
  state: UnifiedState
  title: string
  seasonLabel?: string
  missingText?: string
  adminNote?: string
  embyItemId?: string | null
  voteCount: number
  voted: boolean
  mine: boolean
  created_at: string
  /** 请求人姓名：公开接口当前不下发（数据缺口），运行时带上即打码展示 */
  requesterName?: string
}

/* 公开面用户名打码（照友站样本）：首尾各留 1 字符、中间星号；
   只用在广场/详情等公开面，本人行走「我提交的」不打码 */
function maskName(name: string): string {
  const s = (name || '').trim()
  if (s.length <= 1) return s
  if (s.length === 2) return s[0] + '*'
  return s[0] + '*'.repeat(s.length - 2) + s[s.length - 1]
}
function requesterOf(r: unknown): string | undefined {
  const o = r as { requester_name?: string | null; username?: string | null }
  return (o.requester_name || o.username || undefined) || undefined
}

const route = useRoute()
const router = useRouter()
const toast = useToast()

const tmdbId = computed(() => (typeof route.query.tmdb === 'string' ? route.query.tmdb : ''))
const mediaName = computed(() => (typeof route.query.name === 'string' ? route.query.name : ''))
const mediaYear = computed(() => (typeof route.query.year === 'string' ? route.query.year : ''))
const mediaType = computed(() => (typeof route.query.type === 'string' ? route.query.type : ''))
const kindLabel = computed(() => (mediaType.value === 'movie' ? '电影' : '剧集'))
const tmdbUrl = computed(() => (tmdbId.value
  ? `https://www.themoviedb.org/${mediaType.value === 'movie' ? 'movie' : 'tv'}/${tmdbId.value}`
  : ''))

const group = ref<PlazaGroup | null>(null)
const myRequests = ref<MediaSeekRequest[]>([])
const loading = ref(true)
const loadError = ref('')
const voting = ref<number | null>(null)

const posterUrl = computed(() => group.value?.poster_url || null)
const inLibrary = computed(() => group.value?.in_library ?? false)
const embyItemId = computed(() => group.value?.emby_item_id || null)
const totalCount = computed(() => group.value?.total_count ?? rows.value.length)

function normTitle(s: string): string {
  return s.toLowerCase().replace(/[《》\s·•:：,，.。!！?？;；'"“”‘’()（）\-—]/g, '')
}
function sameMedia(name: string, rowTmdb: string | null | undefined): boolean {
  if (tmdbId.value && (rowTmdb || '').trim() === tmdbId.value) return true
  return !!mediaName.value && normTitle(name) === normTitle(mediaName.value)
}
function seekState(s: string): UnifiedState {
  if (s === 'approved') return 'doing'
  if (s === 'completed') return 'done'
  if (s === 'rejected') return 'rejected'
  if (s === 'withdrawn') return 'withdrawn'
  return 'pending'
}
function stateLabel(s: UnifiedState): string {
  return { pending: '待处理', doing: '处理中', done: '已完成', rejected: '已拒绝', withdrawn: '已撤回' }[s]
}
function kindLabelOf(r: { request_kind?: string | null }): string {
  return r.request_kind === 'missing_episodes' ? '追新' : '求片'
}

/* 请求列表口径：广场组内明细只有 pending 可附议行（他人已完成/已拒绝的明细
   接口不下发，是已知数据缺口）；本人行走我的请求接口、状态齐全。两边按 id
   去重合并（广场行优先，附议态以广场为准），按创建时间倒序。 */
const rows = computed<DetailRow[]>(() => {
  const byId = new Map<number, DetailRow>()
  for (const w of myRequests.value) {
    if (!sameMedia(w.movie_name, w.tmdb_id)) continue
    byId.set(w.id, {
      id: w.id,
      kindLabel: kindLabelOf(w),
      state: seekState(w.status),
      title: w.movie_name,
      seasonLabel: w.season_label,
      missingText: w.missing_text,
      adminNote: w.admin_note || undefined,
      embyItemId: w.emby_item_id,
      voteCount: w.vote_count ?? 0,
      voted: false,
      mine: true,
      created_at: w.created_at,
    })
  }
  for (const r of group.value?.requests ?? []) {
    const prev = byId.get(r.id)
    byId.set(r.id, {
      id: r.id,
      kindLabel: kindLabelOf(r),
      state: prev?.state ?? 'pending',
      title: r.movie_name,
      seasonLabel: r.season_label,
      missingText: r.missing_text,
      adminNote: prev?.adminNote,
      embyItemId: prev?.embyItemId,
      voteCount: r.vote_count,
      voted: r.voted,
      mine: r.mine,
      created_at: r.created_at,
      requesterName: r.mine ? undefined : requesterOf(r),
    })
  }
  return [...byId.values()].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
})

async function loadAll(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [plazaRes, mineRes] = await Promise.all([
      mediaSeekApi.plaza(mediaName.value ? { q: mediaName.value } : {}),
      mediaSeekApi.getMyRequests(),
    ])
    const groups = plazaRes.groups || []
    group.value = groups.find((g) => sameMedia(g.movie_name, g.tmdb_id)) ?? null
    myRequests.value = mineRes.requests || []
  } catch (e) {
    loadError.value = humanizeError(e)
  } finally {
    loading.value = false
  }
}

async function upvote(row: DetailRow): Promise<void> {
  if (row.voted || voting.value) return
  voting.value = row.id
  try {
    const r = await mediaSeekApi.vote(row.id)
    row.voted = r.voted
    row.voteCount = r.vote_count
  } catch {
    toast.error('附议失败，再试一次')
  } finally {
    voting.value = null
  }
}

function goBack(): void {
  if (window.history.length > 1) router.back()
  else void router.push('/game/requests')
}
function goWatch(): void {
  if (embyItemId.value) void router.push(`/media/${embyItemId.value}`)
}
function goItem(id: string): void {
  void router.push(`/media/${id}`)
}
function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  if (Number.isNaN(date.getTime())) return '时间未知'
  const now = new Date()
  const diff = Math.max(0, now.getTime() - date.getTime())
  const days = Math.floor(diff / (1000 * 60 * 60 * 24))
  if (days === 0) {
    const hours = Math.floor(diff / (1000 * 60 * 60))
    if (hours === 0) {
      const minutes = Math.floor(diff / (1000 * 60))
      return minutes === 0 ? '刚刚' : `${minutes}分钟前`
    }
    return `${hours}小时前`
  }
  if (days === 1) return '昨天'
  if (days < 7) return `${days}天前`
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

onMounted(() => { void loadAll() })
</script>

<style scoped>
/* 标题详情页：与求片反馈页同壳（.rf-page 同款负边距铺满＋深/浅两套）。 */
.rmd-page { margin: -28px -32px -120px; padding: 28px 32px 220px; background: #08080a; min-height: 100dvh; }
.rmd-inner { max-width: 1006px; margin: 0 auto; }
.rmd-topbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.rmd-back {
  flex: none; width: 38px; height: 38px; margin-left: -8px;
  display: inline-flex; align-items: center; justify-content: center;
  border: none; border-radius: 999px; background: transparent;
  color: #fbfcfd; cursor: pointer; font-family: inherit;
}
.rmd-back:active { background: rgba(255, 255, 255, 0.08); }
.rmd-topbar__title { flex: 1; min-width: 0; font-size: 17px; font-weight: 800; color: #fbfcfd; }
.rmd-watch {
  flex: none; display: inline-flex; align-items: center; gap: 8px;
  height: 40px; padding: 0 16px; border-radius: 999px;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box;
  color: #ffffff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 12px 28px -12px rgba(0, 0, 0, 0.5);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  font-size: 14px; font-weight: 700; text-decoration: none; white-space: nowrap;
}
/* 「← 返回」胶囊（照友站详情态）：描边胶囊，位于顶栏与媒体卡之间 */
.rmd-backpill {
  display: inline-flex; align-items: center; gap: 2px;
  height: 36px; padding: 0 16px 0 10px; margin-bottom: 16px;
  border-radius: 999px; cursor: pointer; font-family: inherit;
  background: transparent; border: 1.5px solid #3a3b3f; color: #fbfcfd;
  font-size: 13px; font-weight: 700;
}
.rmd-media {
  display: flex; gap: 16px; align-items: flex-start;
  background: #17181a; border: 1px solid #26262a; border-radius: 20px; padding: 16px;
  margin-bottom: 22px;
}
.rmd-media__poster { flex: none; width: 92px; height: 138px; border-radius: 12px; object-fit: cover; background: #1e1f21; }
.rmd-media__ph {
  flex: none; width: 92px; height: 138px; border-radius: 12px; background: #1e1f21;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 34px; font-weight: 800; color: #55575c;
}
.rmd-media__info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.rmd-media__name { margin: 0; font-size: 22px; font-weight: 800; color: #fbfcfd; line-height: 1.3; word-break: break-word; }
.rmd-media__meta { margin: 0; font-size: 13px; color: #9ca3af; }
.rmd-media__actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 2px; }
.rmd-media__tmdb { font-size: 13px; font-weight: 700; color: #6ea8fe; text-decoration: none; }
.rmd-media__watch {
  height: 36px; padding: 0 16px; border-radius: 999px; cursor: pointer; font-family: inherit;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box;
  color: #ffffff; font-size: 13px; font-weight: 800;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 8px 20px -10px rgba(0, 0, 0, 0.55);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.rmd-pill {
  flex: none; display: inline-flex; align-items: center; height: 22px; padding: 0 8px;
  border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap;
  border: 1px solid #333336; color: #9ca3af; background: transparent;
}
.rmd-pill--green { background: rgba(22, 163, 74, 0.16); color: #4ade80; border-color: rgba(22, 163, 74, 0.5); }
.rmd-pill--type { background: #26262a; color: #d4d4d8; border-color: #26262a; }
.rmd-pill--mine { background: transparent; color: #6ea8fe; border-color: #6ea8fe; }
.rmd-pill--pending { color: #9ca3af; border-color: #3a3b3f; }
.rmd-pill--doing { color: #f59e0b; border-color: #f59e0b; }
.rmd-pill--done { color: #4ade80; border-color: #16a34a; }
.rmd-pill--rejected { color: #f87171; border-color: #ef4444; }
.rmd-pill--withdrawn { color: #9ca3af; border-color: #3a3b3f; }
.rmd-reqhead { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.rmd-reqhead__title { margin: 0; font-size: 17px; font-weight: 800; color: #fbfcfd; }
.rmd-reqhead__count { font-size: 13px; color: #9ca3af; }
.rmd-hint { margin: 8px 0 14px; font-size: 12px; line-height: 1.6; color: #9ca3af; }
.rmd-rows { display: flex; flex-direction: column; gap: 10px; }
.rmd-card { background: #17181a; border: 1px solid #26262a; border-radius: 16px; padding: 14px; }
.rmd-card__head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rmd-card__time { margin-left: auto; font-size: 12px; color: #9ca3af; white-space: nowrap; }
.rmd-card__requester { font-size: 12px; color: #9ca3af; }
.rmd-card__body { margin: 10px 0 0; font-size: 14px; font-weight: 700; color: #fbfcfd; word-break: break-word; }
.rmd-card__sub { margin: 4px 0 0; font-size: 12px; color: #9ca3af; line-height: 1.6; }
.rmd-card__note { margin: 6px 0 0; font-size: 12px; color: #d4d4d8; line-height: 1.6; }
.rmd-card__foot { margin-top: 10px; display: flex; align-items: center; gap: 10px; }
.rmd-card__votes { flex: 1; font-size: 12px; color: #9ca3af; }
.rmd-vote {
  flex: none; min-height: 36px; padding: 0 14px; border-radius: 999px; cursor: pointer;
  font-size: 13px; font-weight: 700; font-family: inherit;
  background: #fbfcfd; color: #111111; border: none;
}
.rmd-vote--done { background: #16a34a; color: #ffffff; }
.rmd-vote:disabled { cursor: default; }
.rmd-link { background: none; border: none; color: #6ea8fe; font-size: 13px; font-weight: 700; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; padding: 0; }
.rmd-empty {
  display: flex; flex-direction: column; align-items: center; gap: 12px; text-align: center;
  min-height: 180px; justify-content: center;
  border: 1px dashed #3a3b3f; border-radius: 16px; padding: 20px;
  font-size: 14px; color: #8a8f96;
}
.rmd-empty p { margin: 0; }
.rmd-empty__go { color: #6ea8fe; font-weight: 700; text-decoration: none; }
.rmd-err { font-size: 13px; color: #fca5a5; line-height: 1.6; }
.rmd-retry { margin-top: 6px; padding: 3px 12px; font-size: 12px; color: #fbfcfd; background: transparent; border: 1px solid currentColor; border-radius: 999px; cursor: pointer; opacity: 0.8; }
.rmd-skeleton { background: #1e1f21; border-radius: 10px; animation: rmd-pulse 1.4s ease-in-out infinite; }
@keyframes rmd-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
@media (max-width: 768px) {
  .rmd-page { margin: -20px -16px -120px; padding: 20px 16px 120px; }
}

html[data-theme='light'] .rmd-page { background: #f8f8f8; }
html[data-theme='light'] .rmd-topbar__title { color: #111111; }
html[data-theme='light'] .rmd-back { color: #111111; }
html[data-theme='light'] .rmd-back:active { background: rgba(0, 0, 0, 0.05); }
html[data-theme='light'] .rmd-watch {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rmd-backpill { border-color: #d4d4d8; color: #111111; }
html[data-theme='light'] .rmd-media { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rmd-media__poster, html[data-theme='light'] .rmd-media__ph { background: #f0f0f1; }
html[data-theme='light'] .rmd-media__ph { color: #9ca3af; }
html[data-theme='light'] .rmd-media__name { color: #111111; }
html[data-theme='light'] .rmd-media__meta { color: #6b7280; }
html[data-theme='light'] .rmd-media__tmdb { color: #2563eb; }
html[data-theme='light'] .rmd-media__watch {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rmd-pill { border-color: #e5e5e5; color: #6b7280; }
html[data-theme='light'] .rmd-pill--green { background: #e6f4ea; color: #16a34a; border-color: #16a34a; }
html[data-theme='light'] .rmd-pill--type { background: #f0f0f1; color: #55575c; border-color: #f0f0f1; }
html[data-theme='light'] .rmd-pill--mine { color: #2563eb; border-color: #2563eb; }
html[data-theme='light'] .rmd-pill--doing { color: #d97706; border-color: #f59e0b; }
html[data-theme='light'] .rmd-pill--done { color: #16a34a; border-color: #16a34a; }
html[data-theme='light'] .rmd-pill--rejected { color: #dc2626; border-color: #ef4444; }
html[data-theme='light'] .rmd-reqhead__title { color: #111111; }
html[data-theme='light'] .rmd-reqhead__count, html[data-theme='light'] .rmd-hint { color: #6b7280; }
html[data-theme='light'] .rmd-card { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rmd-card__time { color: #6b7280; }
html[data-theme='light'] .rmd-card__requester { color: #6b7280; }
html[data-theme='light'] .rmd-card__body { color: #111111; }
html[data-theme='light'] .rmd-card__sub, html[data-theme='light'] .rmd-card__votes { color: #6b7280; }
html[data-theme='light'] .rmd-card__note { color: #55575c; }
html[data-theme='light'] .rmd-vote { background: #111111; color: #ffffff; }
html[data-theme='light'] .rmd-vote--done { background: #16a34a; color: #ffffff; }
html[data-theme='light'] .rmd-link { color: #2563eb; }
html[data-theme='light'] .rmd-empty { border-color: #d4d4d8; color: #8a8f96; }
html[data-theme='light'] .rmd-empty__go { color: #2563eb; }
html[data-theme='light'] .rmd-err { color: #dc2626; }
html[data-theme='light'] .rmd-retry { color: #111111; }
html[data-theme='light'] .rmd-skeleton { background: #f0f0f1; }
</style>
