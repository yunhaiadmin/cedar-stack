<template>
  <UhdAccountLayout>
    <!-- 求片反馈（2026-11-13 合并页；2026-11-15 按作品组织重做）：许愿池＋
         求救信号合并为一页。三页签（发起请求/我的请求/请求广场），每页签一句
         副标题；发起请求是三档分段（求片/追新/资源反馈）＋每档一套须知卡组。
         2026-11-15 重做（照友站录屏逐帧形态，任务书 uhd-request-flow-frame-study）：
         求片/追新共用搜索、先搜后发、提交走弹层、成功跳作品请求详情页；
         我的请求与广场改单容器分隔行＋海报＋分页；追新落 movie_requests
         （kind=chase），旧追新工单仍在我的请求里展开线程看管理员回复。
         页面跟随用户外观；顶栏与招牌主按钮配方沿用 2026-11-14 对齐版。 -->
    <div class="rf-page">
      <div class="rf-inner">
      <!-- 顶栏（照友站：返回＋小标题＋右上「网页观影」彩虹玻璃胶囊） -->
      <div class="rf-topbar">
        <button type="button" class="rf-back" aria-label="返回" @click="goBack">
          <ChevronLeft :size="24" aria-hidden="true" />
        </button>
        <span class="rf-topbar__title">求片反馈</span>
        <RouterLink to="/library" class="rf-watch">
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
            <defs>
              <linearGradient id="wp-g-rf" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="#2f8fe0" />
                <stop offset="0.5" stop-color="#e8766f" />
                <stop offset="1" stop-color="#ecd04f" />
              </linearGradient>
            </defs>
            <path d="M7 4.2v15.6c0 .8.9 1.3 1.6.9l11.6-7.8c.6-.4.6-1.3 0-1.7L8.6 3.3C7.9 2.9 7 3.4 7 4.2Z" fill="url(#wp-g-rf)" />
          </svg>
          网页观影
        </RouterLink>
      </div>

      <header class="rf-head">
        <div class="rf-head__text">
          <h1>求片反馈</h1>
          <p>{{ tabSubtitle }}</p>
        </div>
      </header>

      <nav class="rf-tabs" aria-label="求片反馈页签">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          class="rf-tabs__btn"
          :class="{ 'rf-tabs__btn--on': tab === t.key }"
          :aria-current="tab === t.key ? 'page' : undefined"
          @click="setTab(t.key)"
        >{{ t.label }}</button>
      </nav>

      <!-- 发起请求 -->
      <section v-if="tab === 'create'" class="rf-create">
        <!-- 三档分段（原站形态）：求片／追新／资源反馈。
             映射：求片＝按作品搜 TMDB 发起求片；追新＝按作品搜、从库内
             作品行发起（电影/剧集由作品本身决定，不再手选）；资源反馈＝
             修正信息＋其他反馈的问题类型（表单内网格第一项就是修正信息）。
             旧的电影/剧集追新工单仍在「我的请求」里可查，不丢类型。 -->
        <div class="rf-seg" role="group" aria-label="请求类型">
          <button
            v-for="g in segGroups"
            :key="g.key"
            type="button"
            class="rf-seg__btn"
            :class="{ 'rf-seg__btn--on': segGroup === g.key }"
            :aria-pressed="segGroup === g.key"
            @click="setSegGroup(g.key)"
          >{{ g.label }}</button>
        </div>

        <!-- 求片/追新共用搜索（2026-11-15 按作品组织重做，照友站 2.1/2.2）：
             先搜后发——同一搜索框、同一结果列表，只有右侧行动按档分化；
             提交走弹层（RequestSubmitModal），成功后跳作品请求详情页。
             追新不再手填片名：必须从库内作品行发起（后端同口径校验）。 -->
        <div v-if="reqType === 'seek' || isChase" class="rf-create__body">
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
              @keyup.enter="runSearchNow"
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
              <span v-if="searching" class="rf-search__spinner" aria-hidden="true"></span>
              <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
                <circle cx="10.5" cy="10.5" r="6.5" />
                <path d="M15.5 15.5 21 21" />
              </svg>
            </button>
          </div>
          <div v-if="emptyWarn" class="rf-hint--err">先输入片名，再点搜索</div>
          <WorkSearchResults
            :mode="reqType === 'seek' ? 'seek' : 'chase'"
            :candidates="candidates"
            :searching="searching"
            :status="searchStatus"
            :error="searchError"
            :degraded="searchDegraded"
            :query="searchFor"
            @pick="openSubmitModal"
            @existing="goExistingWork"
            @fallback="openFallbackModal"
          />
          <p v-if="reqType === 'seek' && monthlyRemaining != null" class="rf-quota">本月还可求片 {{ monthlyRemaining }} 次</p>
          <NoticeCards :kind="reqType === 'seek' ? 'seek' : 'chase'" />
        </div>

        <!-- 资源反馈档：修正信息并入问题类型网格第一项（选中后走 correction
             工单、表单文案随之切换）；其余项是其他反馈的问题类型 -->
        <div v-else class="rf-create__body">
          <div class="rf-form">
            <div class="rf-form__label">问题类型</div>
            <div class="rf-types" role="radiogroup" aria-label="问题类型">
              <button
                v-for="c in fbOptions"
                :key="c.value"
                type="button"
                class="rf-type"
                :class="{ on: fbSelected === c.value }"
                role="radio"
                :aria-checked="fbSelected === c.value"
                @click="setFbOption(c.value)"
              >{{ c.label }}</button>
            </div>
            <label class="rf-form__label" for="rf-fb-title">一句话标题</label>
            <input
              id="rf-fb-title"
              v-model="fbTitle"
              class="rf-form__input"
              type="text"
              :placeholder="fbTitlePh"
              maxlength="100"
            />
            <label class="rf-form__label" for="rf-fb-msg">{{ fbMsgLabel }}</label>
            <textarea
              id="rf-fb-msg"
              v-model="fbMessage"
              class="rf-form__area"
              :placeholder="fbMsgPh"
              maxlength="1000"
            ></textarea>
            <button class="rf-submit" :disabled="fbSending" @click="submitFeedback">{{ fbSending ? '提交中…' : fbSelected === 'correction' ? '提交修正' : '提交反馈' }}</button>
          </div>
          <NoticeCards kind="feedback" />
        </div>
        <RequestSubmitModal
          :open="modalOpen"
          :mode="modalMode"
          :work="modalWork"
          @close="modalOpen = false"
          @submitted="onModalSubmitted"
        />
      </section>

      <!-- 我的请求（照友站 2.6）：状态 chips 在圆角容器条内（全部＝填充
           pill、其余文字＋色点）＋整宽类型下拉；行＝海报＋片名＋类型 pill＋
           状态 pill＋正文摘要＋›；求片类行点进作品请求详情页，工单行仍在
           本页展开线程（管理员回复保留在此，2026-11-15 定版）；底部分页。 -->
      <section v-else-if="tab === 'mine'" class="rf-list">
        <div class="rf-filterbar">
          <div class="rf-statchips" role="group" aria-label="按状态筛选">
            <button
              v-for="s in stateOptions"
              :key="s.key"
              type="button"
              class="rf-statchip"
              :class="{ 'rf-statchip--on': stateFilter === s.key }"
              :aria-pressed="stateFilter === s.key"
              @click="stateFilter = s.key"
            ><span v-if="s.key !== 'all'" class="rf-dot" :class="'rf-dot--' + s.key" aria-hidden="true"></span>{{ s.label }}</button>
          </div>
        </div>
        <select v-model="typeFilter" class="rf-filter__select rf-filter__select--wide" aria-label="按类型筛选">
          <option value="all">全部类型</option>
          <option value="seek">影片求片</option>
          <option value="chase_movie">电影追新</option>
          <option value="chase_series">剧集追新</option>
          <option value="correction">修正信息</option>
          <option value="feedback">其他反馈</option>
        </select>

        <div v-if="mineLoading" class="rf-loading">
          <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 76px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="mineError" class="rf-err">
          请求记录读取失败：{{ mineError }}<br />
          <button class="rf-retry" @click="() => loadMine()">重试</button>
        </div>
        <div v-else-if="filteredMine.length === 0" class="rf-empty">暂无符合条件的请求</div>
        <template v-else>
          <div class="rf-minebox">
            <div
              v-for="row in pagedMine"
              :key="row.source + ':' + row.id"
              class="rf-mrow"
              :class="{ 'rf-mrow--new': justAddedKey === row.source + ':' + row.id }"
            >
              <button class="rf-mrow__head" :aria-expanded="row.source === 'ticket' ? expandedKey === row.source + ':' + row.id : undefined" @click="onMineRowClick(row)">
                <img v-if="row.posterUrl" :src="imageAuthUrl(row.posterUrl, 120)" :alt="row.title" class="rf-mrow__poster" loading="lazy" />
                <span v-else class="rf-mrow__poster rf-mrow__poster--ph" aria-hidden="true">{{ row.title.slice(0, 1) }}</span>
                <span class="rf-mrow__main">
                  <span class="rf-mrow__titlerow">
                    <span class="rf-mrow__title">{{ row.title }}</span>
                    <span class="rf-pill rf-pill--type">{{ rowTypeLabel(row) }}</span>
                    <span class="rf-pill rf-pill--state" :class="'rf-pill--' + row.state">{{ stateLabel(row.state) }}</span>
                  </span>
                  <span v-if="rowSummary(row)" class="rf-mrow__summary">{{ rowSummary(row) }}</span>
                </span>
                <span class="rf-row__chev" :class="{ 'rf-row__chev--open': row.source === 'ticket' && expandedKey === row.source + ':' + row.id }" aria-hidden="true">›</span>
              </button>
              <div v-if="row.source === 'ticket' && expandedKey === row.source + ':' + row.id" class="rf-row__detail">
                <TicketThread :ticket="row.rawTicket" @changed="onThreadChanged" />
              </div>
            </div>
          </div>
          <div class="rf-pager">
            <span class="rf-pager__info">共 {{ filteredMine.length }} 条 · 第 {{ pageRangeText }} 条</span>
            <div class="rf-pager__nav" role="navigation" aria-label="分页">
              <button type="button" class="rf-pager__btn" :disabled="minePage <= 1" aria-label="第一页" @click="minePage = 1">«</button>
              <button type="button" class="rf-pager__btn" :disabled="minePage <= 1" aria-label="上一页" @click="minePage--">‹</button>
              <button
                v-for="p in pageNumbers"
                :key="p"
                type="button"
                class="rf-pager__btn"
                :class="{ 'rf-pager__btn--on': p === minePage }"
                :aria-current="p === minePage ? 'page' : undefined"
                @click="minePage = p"
              >{{ p }}</button>
              <button type="button" class="rf-pager__btn" :disabled="minePage >= pageCount" aria-label="下一页" @click="minePage++">›</button>
              <button type="button" class="rf-pager__btn" :disabled="minePage >= pageCount" aria-label="最后一页" @click="minePage = pageCount">»</button>
            </div>
          </div>
        </template>
      </section>

      <!-- 请求广场（照友站 2.7）：搜索框内右侧嵌「搜索」钮；chips＋整宽
           类型下拉；单容器分隔行按作品聚合（后端 /media-seek/plaza 真聚合）。
           类型筛选只留后端真支持的：求片＋追新（自由追新与补缺集同归
           chase）；反馈工单没有作品绑定、不进广场聚合（计数恒 0 是数据
           口径，见后端 plaza_groups docstring）。 -->
      <section v-else class="rf-list">
        <form class="rf-searchbar" @submit.prevent="applyPlazaSearch">
          <input
            v-model="plazaQuery"
            class="rf-searchbar__input"
            type="text"
            placeholder="搜索影片议题"
            maxlength="50"
            aria-label="搜索影片议题"
          />
          <button type="submit" class="rf-searchbar__btn">搜索</button>
        </form>
        <div class="rf-filterbar">
          <div class="rf-statchips" role="group" aria-label="按状态筛选">
            <button
              v-for="s in plazaStateOptions"
              :key="s.key"
              type="button"
              class="rf-statchip"
              :class="{ 'rf-statchip--on': plazaStateFilter === s.key }"
              :aria-pressed="plazaStateFilter === s.key"
              @click="plazaStateFilter = s.key"
            ><span v-if="s.key !== 'all'" class="rf-dot" :class="'rf-dot--' + s.key" aria-hidden="true"></span>{{ s.label }}</button>
          </div>
        </div>
        <select v-model="plazaTypeFilter" class="rf-filter__select rf-filter__select--wide" aria-label="按类型筛选">
          <option value="all">全部类型</option>
          <option value="seek">影片求片</option>
          <option value="chase">追新</option>
        </select>

        <div v-if="plazaLoading" class="rf-loading">
          <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 76px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="plazaError" class="rf-err">
          广场加载失败：{{ plazaError }}<br />
          <button class="rf-retry" @click="loadPlaza">重试</button>
        </div>
        <div v-else-if="plazaGroups.length === 0" class="rf-empty">暂无符合条件的请求</div>
        <div v-else class="rf-minebox">
          <article v-for="g in plazaGroups" :key="g.key" class="rf-mrow">
            <div class="rf-mrow__head rf-mrow__head--static">
              <img
                v-if="g.poster_url"
                class="rf-mrow__poster"
                :src="imageAuthUrl(g.poster_url, 120)"
                :alt="g.movie_name"
                loading="lazy"
              />
              <span v-else class="rf-mrow__poster rf-mrow__poster--ph" aria-hidden="true">{{ (g.movie_name || '片').slice(0, 1) }}</span>
              <span class="rf-mrow__main">
                <span class="rf-mrow__titlerow">
                  <span class="rf-mrow__title">{{ g.movie_name }}</span>
                  <span v-if="g.in_library" class="rf-badge rf-badge--green">库内已有</span>
                  <span v-else-if="g.processing_count > 0" class="rf-badge rf-badge--gray">{{ g.processing_count }} 条处理中</span>
                </span>
                <span class="rf-mrow__summary rf-mrow__summary--wrap">
                  求片 {{ g.seek_count }}<span class="rf-topic__sep" aria-hidden="true">·</span>追新 {{ g.chase_count }}<span class="rf-topic__sep" aria-hidden="true">·</span>反馈 {{ g.feedback_count }}<span class="rf-topic__sep" aria-hidden="true">·</span>{{ g.total_count }} 条记录
                </span>
              </span>
              <button
                type="button"
                class="rf-topic__view"
                @click="goMediaDetail(g)"
              >查看 <span class="rf-topic__chev" aria-hidden="true">›</span></button>
            </div>
          </article>
        </div>
      </section>
      </div>
    </div>
  </UhdAccountLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft } from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import NoticeCards from '@/components/requests/NoticeCards.vue'
import RequestSubmitModal, { type SubmitWork } from '@/components/requests/RequestSubmitModal.vue'
import TicketThread from '@/components/requests/TicketThread.vue'
import WorkSearchResults from '@/components/requests/WorkSearchResults.vue'
import {
  mediaSeekApi, ticketApi,
  type MediaSeekCandidate, type MediaSeekQuota, type MediaSeekRequest, type PlazaGroup, type Ticket,
} from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

type TabKey = 'create' | 'mine' | 'plaza'
/* 五类型（2026-11-13 用户亲定，照 UHDNow）：影片求片=seek 走 mediaSeek；
   电影/剧集追新、修正信息、其他反馈走工单 category（chase_movie/chase_series/correction/问题类型值） */
type ReqType = 'seek' | 'chase_movie' | 'chase_series' | 'correction' | 'feedback'
/* 列表行类型比提交类型多一个存量值：旧追新工单 category='chase' 未区分电影/剧集 */
type RowType = ReqType | 'chase'
type UnifiedState = 'pending' | 'doing' | 'done' | 'rejected' | 'withdrawn'

interface UnifiedRow {
  source: 'seek' | 'ticket'
  id: number
  type: RowType
  title: string
  created_at: string
  updated_at: string
  state: UnifiedState
  /* 求片类行（movie_requests）的展示分类：seek 求片 / chase 追新（含补缺集） */
  kind?: 'seek' | 'chase'
  posterUrl?: string | null
  note?: string | null
  tmdbId?: string | null
  workType?: string | null
  movieName?: string
  year?: string | null
  voteCount?: number
  seasonLabel?: string
  titleOnly?: boolean
  embyItemId?: string | null
  categoryLabel?: string
  rawTicket?: Ticket
}

const tabs: { key: TabKey; label: string }[] = [
  { key: 'create', label: '发起请求' },
  { key: 'mine', label: '我的请求' },
  { key: 'plaza', label: '请求广场' },
]
/* 发起请求的三档分段（原站形态）与我方 5 种类型的映射：
   求片 → 影片求片（seek）；追新 → 电影追新/剧集追新（档内小切换）；
   资源反馈 → 修正信息＋其他反馈（问题类型网格，修正信息为第一项） */
type SegGroup = 'seek' | 'chase' | 'feedback'
const segGroups: { key: SegGroup; label: string }[] = [
  { key: 'seek', label: '求片' },
  { key: 'chase', label: '追新' },
  { key: 'feedback', label: '资源反馈' },
]
const feedbackCategories = [
  { value: 'emby', label: '播放卡住' },
  { value: 'account', label: '账号问题' },
  { value: 'subscription', label: '订阅问题' },
  { value: 'payment', label: '支付问题' },
  { value: 'other', label: '其他问题' },
]
/* 资源反馈档的问题类型网格：修正信息在前，其后是其他反馈的问题类型 */
const fbOptions = [
  { value: 'correction', label: '修正信息' },
  ...feedbackCategories,
]
const stateOptions: { key: 'all' | UnifiedState; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待处理' },
  { key: 'doing', label: '处理中' },
  { key: 'done', label: '已完成' },
  { key: 'rejected', label: '已拒绝' },
  { key: 'withdrawn', label: '已撤回' },
]

const router = useRouter()
const route = useRoute()
const toast = useToast()

/* 顶栏返回：与站内其他二级页同口径（订阅页 goBack）——有历史后退，
   无历史回「我的」菜单（本页入口所在） */
function goBack(): void {
  if (window.history.length > 1) router.back()
  else void router.push('/game/account/menu')
}

const tab = ref<TabKey>('create')
const reqType = ref<ReqType>('seek')
const prefillQ = computed(() => (typeof route.query.q === 'string' ? route.query.q : ''))
/* 每页签一句副标题（文案自写，意思对齐原站：广场按媒体汇总、我的看状态、
   发起区分求片/追新/反馈三种来路） */
const tabSubtitle = computed(() => ({
  create: '库里没有的片来求，库里的剧集缺集来追，已有资源出问题来反馈。',
  mine: '你提交过的每一条请求，现在处理到哪一步，都在这里看。',
  plaza: '全站公开的请求按作品汇总，同一部片的求片、追新和反馈放在一起看。',
}[tab.value]))
const segGroup = computed<SegGroup>(() => {
  if (reqType.value === 'seek') return 'seek'
  if (reqType.value === 'chase_movie' || reqType.value === 'chase_series') return 'chase'
  return 'feedback'
})

const myRequests = ref<MediaSeekRequest[]>([])
const tickets = ref<Ticket[]>([])
type QuotaExt = MediaSeekQuota & { monthly_remaining?: number; monthly_limit?: number; monthly_used?: number }
const myQuota = ref<QuotaExt | null>(null)
const monthlyRemaining = computed(() => myQuota.value?.monthly_remaining ?? null)

const mineLoading = ref(true)
const mineError = ref('')
const expandedKey = ref<string | null>(null)
const justAddedKey = ref<string | null>(null)
const stateFilter = ref<'all' | UnifiedState>('all')
const typeFilter = ref<'all' | RowType>('all')
const plazaStateFilter = ref<'all' | UnifiedState>('all')
const plazaTypeFilter = ref<'all' | 'seek' | 'chase'>('all')
/* 我的请求分页（照友站 2.6：每页 10 条＋底部计数与分页器） */
const MINE_PAGE_SIZE = 10
const minePage = ref(1)
/* 广场状态 chips 不含「已撤回」（撤回是自己的取消，后端口径也不进广场） */
const plazaStateOptions = stateOptions.filter((s) => s.key !== 'withdrawn')
const plazaGroups = ref<PlazaGroup[]>([])
const plazaLoading = ref(true)
const plazaError = ref('')
const plazaQuery = ref('')
const plazaQ = ref('')

/* ===== 作品搜索（求片/追新共用，先搜后发）＋提交弹层 ===== */
const wishText = ref('')
const seekInput = ref<HTMLInputElement | null>(null)
const emptyWarn = ref(false)
const searching = ref(false)
const searchError = ref('')
const searchStatus = ref<'idle' | 'loading' | 'done' | 'unconfigured' | 'error'>('idle')
const searchFor = ref('')
const searchDegraded = ref(false)
const candidates = ref<MediaSeekCandidate[]>([])
let searchTimer: ReturnType<typeof setTimeout> | undefined
let searchSeq = 0
const modalOpen = ref(false)
const modalMode = ref<'seek' | 'chase'>('seek')
const modalWork = ref<SubmitWork | null>(null)

const fbCategory = ref('other')
const fbTitle = ref('')
const fbMessage = ref('')
const fbSending = ref(false)
/* 资源反馈档当前选中的问题类型：reqType=correction 时恒为修正信息，
   否则是 fbCategory（其他反馈的问题类型） */
const fbSelected = computed(() => (reqType.value === 'correction' ? 'correction' : fbCategory.value))
const fbTitlePh = computed(() => (fbSelected.value === 'correction'
  ? '比如：《一念永恒》年份标错了'
  : '比如：《一念永恒》第 3 集播放卡住'))
const fbMsgLabel = computed(() => (fbSelected.value === 'correction' ? '修正说明' : '问题描述'))
const fbMsgPh = computed(() => (fbSelected.value === 'correction'
  ? '比如：第 2 季标的是 12 集，实际有 16 集…'
  : '比如：《一念永恒》看到第 3 集，转圈半天出不来…'))

function stateLabel(s: UnifiedState): string {
  return { pending: '待处理', doing: '处理中', done: '已完成', rejected: '已拒绝', withdrawn: '已撤回' }[s]
}
function typeLabel(t: RowType): string {
  return {
    seek: '影片求片',
    chase_movie: '电影追新',
    chase_series: '剧集追新',
    chase: '追新',
    correction: '修正信息',
    feedback: '其他反馈',
  }[t]
}
/* 工单 category → 列表行类型：新值一一对应；存量 'chase' 未分电影/剧集，单列 legacy；
   其余（emby/account/subscription/payment/other 及未知值）都是其他反馈的问题类型 */
function ticketRowType(category: string): RowType {
  if (category === 'chase_movie') return 'chase_movie'
  if (category === 'chase_series') return 'chase_series'
  if (category === 'chase') return 'chase'
  if (category === 'correction') return 'correction'
  return 'feedback'
}
function feedbackCategoryLabel(category: string): string {
  return feedbackCategories.find((c) => c.value === category)?.label ?? category
}
function seekState(s: string): UnifiedState {
  if (s === 'approved') return 'doing'
  if (s === 'completed') return 'done'
  if (s === 'rejected') return 'rejected'
  return 'pending'
}
function ticketState(s: string): UnifiedState {
  if (s === 'pending') return 'doing'
  if (s === 'resolved') return 'done'
  if (s === 'closed') return 'withdrawn'
  return 'pending'
}
/* 两套数据源归一化后按 updated_at 倒序交错排（不许先 seek 后 ticket 分段排） */
const unifiedMine = computed<UnifiedRow[]>(() => {
  const rows: UnifiedRow[] = []
  for (const w of myRequests.value) {
    /* 求片类行的类型筛选口径：整片求片归 seek；自由追新/补缺集归两个
       追新筛选（行本身不再分电影/剧集，靠作品类型展示） */
    const isChaseKind = w.request_kind === 'chase' || w.request_kind === 'missing_episodes'
    rows.push({
      source: 'seek', id: w.id,
      type: isChaseKind ? (w.type === 'movie' ? 'chase_movie' : 'chase_series') : 'seek',
      kind: isChaseKind ? 'chase' : 'seek',
      title: w.movie_name,
      created_at: w.created_at, updated_at: w.created_at, state: seekState(w.status),
      posterUrl: w.poster_url, note: w.note, tmdbId: w.tmdb_id, workType: w.type,
      movieName: w.movie_name, year: w.year, voteCount: w.vote_count,
      seasonLabel: w.season_label, titleOnly: w.title_only, embyItemId: w.emby_item_id,
    })
  }
  for (const t of tickets.value) {
    const rowType = ticketRowType(t.category)
    rows.push({
      source: 'ticket', id: t.id, type: rowType, title: t.title,
      created_at: t.created_at, updated_at: t.updated_at, state: ticketState(t.status),
      categoryLabel: rowType === 'feedback' ? feedbackCategoryLabel(t.category) : undefined,
      rawTicket: t,
    })
  }
  rows.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  return rows
})
const filteredMine = computed(() => unifiedMine.value.filter((r) => {
  if (stateFilter.value !== 'all' && r.state !== stateFilter.value) return false
  if (typeFilter.value !== 'all') {
    if (r.type === typeFilter.value) return true
    /* 存量旧追新未分电影/剧集，两个追新筛选都带上它，不让老单在筛选下消失 */
    if (r.type === 'chase' && (typeFilter.value === 'chase_movie' || typeFilter.value === 'chase_series')) return true
    return false
  }
  return true
}))
/* 我的请求分页（筛选后切片；筛选变化回第一页） */
const pageCount = computed(() => Math.max(1, Math.ceil(filteredMine.value.length / MINE_PAGE_SIZE)))
const pagedMine = computed(() => filteredMine.value.slice(
  (minePage.value - 1) * MINE_PAGE_SIZE, minePage.value * MINE_PAGE_SIZE))
const pageRangeText = computed(() => {
  if (filteredMine.value.length === 0) return '0 至 0'
  const from = (minePage.value - 1) * MINE_PAGE_SIZE + 1
  const to = Math.min(filteredMine.value.length, minePage.value * MINE_PAGE_SIZE)
  return `${from} 至 ${to}`
})
const pageNumbers = computed(() => {
  const total = pageCount.value
  const cur = minePage.value
  const pages = new Set<number>([1, total, cur - 1, cur, cur + 1])
  return [...pages].filter((p) => p >= 1 && p <= total).sort((a, b) => a - b)
})
watch([stateFilter, typeFilter], () => { minePage.value = 1 })
watch(pageCount, (n) => { if (minePage.value > n) minePage.value = n })

/* 行 pill 短标签：求片类行用「求片/追新」（照友站 2.6），工单行沿用全称 */
function rowTypeLabel(row: UnifiedRow): string {
  if (row.source === 'seek') return row.kind === 'chase' ? '追新' : '求片'
  return typeLabel(row.type)
}
/* 行正文摘要：求片类行是提交时的具体说明；工单行是问题类型/附议数 */
function rowSummary(row: UnifiedRow): string {
  if (row.source === 'seek') {
    const parts: string[] = []
    if (row.note) parts.push(row.note)
    else if (row.seasonLabel) parts.push(row.seasonLabel)
    if (row.voteCount) parts.push(`${row.voteCount} 人附议`)
    return parts.join(' · ')
  }
  if (row.type === 'feedback' && row.categoryLabel) return row.categoryLabel
  return ''
}
/* 行点击分化：求片类行进作品请求详情页；工单行在本页展开线程
   （管理员回复的老口径保留在我的请求详情里） */
function onMineRowClick(row: UnifiedRow): void {
  if (row.source === 'seek') {
    void router.push({
      path: '/game/requests/media',
      query: {
        ...(row.tmdbId ? { tmdb: row.tmdbId } : {}),
        name: row.movieName || row.title,
        ...(row.year ? { year: row.year } : {}),
        ...(row.workType ? { type: row.workType } : {}),
      },
    })
    return
  }
  toggleDetail(row)
}

const isChase = computed(() => reqType.value === 'chase_movie' || reqType.value === 'chase_series')

function syncFromQuery(): void {
  const t = route.query.tab
  if (t === 'mine' || t === 'plaza' || t === 'create') tab.value = t
  const ty = route.query.type
  if (ty === 'seek' || ty === 'chase_movie' || ty === 'chase_series' || ty === 'correction' || ty === 'feedback') reqType.value = ty
  /* 旧链接兼容：?type=chase 是五类型之前的写法，旧追新以剧集为主，落剧集追新 */
  else if (ty === 'chase') reqType.value = 'chase_series'
}
function setTab(t: TabKey): void {
  tab.value = t
  const query: Record<string, string> = { tab: t }
  if (t === 'create') query.type = reqType.value
  void router.replace({ query })
}
function setType(t: ReqType): void {
  reqType.value = t
  void router.replace({ query: { tab: 'create', type: t } })
}
/* 三档切换落到具体类型：追新档落 chase_series 占位（电影/剧集由搜到的
   作品本身决定，reqType 只用来区分当前在哪一档）；
   资源反馈档若已在修正信息/其他反馈内则不动当前选择 */
function setSegGroup(g: SegGroup): void {
  if (g === 'seek') setType('seek')
  else if (g === 'chase') setType('chase_series')
  else if (reqType.value !== 'correction' && reqType.value !== 'feedback') setType('feedback')
}
/* 资源反馈档问题类型网格：修正信息切换类型本身，其余项切 fbCategory */
function setFbOption(value: string): void {
  if (value === 'correction') {
    setType('correction')
    return
  }
  fbCategory.value = value
  if (reqType.value !== 'feedback') setType('feedback')
}
let mineSeq = 0
async function loadMine(silent = false): Promise<void> {
  const seq = ++mineSeq
  if (!silent) { mineLoading.value = true; mineError.value = '' }
  try {
    const [seekRes, ticketRes] = await Promise.all([
      mediaSeekApi.getMyRequests(),
      ticketApi.getMyTickets(),
    ])
    if (seq !== mineSeq) return
    myRequests.value = seekRes.requests || []
    myQuota.value = (seekRes.quota as QuotaExt) || null
    tickets.value = (ticketRes as Ticket[]) || []
  } catch (e) {
    if (seq !== mineSeq) return
    if (!myRequests.value.length && !tickets.value.length) mineError.value = humanizeError(e)
    else toast.error(humanizeError(e))
  } finally {
    if (seq === mineSeq) mineLoading.value = false
  }
}
let plazaSeq = 0
async function loadPlaza(): Promise<void> {
  const seq = ++plazaSeq
  plazaLoading.value = true
  plazaError.value = ''
  try {
    const params: { status?: string; kind?: string; q?: string } = {}
    if (plazaStateFilter.value !== 'all') params.status = plazaStateFilter.value
    if (plazaTypeFilter.value !== 'all') params.kind = plazaTypeFilter.value
    if (plazaQ.value) params.q = plazaQ.value
    const r = await mediaSeekApi.plaza(params)
    if (seq !== plazaSeq) return
    plazaGroups.value = r.groups || []
  } catch (e) {
    if (seq !== plazaSeq) return
    plazaError.value = humanizeError(e)
  } finally {
    if (seq === plazaSeq) plazaLoading.value = false
  }
}
function applyPlazaSearch(): void {
  plazaQ.value = plazaQuery.value.trim()
  void loadPlaza()
}
/* 广场卡「查看」跳标题详情页（2026-11-14）：组内请求与附议都在详情页，
   不再内联展开（与友站广场→详情的流程一致） */
function goMediaDetail(g: PlazaGroup): void {
  void router.push({
    path: '/game/requests/media',
    query: {
      ...(g.tmdb_id ? { tmdb: g.tmdb_id } : {}),
      name: g.movie_name,
      ...(g.year ? { year: g.year } : {}),
      ...(g.type ? { type: g.type } : {}),
    },
  })
}
watch([plazaStateFilter, plazaTypeFilter], () => {
  void loadPlaza()
})
function highlight(key: string): void {
  justAddedKey.value = key
  window.setTimeout(() => { if (justAddedKey.value === key) justAddedKey.value = null }, 1700)
}

/* ===== 作品搜索（求片/追新共用）：防抖搜 TMDB 候选，后端逐条标库内
   状态与未完成请求（open_seek/open_chase），行行动分化在结果组件里 ===== */
function onWishInput(): void {
  emptyWarn.value = false
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
  searchStatus.value = 'loading'
  searchFor.value = q
  try {
    const r = await mediaSeekApi.search(q)
    if (seq !== searchSeq) return
    if (q !== wishText.value.trim()) return
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
function openSubmitModal(work: SubmitWork): void {
  modalMode.value = reqType.value === 'seek' ? 'seek' : 'chase'
  modalWork.value = work
  modalOpen.value = true
}
/* 原名兜底：搜不到/搜不了时按输入的名字开弹层（只求片档有） */
function openFallbackModal(): void {
  const name = wishText.value.trim()
  if (!name) return
  modalMode.value = 'seek'
  modalWork.value = { name, kind: '' }
  modalOpen.value = true
}
function goExistingWork(c: MediaSeekCandidate): void {
  void router.push({
    path: '/game/requests/media',
    query: { tmdb: c.tmdb_id, name: c.name, year: c.year, type: c.kind },
  })
}
/* 提交成功（照友站 2.3）：关弹层、刷新各面、跳作品请求详情页 */
function onModalSubmitted(): void {
  const w = modalWork.value
  modalOpen.value = false
  void loadMine(true)
  void loadPlaza()
  if (w) {
    void router.push({
      path: '/game/requests/media',
      query: {
        ...(w.tmdb_id ? { tmdb: w.tmdb_id } : {}),
        name: w.name,
        ...(w.year ? { year: w.year } : {}),
        ...(w.kind ? { type: w.kind } : {}),
      },
    })
  }
}
/* 提交资源反馈档：fbSelected=correction 时 category 就是 'correction'
   （与原修正信息提交同一条工单链路），其余为其他反馈的问题类型 */
async function submitFeedback(): Promise<void> {
  const title = fbTitle.value.trim()
  const message = fbMessage.value.trim()
  if (!title || !message) { toast.error('先写清楚标题和问题描述，再提交'); return }
  if (fbSending.value) return
  fbSending.value = true
  try {
    const r = await ticketApi.create({ category: fbSelected.value, title, message })
    toast.success(r.message || (fbSelected.value === 'correction' ? '修正信息已提交' : '反馈已提交，我们会尽快处理'))
    fbTitle.value = ''
    fbMessage.value = ''
    await loadMine(true)
    setTab('mine')
    highlight(`ticket:${r.ticket_id}`)
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    fbSending.value = false
  }
}

function toggleDetail(row: UnifiedRow): void {
  const key = row.source + ':' + row.id
  expandedKey.value = expandedKey.value === key ? null : key
}
function onThreadChanged(): void {
  void loadMine(true)
}
/* 外部预填（/want?q= 深链、作品详情页「资源反馈」带过来的片名）：
   求片/追新档填进搜索框立即搜；反馈档填进标题（只填空，不覆盖手输） */
watch(prefillQ, (q) => {
  const text = (q || '').trim()
  if (!text) return
  if (segGroup.value === 'feedback') {
    if (!fbTitle.value) fbTitle.value = text
    return
  }
  wishText.value = text
  void runSearch(text)
}, { immediate: true })
watch(() => route.query, () => syncFromQuery())
onMounted(() => {
  syncFromQuery()
  void loadMine()
  void loadPlaza()
})
</script>

<style scoped>
/* 求片反馈页：缺省深色（§6.4），html[data-theme='light'] 时换浅（§6.1–6.3）。
   数值来源：实测清单 §10（H1 30–32px/800、页签下划线 2px、类型分段 211×36–40
   圆角 19–20、搜索框 50 高圆角 25、筛选胶囊 481×38–40、状态圆点 6px、
   空态虚线卡 1006×210 圆角 16、底部留白 200px+；列表行未测到，按同构推定）。 */
/* 页面自带深色底（PersonalCenterView #91 模式）：UhdAccountLayout 外壳恒为浅色
   （--uhd-l-bg），嵌套页不自带底就会白字落浅底；.rf-page 以负边距漫过布局
   main 的内边距（桌面 28px 32px 120px），把深色铺满整块内容区；浅色时与外壳
   同色 #f8f8f8 融合。内容宽度仍由 .rf-inner 的 1006px 约束。 */
.rf-page { margin: -28px -32px -120px; padding: 28px 32px 220px; background: #08080a; min-height: 100dvh; }
.rf-inner { max-width: 1006px; margin: 0 auto; }
.rf-head { margin-bottom: 18px; display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.rf-head__text { min-width: 0; }
.rf-head h1 { margin: 0 0 6px; font-size: 31px; font-weight: 800; color: #fbfcfd; line-height: 1.2; }
.rf-head p { margin: 0; font-size: 14px; color: #9ca3af; line-height: 1.6; }
/* 顶栏（照友站）：返回＋小标题＋右上观影钮，透明底与页面同面 */
.rf-topbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.rf-back {
  flex: none; width: 38px; height: 38px; margin-left: -8px;
  display: inline-flex; align-items: center; justify-content: center;
  border: none; border-radius: 999px; background: transparent;
  color: #fbfcfd; cursor: pointer; font-family: inherit;
}
.rf-back:active { background: rgba(255, 255, 255, 0.08); }
.rf-topbar__title { flex: 1; min-width: 0; font-size: 17px; font-weight: 800; color: #fbfcfd; }
/* 「网页观影」胶囊：彩虹描边＋玻璃底（照友站同钮，配方同订阅页 .sub-head-link／
   .btn-primary 的浅深两态）：深色＝深玻璃底＋白字，浅色＝粉彩底＋黑字 */
.rf-watch {
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
.rf-tabs { display: flex; gap: 28px; border-bottom: 1px solid #26262a; margin-bottom: 20px; }
.rf-tabs__btn {
  position: relative; padding: 10px 2px 12px; background: none; border: none; cursor: pointer;
  font-size: 15px; color: #9ca3af; font-family: inherit;
}
.rf-tabs__btn--on { color: #fbfcfd; font-weight: 800; }
.rf-tabs__btn--on::after {
  content: ""; position: absolute; left: 50%; bottom: -1px; transform: translateX(-50%);
  width: 68px; height: 2px; background: #fbfcfd; border-radius: 2px;
}
.rf-create { display: flex; flex-direction: column; gap: 16px; }
.rf-seg {
  display: flex; width: fit-content; min-width: 211px; height: 38px; padding: 3px;
  background: #1b1c1e; border: 1px solid #2c2d30; border-radius: 19px;
}
.rf-seg__btn {
  flex: 1; border: none; background: transparent; cursor: pointer;
  /* 退出 mobile.css 全局 button min-height:44px，否则钮高 44 溢出 38 高的段容器 */
  min-height: 0;
  font-size: 12px; color: #9ca3af; border-radius: 16px; padding: 0 14px; font-family: inherit; white-space: nowrap;
}
.rf-seg__btn--on { background: #2a2b2d; color: #fbfcfd; font-weight: 700; }
.rf-quota { margin: 0; font-size: 12px; color: #9ca3af; }
.rf-create__body { display: flex; flex-direction: column; gap: 16px; }
.rf-form { display: flex; flex-direction: column; gap: 12px; }
.rf-form__label { font-size: 13px; font-weight: 700; color: #fbfcfd; }
.rf-form__input, .rf-form__area {
  width: 100%; font-size: 15px; color: #fbfcfd; font-family: inherit;
  background: #242526; border: 1px solid #232426; border-radius: 12px; outline: none;
}
.rf-form__input { height: 48px; padding: 0 14px; }
.rf-form__area { min-height: 96px; padding: 12px 14px; resize: none; line-height: 1.7; }
.rf-form__input::placeholder, .rf-form__area::placeholder { color: #9ca3af; }
.rf-form__input:focus, .rf-form__area:focus { border-color: #6b7280; }
.rf-types { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.rf-type {
  background: #17181a; border: 1.5px solid #26262a; border-radius: 12px;
  padding: 14px 8px; min-height: 64px; cursor: pointer;
  font-size: 13px; font-weight: 700; color: #fbfcfd; font-family: inherit;
}
.rf-type.on { border-color: #fbfcfd; background: #1e1f21; }
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

/* 作品搜索框（照友站 2.1）：50 高圆角 25 输入（内左放大镜）＋50×50 圆形
   彩虹描边搜索钮；搜索中钮内转 spinner */
.rf-search { display: flex; gap: 10px; position: relative; }
.rf-search__icon {
  position: absolute; left: 16px; top: 0; height: 50px;
  display: inline-flex; align-items: center; pointer-events: none;
  color: #9ca3af;
}
.rf-search__input {
  flex: 1; min-width: 0; height: 50px; padding: 0 18px 0 42px;
  font-size: 15px; color: #fbfcfd; font-family: inherit;
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
.rf-search__spinner {
  width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid rgba(0, 0, 0, 0.2); border-top-color: #111111;
  animation: rf-spin 0.8s linear infinite;
}
@keyframes rf-spin { to { transform: rotate(360deg); } }
.rf-hint--err { font-size: 12px; color: #fca5a5; }

.rf-list { display: flex; flex-direction: column; gap: 14px; }
/* 广场搜索框（照友站 2.7）：「搜索」钮嵌在输入框内右侧（彩虹描边胶囊） */
.rf-searchbar { position: relative; }
.rf-searchbar__input {
  width: 100%; height: 50px; padding: 0 108px 0 18px;
  font-size: 15px; color: #fbfcfd; font-family: inherit;
  background: #242526; border: 1px solid #232426; border-radius: 25px; outline: none;
}
.rf-searchbar__input::placeholder { color: #9ca3af; }
.rf-searchbar__input:focus { border-color: #6b7280; }
.rf-searchbar__btn {
  position: absolute; right: 5px; top: 5px; height: 40px; padding: 0 20px; border-radius: 20px;
  font-size: 14px; font-weight: 700; color: #fbfcfd; cursor: pointer; font-family: inherit;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box;
}
/* 筛选容器条（照友站 2.6/2.7）：chips 装在圆角容器里，全部＝填充 pill、
   其余＝文字＋色点；一行放不下横向滚动 */
.rf-filterbar {
  background: #17181a; border: 1px solid #26262a; border-radius: 999px;
  padding: 4px 6px;
}
.rf-statchips {
  display: flex; gap: 4px; max-width: 100%;
  overflow-x: auto; scrollbar-width: none;
}
.rf-statchips::-webkit-scrollbar { display: none; }
.rf-statchip {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid transparent; background: transparent; cursor: pointer;
  font-size: 13px; color: #9ca3af; border-radius: 999px; padding: 7px 12px;
  font-family: inherit; white-space: nowrap;
}
.rf-statchip--on { background: #2a2b2d; border-color: #333336; color: #fbfcfd; font-weight: 700; }
.rf-filter__select {
  width: 134px; height: 38px; border-radius: 19px; border: 1px solid #26262a;
  background: #1e1f21; color: #fbfcfd; font-size: 13px; padding: 0 10px; outline: none; font-family: inherit;
}
/* 类型下拉整宽（照友站 2.6：高约 50、圆角 25） */
.rf-filter__select--wide { width: 100%; height: 50px; border-radius: 25px; padding: 0 16px; font-size: 14px; }
/* 单容器分隔行（我的请求/广场共用，照友站 2.6/2.7）：海报 43×60＋片名行
   （类型 pill＋状态 pill）＋正文摘要一行＋右侧 › */
.rf-minebox {
  background: #17181a; border: 1px solid #26262a; border-radius: 16px;
  padding: 4px 16px;
}
.rf-mrow { padding: 12px 0; }
.rf-mrow + .rf-mrow { border-top: 1px solid #26262a; }
.rf-mrow--new { animation: rf-glow 1.6s; }
@keyframes rf-glow { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
.rf-mrow__head {
  display: flex; align-items: center; gap: 12px;
  width: 100%; background: none; border: none; cursor: pointer; text-align: left;
  font-family: inherit; color: #fbfcfd; padding: 0;
}
.rf-mrow__head--static { cursor: default; }
.rf-mrow__poster { flex: none; width: 43px; height: 60px; object-fit: cover; border-radius: 8px; background: #1e1f21; }
.rf-mrow__poster--ph {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 17px; font-weight: 800; color: #55575c;
}
.rf-mrow__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.rf-mrow__titlerow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rf-mrow__title { font-size: 15px; font-weight: 700; word-break: break-word; }
.rf-mrow__summary {
  font-size: 12px; color: #9ca3af; line-height: 1.5;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* 广场计数行不截断（数字是这行的主信息，宁可换行） */
.rf-mrow__summary--wrap { white-space: normal; overflow: visible; text-overflow: clip; }
.rf-pill {
  flex: none; display: inline-flex; align-items: center; height: 22px; padding: 0 8px;
  border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap;
  border: 1px solid #333336; color: #9ca3af;
}
.rf-pill--type { background: #26262a; color: #d4d4d8; border-color: #26262a; }
/* 状态 pill：填充深色胶囊＋状态色字（照友站 2.6） */
.rf-pill--state { background: #26262a; border-color: #26262a; }
.rf-pill--pending { color: #d4d4d8; }
.rf-pill--doing { color: #f59e0b; }
.rf-pill--done { color: #4ade80; }
.rf-pill--rejected { color: #f87171; }
.rf-pill--withdrawn { color: #9ca3af; }
.rf-row__chev { flex: none; font-size: 20px; color: #9ca3af; line-height: 1; transition: transform 0.15s ease; }
.rf-row__chev--open { transform: rotate(90deg); }
.rf-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.rf-dot--pending { background: #9ca3af; }
.rf-dot--doing { background: #f59e0b; }
.rf-dot--done { background: #16a34a; }
.rf-dot--rejected { background: #ef4444; }
.rf-dot--withdrawn { background: #9ca3af; }
.rf-row__detail { margin-top: 12px; border-top: 1px dotted #333336; padding-top: 12px; }
/* 分页（照友站 2.6）：计数行＋« ‹ 页码 › » */
.rf-pager { display: flex; flex-direction: column; align-items: center; gap: 10px; }
.rf-pager__info { font-size: 12px; color: #9ca3af; }
.rf-pager__nav { display: flex; gap: 6px; align-items: center; }
.rf-pager__btn {
  min-width: 34px; height: 34px; padding: 0 8px; border-radius: 10px; cursor: pointer;
  background: #17181a; border: 1px solid #26262a; color: #d4d4d8;
  font-size: 13px; font-weight: 700; font-family: inherit;
}
.rf-pager__btn--on { background: #fbfcfd; color: #111111; border-color: #fbfcfd; }
.rf-pager__btn:disabled { opacity: 0.4; cursor: default; }
.rf-badge {
  flex: none; display: inline-flex; align-items: center; height: 22px; padding: 0 8px;
  border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap;
}
.rf-badge--green { color: #4ade80; }
.rf-badge--gray { background: #26262a; color: #9ca3af; }
.rf-topic__sep { margin: 0 6px; }
.rf-topic__view {
  flex: none; display: inline-flex; align-items: center; gap: 2px;
  background: none; border: none; cursor: pointer; padding: 6px 2px;
  font-size: 14px; font-weight: 700; color: #fbfcfd; font-family: inherit; white-space: nowrap;
}
.rf-topic__chev { font-size: 18px; color: #9ca3af; line-height: 1; }
.rf-empty {
  display: flex; align-items: center; justify-content: center; text-align: center;
  min-height: 210px; border: 1px dashed #3a3b3f; border-radius: 16px;
  font-size: 14px; color: #8a8f96; padding: 20px;
}
.rf-err { font-size: 13px; color: #fca5a5; line-height: 1.6; }
.rf-retry { margin-top: 6px; padding: 3px 12px; font-size: 12px; color: #fbfcfd; background: transparent; border: 1px solid currentColor; border-radius: 999px; cursor: pointer; opacity: 0.8; }
.rf-skeleton { background: #1e1f21; border-radius: 10px; animation: rf-pulse 1.4s ease-in-out infinite; }
@keyframes rf-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
@media (max-width: 768px) {
  .rf-page { margin: -20px -16px -120px; padding: 20px 16px 120px; }
  .rf-types { grid-template-columns: repeat(3, 1fr); }
  /* 三档分段在窄屏收紧内边距，保证一排排得下 */
  .rf-seg__btn { padding: 0 8px; }
}

html[data-theme='light'] .rf-page { background: #f8f8f8; }
html[data-theme='light'] .rf-head h1 { color: #111111; }
html[data-theme='light'] .rf-head p { color: #6b7280; }
html[data-theme='light'] .rf-tabs { border-bottom-color: #e5e5e5; }
html[data-theme='light'] .rf-tabs__btn { color: #6b7280; }
html[data-theme='light'] .rf-tabs__btn--on { color: #06070a; }
html[data-theme='light'] .rf-tabs__btn--on::after { background: #06070a; }
html[data-theme='light'] .rf-topbar__title { color: #111111; }
html[data-theme='light'] .rf-back { color: #111111; }
html[data-theme='light'] .rf-back:active { background: rgba(0, 0, 0, 0.05); }
html[data-theme='light'] .rf-seg { background: #f5f5f6; border-color: #e3e3e6; }
html[data-theme='light'] .rf-seg__btn { color: #6b7280; }
html[data-theme='light'] .rf-seg__btn--on { background: #ffffff; color: #111111; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
html[data-theme='light'] .rf-form__label { color: #111111; }
html[data-theme='light'] .rf-form__input, html[data-theme='light'] .rf-form__area { background: #f5f5f5; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-form__input::placeholder, html[data-theme='light'] .rf-form__area::placeholder { color: #9ca3af; }
html[data-theme='light'] .rf-form__input:focus, html[data-theme='light'] .rf-form__area:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-type { background: #ffffff; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-type.on { border-color: #111111; background: #f9f9f9; }
html[data-theme='light'] .rf-submit {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rf-watch {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rf-quota { color: #6b7280; }
html[data-theme='light'] .rf-hint--err { color: #dc2626; }
html[data-theme='light'] .rf-search__input { background: #f0f0f1; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-search__input:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-searchbar__input { background: #f0f0f1; border-color: #e8e8ea; color: #111111; }
html[data-theme='light'] .rf-searchbar__input::placeholder { color: #9ca3af; }
html[data-theme='light'] .rf-searchbar__input:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-searchbar__btn {
  background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box;
  color: #111111;
}
html[data-theme='light'] .rf-filterbar { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rf-statchip { color: #6b7280; }
html[data-theme='light'] .rf-statchip--on { background: #111111; border-color: #111111; color: #ffffff; }
html[data-theme='light'] .rf-badge--green { color: #16a34a; }
html[data-theme='light'] .rf-badge--gray { background: #f0f0f1; color: #6b7280; }
html[data-theme='light'] .rf-topic__view { color: #111111; }
html[data-theme='light'] .rf-topic__chev { color: #9ca3af; }
html[data-theme='light'] .rf-filter__select { background: #f6f8fa; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-minebox { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rf-mrow + .rf-mrow { border-top-color: #e5e5e5; }
html[data-theme='light'] .rf-mrow__head { color: #111111; }
html[data-theme='light'] .rf-mrow__poster { background: #f0f0f1; }
html[data-theme='light'] .rf-mrow__poster--ph { color: #9ca3af; }
html[data-theme='light'] .rf-mrow__summary { color: #6b7280; }
html[data-theme='light'] .rf-pill { border-color: #e5e5e5; color: #6b7280; }
html[data-theme='light'] .rf-pill--type { background: #f0f0f1; color: #55575c; border-color: #f0f0f1; }
html[data-theme='light'] .rf-pill--state { background: #f0f0f1; border-color: #f0f0f1; }
html[data-theme='light'] .rf-pill--pending { color: #55575c; }
html[data-theme='light'] .rf-pill--doing { color: #d97706; }
html[data-theme='light'] .rf-pill--done { color: #16a34a; }
html[data-theme='light'] .rf-pill--rejected { color: #dc2626; }
html[data-theme='light'] .rf-row__chev { color: #9ca3af; }
html[data-theme='light'] .rf-row__detail { border-top-color: #e5e5e5; }
html[data-theme='light'] .rf-pager__info { color: #6b7280; }
html[data-theme='light'] .rf-pager__btn { background: #ffffff; border-color: #e5e5e5; color: #55575c; }
html[data-theme='light'] .rf-pager__btn--on { background: #111111; color: #ffffff; border-color: #111111; }
html[data-theme='light'] .rf-empty { border-color: #d4d4d8; color: #8a8f96; }
html[data-theme='light'] .rf-err { color: #dc2626; }
html[data-theme='light'] .rf-retry { color: #111111; }
html[data-theme='light'] .rf-skeleton { background: #f0f0f1; }
</style>
