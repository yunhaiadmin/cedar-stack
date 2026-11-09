<template>
  <div class="v4-scope">
    <div class="v4-page">
      <!-- 日志头 -->
      <header class="v4-loghead">
        <span class="v4-stamp">{{ facilityStamp('wishlist') }}</span>
        <h1>许愿池</h1>
        <p>营地中央的古井 —— 把心愿写在纸上，折成小船，投进去。</p>
      </header>

      <!-- 古井 -->
      <section class="v4-sec">
        <div class="well-wrap">
          <div ref="wellRef" class="well" aria-hidden="true">
            <div class="moon">🌙</div>
            <div class="ripple r1"></div>
            <div class="ripple r2"></div>
            <div class="ripple r3"></div>
            <div class="boat-float" style="left:52px;top:88px">⛵</div>
            <div class="boat-float" style="left:130px;top:120px;animation-delay:1.3s;font-size:20px">⛵</div>
          </div>
          <div class="well-note">
            <!-- R43：加载中不写"0 条"（列表还没回来，0 是假的），等数出来了再报数 -->
            <!-- R102-5：双列表都失败时也不写"0 条"，说人话 -->
            <template v-if="myLoading || hotLoading">池里有几条心愿在漂流，正在数…</template>
            <template v-else-if="myError && hotError">池子里的心愿暂时数不出来，看看下面的列表吧</template>
            <template v-else>池里已有 <b>{{ totalWishes }}</b> 条心愿在漂流</template>
          </div>
        </div>
        <button class="wish-btn" @click="openModal">🏮 投下心愿</button>
      </section>

      <!-- 我的许愿 -->
      <section class="v4-sec">
        <div class="v4-sec-title">我的许愿</div>
        <div class="v4-sec-sub">漂流中的心愿，营地正在想办法捞起来</div>
        <div v-if="myLoading" class="v4-skeleton" style="height:80px"></div>
        <div v-else-if="myError" class="v4-err">许愿加载失败：{{ myError }}<br><button class="v4-retry" @click="loadMine">重试</button></div>
        <div v-else-if="myWishes.length === 0" class="v4-empty">池子里还没有你的心愿，投一条下去吧</div>
        <div v-else>
          <div
            v-for="w in myWishes"
            :key="w.id"
            class="wish"
            :class="{ new: w.id === justAddedId }"
          >
            <div>
              <b>《{{ w.movie_name }}》</b>
              <div class="wmeta">
                {{ shortDate(w.created_at) }} 投下
                <span v-if="w.status === 'completed' && w.emby_item_id">
                  · 营地已上架，<button type="button" class="goto-player" @click="goOperator">去播放器看</button>
                </span>
                <span v-else-if="w.vote_count"> · 已有 {{ w.vote_count }} 人附议（加权 {{ w.weighted_vote_count ?? w.vote_count }}）</span>
              </div>
            </div>
            <span class="chip" :class="statusChip(w.status)">{{ statusText(w.status) }}</span>
          </div>
        </div>
      </section>

      <!-- 我的想看：想看页合并进来——库内片「想看」转存到自己盘的任务行；
           商店未开启/非会员时整区隐藏（许愿池本身不挂商店闸） -->
      <section v-if="!wantsHidden" class="v4-sec">
        <div class="v4-sec-title">我的想看</div>
        <div class="v4-sec-sub">想看的片子，正在往你的盘里搬</div>
        <div v-if="wantsLoading" class="v4-skeleton" style="height:80px"></div>
        <div v-else-if="wantsError" class="v4-err">想看清单加载失败：{{ wantsError }}<br><button class="v4-retry" @click="loadWants">重试</button></div>
        <div v-else-if="myWants.length === 0" class="v4-empty">还没有想看的片子，搜到已在库的片子时点「想看」试试</div>
        <div v-else>
          <div v-for="w in myWants" :key="w.job_id ?? w.tmdb_id" class="wish">
            <img v-if="w.poster_url" :src="imageAuthUrl(w.poster_url)" :alt="w.title || w.tmdb_id" class="want-poster">
            <div class="want-main">
              <b>《{{ w.title || `TMDB ${w.tmdb_id}` }}》</b>
              <div class="wmeta">{{ w.message }}</div>
            </div>
            <span class="chip" :class="wantChip(w.state)">{{ wantStateText(w.state) }}</span>
          </div>
        </div>
      </section>

      <!-- 众人许愿 -->
      <section class="v4-sec" style="padding-bottom:20px">
        <div class="v4-sec-title">众人许愿</div>
        <div class="v4-sec-sub">附议多的心愿，优先被捞起来</div>
        <div v-if="hotLoading" class="v4-skeleton" style="height:80px"></div>
        <div v-else-if="hotError" class="v4-err">众人许愿加载失败：{{ hotError }}<br><button class="v4-retry" @click="loadHot">重试</button></div>
        <div v-else-if="hotWishes.length === 0" class="v4-empty">大家的心愿都在漂流，暂时没有公开的</div>
        <div v-else>
          <div v-for="w in hotWishes" :key="w.id" class="crowd">
            <div class="row1">
              <b>《{{ w.movie_name }}》</b>
              <span class="chip blue">{{ w.vote_count > 0 ? w.vote_count + ' 人也想看 · 加权 ' + (w.weighted_vote_count ?? w.vote_count) : '还没有人附议' }}</span>
            </div>
            <div class="want">{{ shortDate(w.created_at) }} 投入池中</div>
            <!-- 举报（想看页合并进来；仅公益服）：hot 榜后端只回 pending 行，
                 id 即求片行 id，与治理举报的目标 id 同域 -->
            <div v-if="welfareRealm" class="report-line">
              <a
                v-if="!reportedIds.has(w.id)"
                href="#"
                class="report-link"
                @click.prevent="openReport(w.id)"
              >举报</a>
              <span v-else class="report-done">已举报</span>
            </div>
            <button
              class="upvote"
              :class="{ done: w.voted }"
              :disabled="w.voted || voting !== null"
              @click="upvote(w)"
            >
              <span class="lbl">{{ w.voted ? '✓ 已附议，坐等打捞' : voting === w.id ? '附议中' : '+1 附议' }}</span>
            </button>
            <div v-if="reportingId === w.id" class="report-form">
              <select v-model="reportReason" class="report-input" aria-label="举报原因">
                <option value="spam">垃圾内容</option>
                <option value="duplicate">重复求片</option>
                <option value="violation">违规内容</option>
              </select>
              <input
                v-model="reportDetail"
                type="text"
                placeholder="补充说明（可选）"
                class="report-input"
                maxlength="500"
              />
              <button class="report-submit" :disabled="reportBusy" @click="submitReport(w.id)">{{ reportBusy ? '提交中…' : '提交举报' }}</button>
              <button class="report-cancel" :disabled="reportBusy" @click="reportingId = null">取消</button>
              <div v-if="reportErr" class="v4-err" style="flex:1 1 100%">{{ reportErr }}</div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 许愿弹窗 -->
    <div class="modal" :class="{ show: modalOpen }" role="dialog" aria-modal="true" aria-label="许愿" tabindex="-1" @click.self="closeModal" @keydown="onModalKeydown">
      <div class="mcard">
        <h3>写下你的心愿</h3>
        <p>写下片名，纸条会折成小船，噗通一声投进池子</p>
        <input
          ref="wishInput"
          v-model="wishText"
          placeholder="比如：《深海之间》"
          maxlength="30"
          @keyup.enter="throwWish"
          @input="onWishInput"
        >
        <div v-if="emptyWarn" class="wish-warn">先写下心愿，再投进池子吧 <span aria-hidden="true">✍️</span></div>

        <div v-if="showCandidates" class="seek-cands">
          <div v-if="searching" class="seek-status">正在池子里捞片名…<span aria-hidden="true">🎣</span></div>
          <div v-else-if="searchError" class="seek-error">
            <span>{{ searchError }}</span>
            <button type="button" class="seek-retry" @click="retrySearch">重试搜索</button>
          </div>
          <template v-else>
            <div v-if="searchDegraded" class="seek-degraded">搜索结果可能不全</div>
            <div v-if="!candidates.length" class="seek-empty">没找到，换个片名试试</div>
            <div class="cand-list">
              <button
                v-for="c in candidates"
                :key="c.kind + ':' + c.tmdb_id"
                type="button"
                class="cand"
                @click="selectCandidate(c)"
              >
                <img v-if="c.poster_url" :src="imageAuthUrl(c.poster_url)" :alt="c.name" class="cand-poster">
                <span v-else class="cand-ph" aria-hidden="true">🎬</span>
                <span class="cand-main">
                  <b>{{ c.name }}</b>
                  <span class="cand-meta">{{ c.year }} · {{ c.kind === 'movie' ? '电影' : '剧集' }}</span>
                </span>
                <span class="cand-badges">
                  <span v-if="c.rating" class="chip gray">★ {{ c.rating }}</span>
                  <span v-if="c.in_library" class="chip green">已在库</span>
                </span>
              </button>
            </div>
          </template>
        </div>

        <div v-if="picked" class="picked-card">
          <img v-if="picked.poster_url" :src="imageAuthUrl(picked.poster_url)" :alt="picked.name" class="picked-poster">
          <span v-else class="picked-ph" aria-hidden="true">🎬</span>
          <div class="picked-info">
            <b>{{ picked.name }}</b>
            <span class="picked-meta">{{ picked.year }} · {{ picked.kind === 'movie' ? '电影' : '剧集' }}</span>
          </div>
          <!-- 已在库：想看页合并进来的「想看」动作（与原想看页同一个 postWant 口） -->
          <button
            v-if="picked.in_library && !wantedIds.has(picked.tmdb_id)"
            type="button"
            class="picked-want"
            :disabled="wanting === picked.tmdb_id"
            @click="doWant(picked)"
          >{{ wanting === picked.tmdb_id ? '想看中…' : '想看' }}</button>
          <span v-else-if="picked.in_library" class="picked-wanted">已想看</span>
          <button type="button" class="picked-change" @click="clearPick">换一部</button>
        </div>

        <div v-if="picked && picked.kind === 'series' && !picked.in_library && (seasons.length > 0 || loadingSeasons || seasonsLoaded || seasonsError !== '')" class="sels-box">
          <div v-if="seasons.length > 0" class="sels-label">选季（可多选）</div>
          <div v-if="seasons.length > 0" class="sels-row">
            <button
              type="button"
              class="sels-chip"
              :class="{ on: seasonAll }"
              @click="toggleSeasonAll"
            >全季</button>
            <button
              v-for="s in seasons"
              :key="s.season_number"
              type="button"
              class="sels-chip"
              :class="{ on: chosenSeasons.includes(s.season_number) }"
              @click="toggleSeason(s.season_number)"
            >第{{ s.season_number }}季（共{{ s.episode_count }}集）</button>
          </div>
          <div v-if="loadingSeasons" class="sels-loading">正在数有几季…</div>
          <div v-else-if="seasonsError !== ''" class="sels-error">
            <span>季列表没取到：{{ seasonsError }}</span>
            <button type="button" class="sels-chip" @click="retrySeasons">重试</button>
          </div>
          <div v-else-if="seasons.length === 0" class="sels-empty">这部剧没有季数据，将按全季投放</div>
        </div>

        <div v-if="hasTypedName" class="seek-fallback">没找到？就按「{{ wishText.trim() }}」这个名字投</div>

        <div class="mrow">
          <button class="mbtn cancel" @click="closeModal">再想想</button>
          <button class="mbtn go" :disabled="throwing" @click="throwWish">{{ primaryBtnText }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { facilityStamp } from '@/config/game'
import { mediaSeekApi, storeApi, type MediaSeekCandidate, type TvSeason, type MediaSeekRequest, type HotMediaSeekRequest, type StoreWantRecord, type StoreWantState } from '@/api'
import { governanceApi, type GovernanceReportReason } from '@/api/governance'
import { useUserStore } from '@/stores/user'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

const myWishes = ref<MediaSeekRequest[]>([])
const hotWishes = ref<HotMediaSeekRequest[]>([])
const myLoading = ref(true)
const hotLoading = ref(true)
const myError = ref('')
const hotError = ref('')

const modalOpen = ref(false)
const wishText = ref('')
const emptyWarn = ref(false)
const throwing = ref(false)
const voting = ref<number | null>(null)
const justAddedId = ref<number | null>(null)
const wellRef = ref<HTMLElement | null>(null)
const wishInput = ref<HTMLInputElement | null>(null)

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
/** 举报入口仅公益服：与补给站同款 userStore.isFreeRealm 信号，拿不到按非公益服处理 */
const welfareRealm = computed(() => userStore.isFreeRealm)
/** R45：营地已上架的心愿 → 去接线员页（播放器接线入口） */
function goOperator() {
  router.push('/game/operator')
}

// ---------- 我的想看（想看页合并进来：库内片想看转存任务） ----------
// 想看口挂商店闸（登录/会员/总闸）：闸没过时整区隐藏，许愿池照常；
// 其余失败亮错误态可重试，不静默吞。
const myWants = ref<StoreWantRecord[]>([])
const wantsLoading = ref(true)
const wantsError = ref('')
const wantsHidden = ref(false)
const wanting = ref('')
/** 已想看集合：我的想看清单 + 本页刚点过的（清单刷新前先本地记下，防重复点） */
const wantedIds = ref(new Set<string>())

function errStatus(err: unknown): number | null {
  const s = (err as { response?: { status?: number } })?.response?.status
  return typeof s === 'number' ? s : null
}
function errDetail(err: unknown): string {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof d === 'string' ? d : ''
}

function wantStateText(s: StoreWantState): string {
  return (
    {
      wanted: '准备中',
      resolving: '准备中',
      transferring: '准备中',
      ready: '可以看了',
      failed: '转存失败',
      missing_source: '库里还没有',
    }[s] ?? s
  )
}
function wantChip(s: StoreWantState): string {
  if (s === 'ready') return 'green'
  if (s === 'failed' || s === 'missing_source') return 'gray'
  return 'blue'
}

let wantsSeq = 0
async function loadWants() {
  const seq = ++wantsSeq
  wantsLoading.value = true
  wantsError.value = ''
  try {
    const r = await storeApi.getWants()
    if (seq !== wantsSeq) return
    myWants.value = r.items || []
    wantedIds.value = new Set([
      ...wantedIds.value,
      ...myWants.value
        .filter((w) => w.state !== 'failed' && w.state !== 'missing_source')
        .map((w) => w.tmdb_id),
    ])
  } catch (e) {
    if (seq !== wantsSeq) return
    const s = errStatus(e)
    if (s === 401 || s === 403 || (s === 404 && errDetail(e) === '想看商店未开启')) {
      wantsHidden.value = true
      return
    }
    wantsError.value = humanizeError(e)
  } finally {
    if (seq === wantsSeq) wantsLoading.value = false
  }
}

/** 已在库候选的「想看」：与原想看页同一个 postWant 口，点完刷新我的想看 */
async function doWant(c: MediaSeekCandidate) {
  if (wanting.value) return
  wanting.value = c.tmdb_id
  try {
    const r = await storeApi.postWant(c.tmdb_id)
    useToast().success(r.message || '已加入想看')
    if (r.state !== 'failed' && r.state !== 'missing_source') {
      wantedIds.value = new Set(wantedIds.value).add(c.tmdb_id)
    }
    void loadWants()
  } catch (e) {
    useToast().error(errDetail(e) || humanizeError(e))
  } finally {
    wanting.value = ''
  }
}

// ---------- 举报（仅公益服；原因与后端 REPORT_REASONS 逐字一致） ----------
// hot 榜行 id 就是 movie_requests 行 id（后端只回 pending 行），与老想看页
// 求片榜的 request_id 同域，直接作治理举报的 target_id。
const reportingId = ref<number | null>(null)
const reportReason = ref<GovernanceReportReason>('spam')
const reportDetail = ref('')
const reportBusy = ref(false)
const reportErr = ref('')
const reportedIds = ref(new Set<number>())

function openReport(requestId: number) {
  reportingId.value = requestId
  reportReason.value = 'spam'
  reportDetail.value = ''
  reportErr.value = ''
}

async function submitReport(requestId: number) {
  reportBusy.value = true
  reportErr.value = ''
  try {
    await governanceApi.createReport(
      requestId,
      reportReason.value,
      reportDetail.value.trim() || undefined,
    )
    reportedIds.value = new Set(reportedIds.value).add(requestId)
    reportingId.value = null
    useToast().success('举报已提交，待复核')
  } catch (err) {
    // 后端文案原样回：403 治理特权已被冻结、409 已举报过该求片、404 求片不存在
    reportErr.value = errDetail(err) || '举报失败，稍后再试'
  } finally {
    reportBusy.value = false
  }
}

// hot 接口返回"所有人 pending（含自己）"，与 myWishes 按 id 去重，避免自己的心愿被算两次
const totalWishes = computed(() => {
  const ids = new Set<string | number>()
  myWishes.value.forEach((w) => ids.add(w.id))
  hotWishes.value.forEach((w) => ids.add(w.id))
  return ids.size
})

function statusText(s: string): string {
  if (s === 'completed') return '已捞起'
  if (s === 'approved') return '打捞中'
  if (s === 'rejected') return '已退回'
  return '漂流中'
}
function statusChip(s: string): string {
  if (s === 'completed') return 'green'
  if (s === 'rejected') return 'gray'
  return 'blue'
}
function shortDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}-${d.getDate()}`
}
function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape' && modalOpen.value) closeModal()
}
/** R27：简易 focus trap —— Tab 在对话框内循环，不跑出对话框（已有 Esc + 自动聚焦） */
function onModalKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab' || !modalOpen.value) return
  const card = (e.currentTarget as HTMLElement).querySelector('.mcard')
  if (!card) return
  const els = Array.from(
    card.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => el.offsetParent !== null)
  if (els.length === 0) return
  const first = els[0]
  const last = els[els.length - 1]
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}
/** R38 P2-2：弹窗打开前记住触发按钮，关闭后焦点回到它 */
const lastTrigger = ref<HTMLElement | null>(null)
function openModal() {
  lastTrigger.value = document.activeElement as HTMLElement | null
  modalOpen.value = true
  // R38 P3-11：弹窗打开时锁住背后页面滚动
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onEsc)
  nextTick(() => wishInput.value?.focus())
}
/* 智能选片：输入防抖搜 TMDB 候选，选中后带 tmdb_id/year/type/season 提交 */
const searching = ref<boolean>(false)
const searchError = ref<string>('')
const searchReady = ref<boolean>(true)
const searchDegraded = ref<boolean>(false)
const candidates = ref<MediaSeekCandidate[]>([])
const picked = ref<MediaSeekCandidate | null>(null)
const seasons = ref<TvSeason[]>([])
const loadingSeasons = ref<boolean>(false)
const seasonsError = ref<string>('')
/** R102-4：季列表是否已成功拉取过（成功但 0 季也要亮出"按全季投放"说明，不能静默消失） */
const seasonsLoaded = ref<boolean>(false)
const seasonAll = ref<boolean>(true)
const chosenSeasons = ref<number[]>([])

let searchTimer: ReturnType<typeof setTimeout> | undefined
let searchSeq = 0

const showCandidates = computed<boolean>(() => (
  !picked.value
  && searchReady.value
  && (searching.value || searchError.value !== '' || candidates.value.length > 0)
))

const hasTypedName = computed<boolean>(() => !picked.value && wishText.value.trim() !== '')

const primaryBtnText = computed<string>(() => {
  if (throwing.value) return '投放中'
  // R53A-39：装饰 emoji 不进按钮可访问名，避免读屏念成「电影摄影机／帆船」
  if (picked.value && picked.value.in_library) return '已在库，去播放器看'
  return '投进池子'
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
  picked.value = null
  seasons.value = []
  invalidateSeasons()
  chosenSeasons.value = []
  seasonAll.value = true
  if (searchTimer !== undefined) {
    clearTimeout(searchTimer)
    searchTimer = undefined
  }
  const q = wishText.value.trim()
  if (!q) {
    // R53A-40：清空输入时一并让在途搜索作废，避免旧候选落到新输入上
    searchSeq++
    candidates.value = []
    searching.value = false
    searchError.value = ''
    return
  }
  searchTimer = setTimeout(() => { void runSearch(q) }, 350)
}

async function runSearch(q: string): Promise<void> {
  const seq = ++searchSeq
  searching.value = true
  searchError.value = ''
  try {
    const r = await mediaSeekApi.search(q)
    if (seq !== searchSeq) return
    // R53A-40：响应回来时输入已变，丢弃过期候选，避免旧结果落到新输入上
    if (q !== wishText.value.trim()) return
    searchReady.value = r.configured
    // degraded：TMDB 搜索中途失败时为 true（后端 media_seek.search_candidates 每条返回路径都带）
    searchDegraded.value = r.degraded === true
    candidates.value = r.configured ? (r.results || []) : []
  } catch (e) {
    if (seq !== searchSeq) return
    searchError.value = humanizeError(e)
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

/** 重试上一次关键词的搜索（错误态入口） */
function retrySearch(): void {
  const q = wishText.value.trim()
  if (q) void runSearch(q)
}

function selectCandidate(c: MediaSeekCandidate): void {
  if (searchTimer !== undefined) {
    clearTimeout(searchTimer)
    searchTimer = undefined
  }
  picked.value = c
  candidates.value = []
  searching.value = false
  searchError.value = ''
  invalidateSeasons()
  if (c.kind === 'series' && !c.in_library) {
    void loadSeasons(c.tmdb_id)
  }
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

/** 季列表拉取失败时重试当前选中剧集（错误态入口） */
function retrySeasons(): void {
  const p = picked.value
  if (p && p.kind === 'series' && !p.in_library) void loadSeasons(p.tmdb_id)
}

/** 作废在途的季列表请求，避免旧响应覆盖新选中剧集 */
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

function toggleSeasonAll(): void {
  seasonAll.value = true
  chosenSeasons.value = []
}

function toggleSeason(n: number): void {
  seasonAll.value = false
  const i = chosenSeasons.value.indexOf(n)
  if (i >= 0) {
    chosenSeasons.value.splice(i, 1)
  } else {
    chosenSeasons.value.push(n)
  }
  if (chosenSeasons.value.length === 0) seasonAll.value = true
}

function closeModal() {
  modalOpen.value = false
  wishText.value = ''
  emptyWarn.value = false
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onEsc)
  lastTrigger.value?.focus?.()
  // 智能选片状态复位：作废在飞行请求
  searchSeq++
  if (searchTimer !== undefined) {
    clearTimeout(searchTimer)
    searchTimer = undefined
  }
  searching.value = false
  searchError.value = ''
  candidates.value = []
  picked.value = null
  seasons.value = []
  loadingSeasons.value = false
  seasonsLoaded.value = false
  seasonAll.value = true
  chosenSeasons.value = []
  searchReady.value = true
  searchDegraded.value = false
}
// R53A-44：飞行动画定时器与卸载标记，供 onUnmounted 统一清理
let flyTimerA: number | undefined
let flyTimerB: number | undefined
let flyBoatEl: HTMLElement | null = null
let flySplashEl: HTMLElement | null = null
let flyUnmounted = false
onUnmounted(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onEsc)
  // R53A-44：卸载时清掉飞行动画定时器，避免 done() 触发已卸载组件的 loadMine
  flyUnmounted = true
  if (flyTimerA !== undefined) { clearTimeout(flyTimerA); flyTimerA = undefined }
  if (flyTimerB !== undefined) { clearTimeout(flyTimerB); flyTimerB = undefined }
  flyBoatEl?.remove()
  flySplashEl?.remove()
  flyBoatEl = null
  flySplashEl = null
})

let mineSeq = 0
let hotSeq = 0

async function loadMine() {
  const seq = ++mineSeq
  myLoading.value = true
  myError.value = ''
  try {
    const r = await mediaSeekApi.getMyRequests()
    if (seq !== mineSeq) return
    myWishes.value = r.requests || []
  } catch (e) {
    if (seq !== mineSeq) return
    myError.value = humanizeError(e)
  } finally {
    if (seq === mineSeq) myLoading.value = false
  }
}
async function loadHot() {
  const seq = ++hotSeq
  hotLoading.value = true
  hotError.value = ''
  try {
    const r = await mediaSeekApi.hot()
    if (seq !== hotSeq) return
    hotWishes.value = r.requests || []
  } catch (e) {
    if (seq !== hotSeq) return
    hotError.value = humanizeError(e)
  } finally {
    if (seq === hotSeq) hotLoading.value = false
  }
}

async function throwWish() {
  // 选中已在库候选：不占求片额度，直接引导去播放器
  if (picked.value && picked.value.in_library) {
    closeModal()
    goOperator()
    return
  }
  const name = picked.value ? picked.value.name : wishText.value.trim()
  if (!name) { emptyWarn.value = true; return }
  if (throwing.value) return
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
    closeModal()
    flyBoat(() => {
      // 动画结束后刷新列表并高亮新愿望
      // R53A-42：等列表真的刷到这条新愿望后再起高亮计时，慢网下才看得到「刚投进井里」
      void loadMine().then(() => {
        justAddedId.value = r.request_id
        window.setTimeout(() => { justAddedId.value = null }, 1700)
      })
    })
  } catch (e) {
    // 投放失败走 toast，不污染"我的心愿"列表的 myError
    useToast().error(humanizeError(e))
  } finally {
    throwing.value = false
  }
}

/** 纸船飞入古井动画（设计稿原交互） */
function flyBoat(done: () => void) {
  // 减少动态偏好：跳过飞行动画，直接完成
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { done(); return }
  const well = wellRef.value
  if (!well) { done(); return }
  const wr = well.getBoundingClientRect()
  const boat = document.createElement('div')
  boat.className = 'flyboat'
  boat.textContent = '⛵'
  boat.setAttribute('aria-hidden', 'true')
  boat.style.left = `${wr.left + wr.width / 2 - 17}px`
  boat.style.top = `${wr.top - 70}px`
  document.body.appendChild(boat)
  flyBoatEl = boat
  requestAnimationFrame(() => {
    boat.style.transition = 'transform .85s cubic-bezier(.5,-.1,.7,.5)'
    boat.style.transform = `translateY(${wr.height / 2 + 53}px)`
  })
  flyTimerA = window.setTimeout(() => {
    boat.style.transition = 'opacity .25s'
    boat.style.opacity = '0'
    const sp = document.createElement('div')
    sp.className = 'splash'
    sp.textContent = '噗通！'
    sp.setAttribute('aria-hidden', 'true')
    sp.style.left = `${wr.left + wr.width / 2 - 22}px`
    sp.style.top = `${wr.top + wr.height / 2 - 40}px`
    document.body.appendChild(sp)
    flySplashEl = sp
    flyTimerB = window.setTimeout(() => {
      sp.remove()
      boat.remove()
      flyBoatEl = null
      flySplashEl = null
      // R53A-44：组件已卸载就不再回调，避免对已卸载组件 loadMine
      if (!flyUnmounted) done()
    }, 1150)
  }, 900)
}

async function upvote(w: HotMediaSeekRequest) {
  if (w.voted || voting.value) return
  voting.value = w.id
  try {
    const r = await mediaSeekApi.vote(w.id)
    w.voted = r.voted
    w.vote_count = r.vote_count
    w.weighted_vote_count = r.weighted_vote_count
  } catch {
    // 附议失败不再静默：toast 提醒，按钮保持可点
    useToast().error('附议失败，再试一次')
  } finally {
    voting.value = null
  }
}

onMounted(() => {
  loadMine()
  loadHot()
  loadWants()
  // /want?q=… 旧深链重定向过来时带词：直接打开许愿弹窗并预搜（原想看页同口径）
  const q = route.query.q
  if (typeof q === 'string' && q.trim()) {
    wishText.value = q.trim()
    openModal()
    void runSearch(q.trim())
  }
})
</script>

<style scoped>
/* 搜索错误态：重试入口 / 无结果提示 */
.seek-retry {
  margin-top: 6px;
  padding: 3px 12px;
  font: inherit;
  font-size: 12px;
  color: var(--ink);
  background: transparent;
  border: 1px solid currentColor;
  border-radius: var(--paper-r-full, 999px);
  cursor: pointer;
  opacity: 0.8;
}
.seek-retry:hover { opacity: 1; }
.seek-empty { padding: 6px 0; font-size: 13px; opacity: 0.75; }

/* 古井：设计稿 game-wishlist.html 原值 */
.well-wrap { text-align: center; margin-bottom: 14px; }
.well {
  width: 220px;
  height: 220px;
  margin: 0 auto;
  position: relative;
  border-radius: var(--paper-r-full, 50%);
  background: radial-gradient(circle, #7FA8C9 0%, #4A7CA8 55%, #2C5378 100%);
  border: 14px solid #B9A87E;
  outline: 3px solid var(--ink);
  box-shadow: inset 0 0 40px rgba(0, 0, 0, 0.35), 0 8px 20px rgba(0, 0, 0, 0.15);
  overflow: hidden;
}
.well::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: var(--paper-r-full, 50%);
  background: radial-gradient(circle at 35% 30%, rgba(255, 255, 255, 0.35), transparent 45%);
}
.moon {
  position: absolute;
  top: 34px;
  left: 0;
  right: 0;
  margin: 0 auto;
  width: fit-content;
  font-size: 26px;
  opacity: 0.85;
  filter: drop-shadow(0 0 8px #FFE9A8);
}
.ripple {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 200px;
  height: 200px;
  border: 2px solid rgba(255, 255, 255, 0.55);
  border-radius: var(--paper-r-full, 50%);
  transform: translate(-50%, -50%) scale(0.2);
  animation: rip 3.2s infinite;
  pointer-events: none;
}
/* 减少动态偏好：涟漪纯属装饰，直接隐藏（基态 transform 会被全局置空导致错位） */
@media (prefers-reduced-motion: reduce) {
  .ripple { display: none; }
}
.r2 { animation-delay: 1.05s; }
.r3 { animation-delay: 2.1s; }
/* 只动 transform/opacity：不刷 layout，60fps */
@keyframes rip {
  0% { transform: translate(-50%, -50%) scale(0.2); opacity: 0.9; }
  100% { transform: translate(-50%, -50%) scale(1); opacity: 0; }
}
.well .boat-float {
  position: absolute;
  font-size: 24px;
  animation: float 4s ease-in-out infinite;
}
@keyframes float {
  0%, 100% { transform: translateY(0) rotate(-6deg); }
  50% { transform: translateY(-7px) rotate(6deg); }
}
.well-note { font-size: 12px; color: var(--ink2); margin-top: 10px; }
.well-note b { color: var(--red); }

/* 大按钮：设计稿原值 */
.wish-btn {
  display: block;
  width: 100%;
  background: var(--red);
  color: var(--paper-card);
  border: none;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 3px;
  padding: 15px;
  border-radius: 14px;
  cursor: pointer;
  box-shadow: 0 4px 0 var(--red-deep, #a33319);
  transition: transform 0.12s;
}
.wish-btn:active { transform: translateY(3px); box-shadow: 0 1px 0 var(--red-deep, #a33319); }

/* 纸船飞 + 噗通 */
.flyboat {
  position: fixed;
  font-size: 34px;
  z-index: 99;
  pointer-events: none;
  filter: drop-shadow(0 4px 6px rgba(0, 0, 0, 0.3));
}
.splash {
  position: fixed;
  font-size: 20px;
  font-weight: 800;
  color: var(--red);
  z-index: 99;
  pointer-events: none;
  animation: pop 1.2s forwards;
}
@keyframes pop {
  0% { transform: scale(0.4); opacity: 0; }
  25% { transform: scale(1.25); opacity: 1; }
  100% { transform: scale(1) translateY(-18px); opacity: 0; }
}

/* 愿望列表 */
.wish {
  background: var(--paper-card);
  border: 1.5px solid var(--line);
  border-radius: var(--paper-r-lg, 12px);
  padding: 13px 14px;
  margin-bottom: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.wish.new { animation: glow 1.6s; border-color: var(--gold); }
@keyframes glow {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
.wish > div:first-child { min-width: 0; flex: 1; }
.wish b { font-size: 14px; display: block; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wmeta { font-size: 11px; color: var(--ink2); margin-top: 4px; }
/* R45/R53：营地已上架 → 文字按钮（接线员页接线入口）；透明 padding + 负 margin 扩大点击区，视觉行高不变，纵向命中区 ≥44px */
.goto-player {
  background: none;
  border: none;
  color: var(--blue);
  font-size: 11px;
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
  padding: 16px 6px;
  margin: -16px -6px;
}
.chip {
  font-size: 11px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: var(--paper-r-2xl, 20px);
  white-space: nowrap;
}
.chip.blue { background: #E3ECF8; color: var(--blue); border: 1px solid var(--blue); }
.chip.green { background: #E8F0E9; color: var(--green); border: 1px solid var(--green); }
.chip.gray { background: var(--paper2); color: var(--ink2); border: 1px solid var(--line); }
.crowd {
  background: var(--paper2);
  border: 1.5px dashed var(--ink2);
  border-radius: var(--paper-r-lg, 12px);
  padding: 13px 14px;
  margin-bottom: 10px;
}
.crowd .row1 { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.crowd .row1 > :first-child { min-width: 0; flex: 1; }
.crowd b { font-size: 14px; display: block; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.crowd .want { font-size: 11px; color: var(--ink2); margin-top: 6px; }
.upvote {
  width: 100%;
  position: relative;
  /* 透明 padding + 负 margin：视觉按钮不变，点击区纵向扩大到 44px */
  background: transparent;
  border: none;
  color: var(--paper-card);
  font-size: 13px;
  font-weight: 700;
  padding: 14px 9px;
  margin: 5px 0 -5px;
  cursor: pointer;
}
.upvote::before {
  content: "";
  position: absolute;
  top: 5px; right: 0; bottom: 5px; left: 0;
  background: var(--ink);
  border-radius: var(--paper-r-md, 10px);
}
.upvote .lbl { position: relative; }
.upvote.done::before { background: var(--green); }
.upvote:disabled { cursor: default; }

/* 弹窗 */
.modal {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: none;
  align-items: flex-start;
  justify-content: center;
  background: rgba(42, 38, 32, 0.45);
  /* R102-2：四边安全区（与 AccountSettingsView 弹窗同口径），刘海屏顶部不再贴边 */
  padding: max(24px, env(safe-area-inset-top)) max(24px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(24px, env(safe-area-inset-left));
  overflow-y: auto;
}
.modal.show { display: flex; }
.mcard {
  background: var(--paper-card);
  border: 2px solid var(--ink);
  border-radius: 14px;
  padding: 22px 20px;
  width: 100%;
  /* R102-1：桌面端限宽，与 .v4-page 的 min(430px,100vw) 列宽对齐（同类弹窗 OperatorView/AccountSettingsView 均有限宽，本页是离群） */
  max-width: 430px;
  animation: up 0.25s;
}
@keyframes up { from { transform: translateY(24px); opacity: 0; } }
.mcard h3 { font-size: 17px; font-weight: 800; margin: 0 0 4px; }
.mcard p { font-size: 12px; color: var(--ink2); margin: 0 0 14px; }
.mcard input {
  width: 100%;
  font-size: 16px;
  padding: 12px;
  border: 1.5px solid var(--line);
  border-radius: var(--paper-r-md, 10px);
  background: var(--paper);
  outline: none;
  color: var(--ink);
}
.mcard input:focus { border-color: var(--gold); }
.wish-warn { font-size: 12px; color: var(--red); margin-top: 8px; }
.mrow { display: flex; gap: 10px; margin-top: 16px; }
.mbtn {
  flex: 1;
  min-height: 44px;
  padding: 12px;
  border-radius: var(--paper-r-md, 10px);
  font-size: 14px;
  font-weight: 800;
  cursor: pointer;
  border: none;
}
.mbtn.cancel { background: var(--paper2); color: var(--ink2); }
.mbtn.go { background: var(--red); color: var(--on-dark, #fff); box-shadow: 0 3px 0 var(--red-deep, #a33319); }
.mbtn.go:disabled { background: var(--ink2); box-shadow: none; }

/* 智能选片：候选/已选/季选择（纸色风格，scoped） */
.seek-cands { margin-top: 10px; }
.seek-status { font-size: 12px; color: var(--ink2); }
.seek-error { font-size: 12px; color: var(--red); }
.seek-degraded { font-size: 11px; color: var(--ink2); margin-bottom: 6px; }
.seek-fallback { font-size: 11px; color: var(--ink2); margin-top: 10px; }
.cand-list {
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cand {
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
  background: var(--paper);
  border: 1.5px solid var(--line);
  border-radius: 12px;
  padding: 8px 10px;
  cursor: pointer;
  width: 100%;
}
.cand:active { transform: scale(0.98); }
.cand-poster {
  width: 40px;
  height: 56px;
  object-fit: cover;
  border-radius: 8px;
  flex-shrink: 0;
  border: 1px solid var(--line);
}
.cand-ph {
  width: 40px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  background: var(--paper2);
  border-radius: 8px;
  flex-shrink: 0;
  border: 1px solid var(--line);
}
.cand-main { flex: 1; min-width: 0; }
.cand-main b {
  display: block;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cand-meta { font-size: 11px; color: var(--ink2); margin-top: 2px; display: block; }
.cand-badges { display: flex; gap: 4px; flex-shrink: 0; }
.picked-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  background: var(--paper2);
  border: 1.5px solid var(--gold);
  border-radius: 12px;
  padding: 8px 10px;
}
.picked-poster {
  width: 40px;
  height: 56px;
  object-fit: cover;
  border-radius: 8px;
  flex-shrink: 0;
  border: 1px solid var(--line);
}
.picked-ph {
  width: 40px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  background: var(--paper2);
  border-radius: 8px;
  flex-shrink: 0;
  border: 1px solid var(--line);
}
.picked-info { flex: 1; min-width: 0; }
.picked-info b {
  display: block;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.picked-meta { font-size: 11px; color: var(--ink2); margin-top: 2px; display: block; }
.picked-change {
  position: relative;
  font-size: 12px;
  color: var(--blue);
  background: transparent;
  border: 1px solid var(--blue);
  border-radius: 20px;
  padding: 4px 10px;
  cursor: pointer;
  white-space: nowrap;
}
/* 已在库候选的「想看」动作（想看页合并进来）：与投心愿同为红主按钮口径 */
.picked-want {
  position: relative;
  font-size: 12px;
  font-weight: 700;
  color: var(--paper-card);
  background: var(--red);
  border: 1px solid var(--red);
  border-radius: 20px;
  padding: 4px 10px;
  cursor: pointer;
  white-space: nowrap;
}
.picked-want:disabled { opacity: 0.6; cursor: default; }
/* 命中区纵向扩到 44px，视觉尺寸保持不变 */
.picked-want::before {
  content: "";
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 44px;
  transform: translateY(-50%);
}
.picked-wanted { font-size: 12px; color: var(--ink2); white-space: nowrap; }
/* 求片举报（想看页合并进来；仅公益服）：行内小链接 + 行内展开表单（老页同形态） */
.report-line { margin-top: 6px; font-size: 11px; }
.report-link { color: var(--ink2); text-decoration: underline; cursor: pointer; }
.report-done { color: var(--ink2); }
.report-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
}
.report-form .report-input {
  flex: 1 1 140px;
  font-size: 13px;
  padding: 8px 10px;
  border: 1.5px solid var(--line);
  border-radius: var(--paper-r-md, 10px);
  background: var(--paper);
  outline: none;
  color: var(--ink);
}
.report-form .report-input:focus { border-color: var(--gold); }
.report-submit {
  font-size: 12px;
  font-weight: 700;
  color: var(--paper-card);
  background: var(--red);
  border: 1px solid var(--red);
  border-radius: 20px;
  padding: 4px 10px;
  cursor: pointer;
  white-space: nowrap;
}
.report-submit:disabled { opacity: 0.6; cursor: default; }
.report-cancel {
  font-size: 12px;
  color: var(--ink2);
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 4px 10px;
  cursor: pointer;
  white-space: nowrap;
}
.report-cancel:disabled { opacity: 0.6; cursor: default; }
/* 我的想看行：海报 + 标题/状态消息（结构与 .wish 行同口径） */
.want-poster {
  width: 34px;
  height: 48px;
  object-fit: cover;
  border-radius: 6px;
  flex-shrink: 0;
  border: 1px solid var(--line);
  background: var(--paper2);
}
.want-main { flex: 1; min-width: 0; }
.want-main b {
  display: block;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 命中区纵向扩到 44px，视觉尺寸保持不变 */
.picked-change::before {
  content: "";
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 44px;
  transform: translateY(-50%);
}
.sels-box { margin-top: 10px; }
.sels-label { font-size: 12px; color: var(--ink2); margin-bottom: 6px; }
.sels-row { display: flex; flex-wrap: wrap; column-gap: 6px; row-gap: 22px; }
.sels-chip {
  position: relative;
  font-size: 11px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 20px;
  background: var(--paper);
  color: var(--ink2);
  border: 1px solid var(--line);
  cursor: pointer;
}
/* 命中区纵向扩到 44px，视觉尺寸保持不变 */
.sels-chip::before {
  content: "";
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 44px;
  transform: translateY(-50%);
}
.sels-chip.on { background: var(--ink); color: var(--paper-card); border-color: var(--ink); }
.sels-loading { font-size: 11px; color: var(--ink2); margin-top: 6px; }

/* R27：减少动态偏好下装饰动画停掉（本地守卫） */
@media (prefers-reduced-motion: reduce) {
  .well .boat-float { animation: none; }
  .wish-btn:active { transform: none; }
  /* R41：新愿望 glow + 弹窗入场动画在减弱动态下关闭 */
  .wish.new, .mcard { animation: none; }
}
</style>
