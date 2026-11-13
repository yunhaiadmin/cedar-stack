<template>
  <!-- 求片表单（页签内平铺）：搜索框＋TMDB 候选＋选季＋原名兜底；逻辑与旧许愿池
       弹窗同口径（在库校验/额度/季选/重名改附议），缺集提示条与举报口不平移。 -->
  <div class="rf-seek">
    <div class="rf-search">
      <span class="rf-search__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="M15.5 15.5 21 21" />
        </svg>
      </span>
      <input
        ref="seekInput"
        v-model="wishText"
        class="rf-search__input"
        type="text"
        placeholder="输入电影或剧集名称"
        maxlength="30"
        aria-label="片名"
        @keyup.enter="submitSeek"
        @input="onWishInput"
      />
      <button
        type="button"
        class="rf-search__btn"
        :disabled="searching"
        aria-label="搜索"
        title="搜索"
        @click="runSearchNow"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="M15.5 15.5 21 21" />
        </svg>
      </button>
    </div>
    <div v-if="emptyWarn" class="rf-hint rf-hint--err">先输入片名，再提交</div>

    <div v-if="showCandidates" class="rf-cands">
      <div v-if="searching" class="rf-cands__status">正在搜索片名…</div>
      <div v-else-if="searchError" class="rf-cands__error">
        <span>{{ searchError }}</span>
        <button type="button" class="rf-link" @click="retrySearch">重试搜索</button>
      </div>
      <template v-else>
        <div v-if="searchDegraded" class="rf-cands__degraded">搜索结果可能不全</div>
        <div v-if="!candidates.length" class="rf-cands__empty">没找到，换个片名试试</div>
        <div class="rf-cand-list">
          <div
            v-for="c in candidates"
            :key="c.kind + ':' + c.tmdb_id"
            class="rf-cand"
          >
            <button type="button" class="rf-cand__pick" @click="selectCandidate(c)">
              <img v-if="c.poster_url" :src="imageAuthUrl(c.poster_url, 160)" :alt="c.name" class="rf-cand__poster" loading="lazy" decoding="async" />
              <span v-else class="rf-cand__ph" aria-hidden="true">🎬</span>
              <span class="rf-cand__main">
                <span class="rf-cand__titlerow">
                  <b>{{ c.name }}</b>
                  <span class="rf-cand__kind">{{ c.kind === 'movie' ? '电影' : '剧集' }}</span>
                  <span class="rf-chip" :class="c.in_library ? 'rf-chip--green' : 'rf-chip--gray'">{{ c.in_library ? '✓ 库内已有' : '库内暂无' }}</span>
                </span>
                <span class="rf-cand__meta">{{ c.name }} / {{ c.year || '年份未知' }}</span>
              </span>
            </button>
            <button
              v-if="candState(c) === 'existing'"
              type="button"
              class="rf-cand__view"
              @click="goExisting(c)"
            >查看已有请求 ›</button>
            <button
              v-else-if="candState(c) === 'go'"
              type="button"
              class="rf-cand__go"
              @click="selectCandidate(c)"
            >发起求片</button>
            <span v-else class="rf-cand__have">媒体库已有</span>
          </div>
        </div>
      </template>
    </div>

    <div v-if="picked" class="rf-picked">
      <img v-if="picked.poster_url" :src="imageAuthUrl(picked.poster_url, 160)" :alt="picked.name" class="rf-picked__poster" loading="lazy" decoding="async" />
      <span v-else class="rf-picked__ph" aria-hidden="true">🎬</span>
      <div class="rf-picked__info">
        <b>{{ picked.name }}</b>
        <span class="rf-picked__meta">{{ picked.year }} · {{ picked.kind === 'movie' ? '电影' : '剧集' }}</span>
      </div>
      <button
        v-if="picked.in_library && !wantedIds.has(picked.tmdb_id)"
        type="button"
        class="rf-picked__want"
        :disabled="wanting === picked.tmdb_id"
        @click="doWant(picked)"
      >{{ wanting === picked.tmdb_id ? '想看中…' : '想看' }}</button>
      <span v-else-if="picked.in_library" class="rf-picked__wanted">已想看</span>
      <button type="button" class="rf-picked__change" @click="clearPick">换一部</button>
    </div>

    <div v-if="picked && picked.kind === 'series' && !picked.in_library && (seasons.length > 0 || loadingSeasons || seasonsLoaded || seasonsError !== '')" class="rf-seasons">
      <div v-if="seasons.length > 0" class="rf-seasons__label">选季（可多选）</div>
      <div v-if="seasons.length > 0" class="rf-seasons__row">
        <button type="button" class="rf-chipbtn" :class="{ on: seasonAll }" @click="toggleSeasonAll">全季</button>
        <button
          v-for="s in seasons"
          :key="s.season_number"
          type="button"
          class="rf-chipbtn"
          :class="{ on: chosenSeasons.includes(s.season_number) }"
          @click="toggleSeason(s.season_number)"
        >第{{ s.season_number }}季（共{{ s.episode_count }}集）</button>
      </div>
      <div v-if="loadingSeasons" class="rf-seasons__loading">正在加载季列表…</div>
      <div v-else-if="seasonsError !== ''" class="rf-seasons__error">
        <span>季列表没取到：{{ seasonsError }}</span>
        <button type="button" class="rf-chipbtn" @click="retrySeasons">重试</button>
      </div>
      <div v-else-if="seasons.length === 0" class="rf-seasons__empty">这部剧没有季数据，将按全季提交</div>
    </div>

    <div v-if="nameDup" class="rf-dup">
      <template v-if="nameDup.kind === 'mine'">你已经提交过《{{ nameDup.row.movie_name }}》，正在处理中</template>
      <template v-else>已有《{{ nameDup.row.movie_name }}》的求片（{{ (nameDup.row.vote_count || 0) + 1 }} 人求片），不用重复提交，给它 +1 就好</template>
    </div>
    <div v-else-if="fallbackAllowed" class="rf-fallback">没找到对应的片？就按「{{ wishText.trim() }}」这个名字提交，会人工确认</div>
    <div v-if="pickWarn" class="rf-hint rf-hint--err">点上面任意一部就能提交；没有你要的，换个片名再搜</div>

    <button type="button" class="rf-submit" :disabled="throwing" @click="submitSeek">{{ primaryBtnText }}</button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  mediaSeekApi, storeApi,
  type HotMediaSeekRequest, type MediaSeekCandidate, type MediaSeekRequest, type TvSeason,
} from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

const props = defineProps<{
  /** 我的求片单（查重提示用，由父页拉取后传入） */
  myRequests: MediaSeekRequest[]
  /** 众人榜（查重提示用，由父页拉取后传入） */
  hotRequests: HotMediaSeekRequest[]
  /** 已想看的 tmdb 集合（父页在库候选「想看」后同步） */
  wantedIds: Set<string>
  /** 外部预填片名（/want?q= 深链）：父页透传 route.query.q */
  prefill?: string
}>()
const emit = defineEmits<{
  /** 提交完成：created=true 为新建（requestId 为新行 id）；created=false 为转附议/重名 */
  submitted: [payload: { created: boolean; requestId?: number; message?: string }]
  /** 已在库候选点了「想看」：父页同步 wantedIds */
  wanted: [tmdbId: string]
}>()

const router = useRouter()
const toast = useToast()
const wishText = ref('')
const emptyWarn = ref(false)
const throwing = ref(false)
const voting = ref<number | null>(null)
const seekInput = ref<HTMLInputElement | null>(null)

const searching = ref(false)
const searchError = ref('')
const searchStatus = ref<'idle' | 'searching' | 'done' | 'error' | 'unconfigured'>('idle')
const searchFor = ref('')
const pickWarn = ref(false)
const searchReady = ref(true)
const searchDegraded = ref(false)
const candidates = ref<MediaSeekCandidate[]>([])
const picked = ref<MediaSeekCandidate | null>(null)
const seasons = ref<TvSeason[]>([])
const loadingSeasons = ref(false)
const seasonsError = ref('')
const seasonsLoaded = ref(false)
const seasonAll = ref(true)
const chosenSeasons = ref<number[]>([])
const wanting = ref('')

let searchTimer: ReturnType<typeof setTimeout> | undefined
let searchSeq = 0

const showCandidates = computed(() => (
  !picked.value && searchReady.value
  && (searching.value || searchError.value !== '' || candidates.value.length > 0)
))
const fallbackAllowed = computed(() => {
  if (picked.value) return false
  const q = wishText.value.trim()
  if (!q || searchFor.value !== q) return false
  if (searchStatus.value === 'unconfigured' || searchStatus.value === 'error') return true
  return searchStatus.value === 'done' && candidates.value.length === 0
})
function normTitle(s: string): string {
  return s.toLowerCase().replace(/[《》\s·•:：,，.。!！?？;；'"“”‘’()（）\-—]/g, '')
}
/* 候选卡三态动作（照友站）：已有请求（我或他人在这片有处理中求片）→查看已有请求；
   库内暂无且无请求→发起求片；库内已有且无请求→纯文字「媒体库已有」。
   已有请求判定与 nameDup 同口径：tmdb_id 对上或归一化片名对上（我的限处理中）。 */
function existingRowFor(c: MediaSeekCandidate): boolean {
  const key = normTitle(c.name)
  const tmdb = (c.tmdb_id || '').trim()
  const hit = (rowTmdb: string | null | undefined, rowName: string) =>
    (!!tmdb && (rowTmdb || '').trim() === tmdb) || (!!key && normTitle(rowName) === key)
  if (props.myRequests.some(
    (w) => (w.status === 'pending' || w.status === 'approved') && hit(w.tmdb_id, w.movie_name),
  )) return true
  return props.hotRequests.some(
    (w) => hit((w as HotMediaSeekRequest & { tmdb_id?: string | null }).tmdb_id, w.movie_name),
  )
}
function candState(c: MediaSeekCandidate): 'existing' | 'go' | 'have' {
  if (existingRowFor(c)) return 'existing'
  return c.in_library ? 'have' : 'go'
}
function goExisting(c: MediaSeekCandidate): void {
  void router.push({
    path: '/game/requests/media',
    query: { tmdb: c.tmdb_id, name: c.name, year: c.year, type: c.kind },
  })
}
const nameDup = computed<{ kind: 'mine' | 'hot'; row: MediaSeekRequest | HotMediaSeekRequest } | null>(() => {
  if (picked.value) return null
  const key = normTitle(wishText.value)
  if (!key) return null
  const mine = props.myRequests.find(
    (w) => (w.status === 'pending' || w.status === 'approved') && normTitle(w.movie_name) === key,
  )
  if (mine) return { kind: 'mine', row: mine }
  const hot = props.hotRequests.find((w) => !w.mine && normTitle(w.movie_name) === key)
  if (hot) return { kind: 'hot', row: hot }
  return null
})
const primaryBtnText = computed(() => {
  if (throwing.value) return '提交中'
  if (picked.value && picked.value.in_library) return '已在库，去播放器看'
  if (nameDup.value?.kind === 'hot') return '给它 +1 附议'
  return '提交求片'
})
const seasonParam = computed<string | undefined>(() => {
  const p = picked.value
  if (!p || p.kind !== 'series' || p.in_library) return undefined
  if (seasons.value.length === 0) return undefined
  if (seasonAll.value) return 'all'
  if (chosenSeasons.value.length === 0) return undefined
  return [...chosenSeasons.value].sort((a, b) => a - b).join(',')
})

function onWishInput(): void {
  emptyWarn.value = false
  pickWarn.value = false
  picked.value = null
  seasons.value = []
  invalidateSeasons()
  chosenSeasons.value = []
  seasonAll.value = true
  if (searchTimer !== undefined) { clearTimeout(searchTimer); searchTimer = undefined }
  const q = wishText.value.trim()
  if (!q) {
    searchSeq++
    candidates.value = []
    searching.value = false
    searchError.value = ''
    searchStatus.value = 'idle'
    searchFor.value = ''
    return
  }
  searchTimer = setTimeout(() => { void runSearch(q) }, 350)
}
async function runSearch(q: string): Promise<void> {
  const seq = ++searchSeq
  searching.value = true
  searchError.value = ''
  searchStatus.value = 'searching'
  searchFor.value = q
  try {
    const r = await mediaSeekApi.search(q)
    if (seq !== searchSeq) return
    if (q !== wishText.value.trim()) return
    searchReady.value = r.configured
    searchDegraded.value = r.degraded === true
    candidates.value = r.configured ? (r.results || []) : []
    searchStatus.value = r.configured ? 'done' : 'unconfigured'
  } catch (e) {
    if (seq !== searchSeq) return
    searchStatus.value = 'error'
    searchError.value = humanizeError(e)
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}
function runSearchNow(): void {
  const q = wishText.value.trim()
  if (!q) { emptyWarn.value = true; return }
  if (searchTimer !== undefined) { clearTimeout(searchTimer); searchTimer = undefined }
  void runSearch(q)
}
function retrySearch(): void {
  const q = wishText.value.trim()
  if (q) void runSearch(q)
}
function selectCandidate(c: MediaSeekCandidate): void {
  if (searchTimer !== undefined) { clearTimeout(searchTimer); searchTimer = undefined }
  pickWarn.value = false
  picked.value = c
  candidates.value = []
  searching.value = false
  searchError.value = ''
  invalidateSeasons()
  if (c.kind === 'series' && !c.in_library) void loadSeasons(c.tmdb_id)
}
let seasonsSeq = 0
async function loadSeasons(tmdbId: string): Promise<void> {
  const seq = ++seasonsSeq
  loadingSeasons.value = true
  seasonsError.value = ''
  try {
    const r = await mediaSeekApi.seasons(tmdbId)
    if (seq !== seasonsSeq) return
    seasons.value = r.seasons || []
    seasonsLoaded.value = true
  } catch (e) {
    if (seq !== seasonsSeq) return
    seasons.value = []
    seasonsLoaded.value = false
    seasonsError.value = humanizeError(e)
  } finally {
    if (seq === seasonsSeq) loadingSeasons.value = false
  }
}
function retrySeasons(): void {
  const p = picked.value
  if (p && p.kind === 'series' && !p.in_library) void loadSeasons(p.tmdb_id)
}
function invalidateSeasons(): void {
  seasonsSeq++
  loadingSeasons.value = false
  seasonsLoaded.value = false
  seasonsError.value = ''
}
function clearPick(): void {
  picked.value = null
  seasons.value = []
  invalidateSeasons()
  chosenSeasons.value = []
  seasonAll.value = true
  const q = wishText.value.trim()
  if (q) void runSearch(q)
}
function toggleSeasonAll(): void { seasonAll.value = true; chosenSeasons.value = [] }
function toggleSeason(n: number): void {
  seasonAll.value = false
  const i = chosenSeasons.value.indexOf(n)
  if (i >= 0) chosenSeasons.value.splice(i, 1)
  else chosenSeasons.value.push(n)
  if (chosenSeasons.value.length === 0) seasonAll.value = true
}
async function doWant(c: MediaSeekCandidate): Promise<void> {
  if (wanting.value) return
  wanting.value = c.tmdb_id
  try {
    const r = await storeApi.postWant(c.tmdb_id)
    toast.success(r.message || '已加入想看')
    if (r.state !== 'failed' && r.state !== 'missing_source') emit('wanted', c.tmdb_id)
  } catch (e) {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    toast.error(typeof d === 'string' ? d : humanizeError(e))
  } finally {
    wanting.value = ''
  }
}

async function submitSeek(): Promise<void> {
  if (picked.value && picked.value.in_library) {
    resetForm()
    void router.push('/game/operator')
    return
  }
  if (throwing.value) return
  if (!picked.value && nameDup.value?.kind === 'hot') {
    const row = nameDup.value.row as HotMediaSeekRequest
    throwing.value = true
    try {
      const r = await mediaSeekApi.vote(row.id)
      if (r.voted) {
        resetForm()
        toast.success('已附议')
        emit('submitted', { created: false, message: '已附议' })
      }
    } catch {
      toast.error('附议失败，再试一次')
    } finally {
      throwing.value = false
    }
    return
  }
  if (!picked.value && nameDup.value?.kind === 'mine') {
    toast.error('这条求片你已经提交过了')
    return
  }
  const name = picked.value ? picked.value.name : wishText.value.trim()
  if (!name) { emptyWarn.value = true; return }
  if (!picked.value && !fallbackAllowed.value) {
    if (candidates.value.length > 0) { pickWarn.value = true; return }
    if (searchTimer !== undefined) { clearTimeout(searchTimer); searchTimer = undefined }
    void runSearch(name)
    return
  }
  throwing.value = true
  try {
    const p = picked.value
    const payload = p
      ? {
          movie_name: p.name,
          year: p.year,
          type: p.kind,
          tmdb_id: p.tmdb_id,
          ...(seasonParam.value ? { season: seasonParam.value } : {}),
        }
      : { movie_name: name }
    const r = await mediaSeekApi.create(payload)
    const created = r.created !== false
    const requestId = created ? r.request_id : undefined
    resetForm()
    toast.success(r.message || '求片已提交')
    emit('submitted', { created, requestId, message: r.message })
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    throwing.value = false
  }
}
function resetForm(): void {
  wishText.value = ''
  emptyWarn.value = false
  searchSeq++
  if (searchTimer !== undefined) { clearTimeout(searchTimer); searchTimer = undefined }
  searching.value = false
  searchError.value = ''
  searchStatus.value = 'idle'
  searchFor.value = ''
  pickWarn.value = false
  candidates.value = []
  picked.value = null
  seasons.value = []
  invalidateSeasons()
  chosenSeasons.value = []
  seasonAll.value = true
  searchReady.value = true
  searchDegraded.value = false
}
/* 外部预填（/want?q= 深链）：父页透传 query.q，填入后立即搜索 */
watch(
  () => props.prefill,
  (q) => {
    if (typeof q === 'string' && q.trim()) {
      wishText.value = q.trim()
      void runSearch(q.trim())
      seekInput.value?.focus()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
/* 求片表单：缺省深色（§6.4），html[data-theme='light'] 时换浅（§6.1）。
   搜索框约 50 高圆角约 25；搜索钮为圆形彩虹钮（原站发起请求搜索框形态，
   2026-11-13 六图）：50×50 圆、白底＋淡彩虹渐变边框＋放大镜图标。 */
.rf-seek { display: flex; flex-direction: column; gap: 12px; }
.rf-search { display: flex; gap: 10px; position: relative; }
/* 输入框内左侧放大镜（照友站）：图标随输入框同行居中，输入左内边距让位 */
.rf-search__icon {
  position: absolute; left: 16px; top: 0; height: 50px;
  display: inline-flex; align-items: center; pointer-events: none;
  color: #9ca3af;
}
.rf-search__input {
  flex: 1; min-width: 0; height: 50px; padding: 0 18px 0 42px;
  font-size: 15px; color: #fbfcfd;
  background: #242526; border: 1px solid #232426; border-radius: 25px; outline: none;
}
.rf-search__input::placeholder { color: #9ca3af; }
.rf-search__input:focus { border-color: #6b7280; }
.rf-search__btn {
  flex: none; width: 50px; height: 50px; border-radius: 50%;
  color: #111111; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1.5px solid transparent;
  background: linear-gradient(#ffffff, #ffffff) padding-box, var(--uhd-rainbow-border) border-box;
}
.rf-search__btn:disabled { opacity: 0.6; cursor: default; }
.rf-hint { font-size: 12px; color: #fca5a5; }
.rf-hint--err { color: #fca5a5; }
.rf-cands { display: flex; flex-direction: column; gap: 8px; }
.rf-cands__status, .rf-cands__empty, .rf-cands__degraded { font-size: 13px; color: #9ca3af; }
.rf-cands__error { font-size: 13px; color: #fca5a5; display: flex; gap: 8px; align-items: center; }
.rf-link { background: none; border: none; color: #6ea8fe; font-size: 13px; cursor: pointer; text-decoration: underline; }
.rf-cand-list { display: flex; flex-direction: column; gap: 8px; max-height: 320px; overflow-y: auto; }
/* 候选卡（照友站搜索结果态）：海报＋标题行（类型标签＋库内 pill）＋「标题 / 年份」行，
   右侧三态动作（查看已有请求描边胶囊／发起求片彩虹钮／媒体库已有纯文字） */
.rf-cand {
  display: flex; align-items: center; gap: 10px;
  background: #17181a; border: 1px solid #26262a; border-radius: 12px;
  padding: 8px 10px; width: 100%; color: #fbfcfd;
}
.rf-cand__pick {
  flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; text-align: left;
  background: none; border: none; padding: 0; cursor: pointer; color: inherit; font-family: inherit;
}
.rf-cand__pick:active { transform: scale(0.98); }
.rf-cand__poster { width: 40px; height: 56px; object-fit: cover; border-radius: 8px; flex-shrink: 0; border: 1px solid #26262a; }
.rf-cand__ph { width: 40px; height: 56px; display: flex; align-items: center; justify-content: center; font-size: 20px; background: #1e1f21; border-radius: 8px; flex-shrink: 0; border: 1px solid #26262a; }
.rf-cand__main { flex: 1; min-width: 0; }
.rf-cand__titlerow { display: flex; align-items: center; gap: 6px; }
.rf-cand__titlerow b { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rf-cand__kind {
  flex: none; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 6px;
  background: #26262a; color: #9ca3af;
}
.rf-cand__titlerow .rf-chip { flex: none; }
.rf-cand__meta { font-size: 11px; color: #9ca3af; margin-top: 4px; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rf-chip { font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; white-space: nowrap; }
.rf-chip--gray { background: #26262a; color: #9ca3af; border: 1px solid #333336; }
.rf-chip--green { background: rgba(22, 163, 74, 0.15); color: #4ade80; border: 1px solid rgba(22, 163, 74, 0.5); }
.rf-cand__view {
  flex: none; height: 36px; padding: 0 14px; border-radius: 999px; cursor: pointer;
  background: transparent; border: 1.5px solid #3a3b3f; color: #fbfcfd;
  font-size: 12px; font-weight: 700; font-family: inherit; white-space: nowrap;
}
.rf-cand__go {
  flex: none; height: 36px; padding: 0 14px; border-radius: 999px; cursor: pointer;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box;
  color: #ffffff; font-size: 12px; font-weight: 800; font-family: inherit; white-space: nowrap;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 8px 20px -10px rgba(0, 0, 0, 0.55);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.rf-cand__have { flex: none; font-size: 12px; color: #9ca3af; white-space: nowrap; }
.rf-picked {
  display: flex; align-items: center; gap: 10px;
  background: #1e1f21; border: 1.5px solid #3a3b3f; border-radius: 12px; padding: 8px 10px;
}
.rf-picked__poster { width: 40px; height: 56px; object-fit: cover; border-radius: 8px; flex-shrink: 0; border: 1px solid #26262a; }
.rf-picked__ph { width: 40px; height: 56px; display: flex; align-items: center; justify-content: center; font-size: 20px; background: #26262a; border-radius: 8px; flex-shrink: 0; }
.rf-picked__info { flex: 1; min-width: 0; }
.rf-picked__info b { display: block; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rf-picked__meta { font-size: 11px; color: #9ca3af; margin-top: 2px; display: block; }
.rf-picked__want { font-size: 12px; font-weight: 700; color: #111; background: #fbfcfd; border: 1px solid #fbfcfd; border-radius: 20px; padding: 4px 10px; cursor: pointer; white-space: nowrap; }
.rf-picked__want:disabled { opacity: 0.6; cursor: default; }
.rf-picked__wanted { font-size: 12px; color: #9ca3af; white-space: nowrap; }
.rf-picked__change { font-size: 12px; color: #6ea8fe; background: transparent; border: 1px solid #6ea8fe; border-radius: 20px; padding: 4px 10px; cursor: pointer; white-space: nowrap; }

.rf-seasons { display: flex; flex-direction: column; gap: 8px; }
.rf-seasons__label { font-size: 13px; font-weight: 700; color: #fbfcfd; }
.rf-seasons__row { display: flex; flex-wrap: wrap; gap: 8px; }
.rf-chipbtn {
  font-size: 12px; padding: 6px 12px; border-radius: 20px; cursor: pointer;
  background: #1e1f21; color: #d4d4d8; border: 1px solid #333336;
}
.rf-chipbtn.on { background: #fbfcfd; color: #111111; border-color: #fbfcfd; font-weight: 700; }
.rf-seasons__loading, .rf-seasons__empty { font-size: 12px; color: #9ca3af; }
.rf-seasons__error { font-size: 12px; color: #fca5a5; display: flex; gap: 8px; align-items: center; }
.rf-dup {
  font-size: 12px; color: #fbfcfd; background: #1e1f21;
  border: 1px dashed #333336; border-radius: 10px; padding: 8px 10px;
}
.rf-fallback { font-size: 12px; color: #9ca3af; }
/* 主按钮：招牌配方（彩虹边＋玻璃栈，同 .btn-primary 浅深两态），替代旧实色底 */
.rf-submit {
  width: 100%; height: 50px; border-radius: 25px; cursor: pointer;
  font-size: 15px; font-weight: 800; color: #ffffff;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 12px 28px -12px rgba(0, 0, 0, 0.55);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.rf-submit:disabled { opacity: 0.5; cursor: default; }
html[data-theme='light'] .rf-search__input { background: #f0f0f1; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-search__input:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-hint--err, html[data-theme='light'] .rf-cands__error, html[data-theme='light'] .rf-seasons__error { color: #dc2626; }
html[data-theme='light'] .rf-cands__status, html[data-theme='light'] .rf-cands__empty, html[data-theme='light'] .rf-cands__degraded, html[data-theme='light'] .rf-cand__meta, html[data-theme='light'] .rf-picked__meta, html[data-theme='light'] .rf-seasons__loading, html[data-theme='light'] .rf-seasons__empty, html[data-theme='light'] .rf-fallback { color: #6b7280; }
html[data-theme='light'] .rf-link { color: #2563eb; }
html[data-theme='light'] .rf-cand { background: #ffffff; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-cand__kind { background: #f0f0f1; color: #6b7280; }
html[data-theme='light'] .rf-cand__view { border-color: #d4d4d8; color: #111111; }
html[data-theme='light'] .rf-cand__go {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rf-cand__have { color: #6b7280; }
html[data-theme='light'] .rf-cand__poster, html[data-theme='light'] .rf-picked__poster { border-color: #e5e5e5; }
html[data-theme='light'] .rf-cand__ph, html[data-theme='light'] .rf-picked__ph { background: #f5f5f5; border-color: #e5e5e5; }
html[data-theme='light'] .rf-chip--gray { background: #f5f5f5; color: #6b7280; border-color: #e5e5e5; }
html[data-theme='light'] .rf-chip--green { background: #e8f0e9; color: #16a34a; border-color: #16a34a; }
html[data-theme='light'] .rf-picked { background: #f9f9f9; border-color: #e5e5e5; }
html[data-theme='light'] .rf-picked__want { background: #111111; color: #ffffff; border-color: #111111; }
html[data-theme='light'] .rf-picked__wanted { color: #6b7280; }
html[data-theme='light'] .rf-picked__change { color: #2563eb; border-color: #2563eb; }
html[data-theme='light'] .rf-seasons__label { color: #111111; }
html[data-theme='light'] .rf-chipbtn { background: #f5f5f5; color: #55575c; border-color: #e5e5e5; }
html[data-theme='light'] .rf-chipbtn.on { background: #111111; color: #ffffff; border-color: #111111; }
html[data-theme='light'] .rf-dup { background: #f9f9f9; border-color: #d4d4d8; color: #111111; }
html[data-theme='light'] .rf-submit {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
</style>
