<template>
  <UhdAccountLayout>
    <!-- 求片反馈（2026-11-13 合并页＋同日返工补全）：许愿池＋求救信号合并为一页。
         三页签（发起请求/我的请求/请求广场），每页签一句副标题；发起请求是三档
         分段（求片/追新/资源反馈，映射我方 5 种类型：影片求片、电影/剧集追新、
         修正信息、其他反馈，映射见脚本 segGroups 注释）＋每档一套须知卡组；
         请求广场是后端真聚合的媒体卡（海报/库内已有/处理中/计数行）；
         视觉照 UHDNow 原站求片反馈页 1:1（实测清单 §10＋用户六张真机截图）；
         页面跟随用户外观。 -->
    <div class="rf-page">
      <div class="rf-inner">
      <header class="rf-head">
        <div class="rf-head__text">
          <h1>求片反馈</h1>
          <p>{{ tabSubtitle }}</p>
        </div>
        <!-- 「网页观影」胶囊：与「我的」页同款（彩虹三角＋胶囊），跳现行网页播放入口（片库面） -->
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
             与我方 5 种类型的映射：求片＝影片求片；追新＝电影追新/剧集追新
             （档内电影/剧集小切换）；资源反馈＝修正信息＋其他反馈的问题类型
             （表单内网格第一项就是修正信息）。每个类型都可达，不丢类型。 -->
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

        <div v-if="reqType === 'seek'" class="rf-create__body">
          <SeekCreateForm
            :my-requests="myRequests"
            :hot-requests="hotRequests"
            :wanted-ids="wantedIds"
            :prefill="prefillQ"
            @submitted="onSeekSubmitted"
            @wanted="onWanted"
          />
          <p v-if="monthlyRemaining != null" class="rf-quota">本月还可求片 {{ monthlyRemaining }} 次</p>
          <NoticeCards kind="seek" />
        </div>

        <div v-else-if="isChase" class="rf-create__body">
          <div class="rf-seg rf-seg--mini" role="group" aria-label="追新类型">
            <button
              type="button"
              class="rf-seg__btn"
              :class="{ 'rf-seg__btn--on': chaseMedia === 'movie' }"
              :aria-pressed="chaseMedia === 'movie'"
              @click="setType('chase_movie')"
            >电影</button>
            <button
              type="button"
              class="rf-seg__btn"
              :class="{ 'rf-seg__btn--on': chaseMedia === 'series' }"
              :aria-pressed="chaseMedia === 'series'"
              @click="setType('chase_series')"
            >剧集</button>
          </div>
          <div class="rf-form">
            <label class="rf-form__label" for="rf-chase-title">{{ chaseCopy.titleLabel }}</label>
            <input
              id="rf-chase-title"
              v-model="chaseTitle"
              class="rf-form__input"
              type="text"
              :placeholder="chaseCopy.titlePh"
              maxlength="100"
            />
            <label class="rf-form__label" for="rf-chase-msg">{{ chaseCopy.msgLabel }}</label>
            <textarea
              id="rf-chase-msg"
              v-model="chaseMessage"
              class="rf-form__area"
              :placeholder="chaseCopy.msgPh"
              maxlength="1000"
            ></textarea>
            <button class="rf-submit" :disabled="chaseSending" @click="submitChase">{{ chaseSending ? '提交中…' : '提交追新' }}</button>
          </div>
          <NoticeCards kind="chase" />
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
      </section>

      <!-- 我的请求 -->
      <section v-else-if="tab === 'mine'" class="rf-list">
        <div class="rf-filter">
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
          <select v-model="typeFilter" class="rf-filter__select" aria-label="按类型筛选">
            <option value="all">全部类型</option>
            <option value="seek">影片求片</option>
            <option value="chase_movie">电影追新</option>
            <option value="chase_series">剧集追新</option>
            <option value="correction">修正信息</option>
            <option value="feedback">其他反馈</option>
          </select>
        </div>

        <div v-if="mineLoading" class="rf-loading">
          <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 76px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="mineError" class="rf-err">
          请求记录读取失败：{{ mineError }}<br />
          <button class="rf-retry" @click="() => loadMine()">重试</button>
        </div>
        <div v-else-if="filteredMine.length === 0" class="rf-empty">暂无符合条件的请求</div>
        <div v-else class="rf-rows">
          <div
            v-for="row in filteredMine"
            :key="row.source + ':' + row.id"
            class="rf-row"
            :class="{ 'rf-row--new': justAddedKey === row.source + ':' + row.id }"
          >
            <button class="rf-row__head" :aria-expanded="expandedKey === row.source + ':' + row.id" @click="toggleDetail(row)">
              <span class="rf-row__main">
                <span class="rf-row__title">
                  <span class="rf-dot" :class="'rf-dot--' + row.state" aria-hidden="true"></span>
                  {{ row.title }}
                </span>
                <span class="rf-row__meta">
                  {{ typeLabel(row.type) }}<span v-if="row.type === 'feedback' && row.categoryLabel"> · {{ row.categoryLabel }}</span>
                  · {{ stateLabel(row.state) }}
                  · {{ formatDate(row.updated_at) }}
                  <span v-if="row.source === 'seek' && row.voteCount"> · {{ row.voteCount }} 人附议</span>
                </span>
              </span>
              <span class="rf-row__chev" :class="{ 'rf-row__chev--open': expandedKey === row.source + ':' + row.id }" aria-hidden="true">›</span>
            </button>
            <div v-if="expandedKey === row.source + ':' + row.id" class="rf-row__detail">
              <template v-if="row.source === 'seek'">
                <p class="rf-detail__line">片名：{{ row.movieName }}<span v-if="row.year">（{{ row.year }}）</span></p>
                <p v-if="row.seasonLabel" class="rf-detail__line">季：{{ row.seasonLabel }}</p>
                <p v-if="row.titleOnly" class="rf-detail__line">按原名提交，待人工确认</p>
                <p v-if="row.voteCount" class="rf-detail__line">附议：{{ row.voteCount }} 人</p>
                <button
                  v-if="row.state === 'done' && row.embyItemId"
                  type="button"
                  class="rf-link"
                  @click="goDetail(row.embyItemId)"
                >去看这部</button>
              </template>
              <TicketThread v-else :ticket="row.rawTicket" @changed="onThreadChanged" />
            </div>
          </div>
        </div>
      </section>

      <!-- 请求广场：按媒体聚合卡（后端 /media-seek/plaza 真聚合，一部片一张卡）。
           广场只放求片数据：工单类类型（电影追新/修正信息/其他反馈）不公开，
           选到时直接显示不公开空态；剧集追新在我方是补缺集行（chase 计数）。 -->
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
        <div class="rf-filter">
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
          <select v-model="plazaTypeFilter" class="rf-filter__select" aria-label="按类型筛选">
            <option value="all">全部类型</option>
            <option value="seek">影片求片</option>
            <option value="chase_movie">电影追新</option>
            <option value="chase_series">剧集追新</option>
            <option value="correction">修正信息</option>
            <option value="feedback">其他反馈</option>
          </select>
        </div>

        <div v-if="plazaLoading" class="rf-loading">
          <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 140px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="plazaError" class="rf-err">
          广场加载失败：{{ plazaError }}<br />
          <button class="rf-retry" @click="loadPlaza">重试</button>
        </div>
        <div v-else-if="plazaEmptyText" class="rf-empty">{{ plazaEmptyText }}</div>
        <div v-else-if="plazaGroups.length === 0" class="rf-empty">暂无符合条件的请求</div>
        <div v-else class="rf-topics">
          <article v-for="g in plazaGroups" :key="g.key" class="rf-topic">
            <div class="rf-topic__main">
              <img
                v-if="g.poster_url"
                class="rf-topic__poster"
                :src="imageAuthUrl(g.poster_url, 200)"
                :alt="g.movie_name"
                loading="lazy"
              />
              <span v-else class="rf-topic__ph" aria-hidden="true">{{ (g.movie_name || '片').slice(0, 1) }}</span>
              <div class="rf-topic__info">
                <div class="rf-topic__titlerow">
                  <h3 class="rf-topic__title">{{ g.movie_name }}</h3>
                  <span v-if="g.in_library" class="rf-badge rf-badge--green">库内已有</span>
                  <span v-if="g.processing_count > 0" class="rf-badge rf-badge--gray">{{ g.processing_count }} 条处理中</span>
                </div>
                <p class="rf-topic__counts">
                  求片 {{ g.seek_count }}<span class="rf-topic__sep" aria-hidden="true">·</span>追新 {{ g.chase_count }}<span class="rf-topic__sep" aria-hidden="true">·</span>反馈 {{ g.feedback_count }}<span class="rf-topic__sep" aria-hidden="true">·</span>{{ g.total_count }} 条记录
                </p>
              </div>
              <button
                type="button"
                class="rf-topic__view"
                :aria-expanded="expandedPlaza === g.key"
                @click="togglePlaza(g.key)"
              >查看 <span class="rf-topic__chev" :class="{ 'rf-topic__chev--open': expandedPlaza === g.key }" aria-hidden="true">›</span></button>
            </div>
            <div v-if="expandedPlaza === g.key" class="rf-topic__detail">
              <div v-for="r in g.requests" :key="r.id" class="rf-topic__req">
                <div class="rf-topic__reqmain">
                  <span class="rf-topic__reqtitle">
                    {{ r.request_kind === 'missing_episodes' ? '补缺集' : '求片' }}<template v-if="r.season_label"> · {{ r.season_label }}</template>
                  </span>
                  <span class="rf-topic__reqmeta">
                    {{ r.vote_count > 0 ? r.vote_count + ' 人想看' : '还没有人附议' }} · {{ formatDate(r.created_at) }}
                  </span>
                  <p v-if="r.missing_text" class="rf-topic__missing">{{ r.missing_text }}</p>
                </div>
                <button
                  class="rf-vote rf-vote--sm"
                  :class="{ 'rf-vote--done': r.voted }"
                  :disabled="r.voted || voting !== null"
                  @click="upvote(r)"
                >{{ r.voted ? '✓ 已附议' : voting === r.id ? '附议中' : '+1 附议' }}</button>
              </div>
              <p v-if="g.requests.length === 0" class="rf-topic__noreq">这个话题下暂时没有处理中的求片</p>
              <button
                v-if="g.in_library && g.emby_item_id"
                type="button"
                class="rf-link"
                @click="goDetail(g.emby_item_id)"
              >去看这部</button>
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
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import NoticeCards from '@/components/requests/NoticeCards.vue'
import SeekCreateForm from '@/components/requests/SeekCreateForm.vue'
import TicketThread from '@/components/requests/TicketThread.vue'
import {
  mediaSeekApi, storeApi, ticketApi,
  type HotMediaSeekRequest, type MediaSeekQuota, type MediaSeekRequest, type PlazaGroup, type Ticket,
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
const hotRequests = ref<HotMediaSeekRequest[]>([])
type QuotaExt = MediaSeekQuota & { monthly_remaining?: number; monthly_limit?: number; monthly_used?: number }
const myQuota = ref<QuotaExt | null>(null)
const monthlyRemaining = computed(() => myQuota.value?.monthly_remaining ?? null)
const wantedIds = ref(new Set<string>())

const mineLoading = ref(true)
const mineError = ref('')
const hotLoading = ref(true)
const hotError = ref('')
const voting = ref<number | null>(null)
const expandedKey = ref<string | null>(null)
const justAddedKey = ref<string | null>(null)
const stateFilter = ref<'all' | UnifiedState>('all')
const typeFilter = ref<'all' | RowType>('all')
const plazaStateFilter = ref<'all' | UnifiedState>('all')
const plazaTypeFilter = ref<'all' | RowType>('all')
/* 广场状态 chips 不含「已撤回」（撤回是自己的取消，后端口径也不进广场） */
const plazaStateOptions = stateOptions.filter((s) => s.key !== 'withdrawn')
const plazaGroups = ref<PlazaGroup[]>([])
const plazaLoading = ref(true)
const plazaError = ref('')
const plazaQuery = ref('')
const plazaQ = ref('')
const expandedPlaza = ref<string | null>(null)

const chaseTitle = ref('')
const chaseMessage = ref('')
const chaseSending = ref(false)
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
    rows.push({
      source: 'seek', id: w.id, type: 'seek', title: w.movie_name,
      created_at: w.created_at, updated_at: w.created_at, state: seekState(w.status),
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
/* 广场聚合走后端 /media-seek/plaza（按媒体身份真聚合，前端不拼数）。
   工单类类型（电影追新/修正信息/其他反馈）不进广场：选到时不查接口、
   直接显示不公开空态；剧集追新在我方是求片的补缺集行，走 kind=chase。 */
const plazaTypeHidden = computed(() =>
  plazaTypeFilter.value === 'chase_movie'
  || plazaTypeFilter.value === 'correction'
  || plazaTypeFilter.value === 'feedback')
const plazaEmptyText = computed(() => {
  const f = plazaTypeFilter.value
  if (f !== 'chase_movie' && f !== 'correction' && f !== 'feedback') return ''
  return `${typeLabel(f)}走工单处理，不在广场公开显示，去「我的请求」里看自己的`
})
const isChase = computed(() => reqType.value === 'chase_movie' || reqType.value === 'chase_series')
const chaseMedia = computed<'movie' | 'series'>(() => (reqType.value === 'chase_movie' ? 'movie' : 'series'))
const chaseCopy = computed(() => (chaseMedia.value === 'movie'
  ? { titleLabel: '片名', titlePh: '比如：《长安的荔枝》', msgLabel: '想追的内容', msgPh: '比如：续集什么时候上映？' }
  : { titleLabel: '剧名', titlePh: '比如：《一念永恒》', msgLabel: '想追的季 / 集', msgPh: '比如：第 3 季第 5 集，什么时候更新？' }))

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
/* 三档切换落到具体类型：追新档保留当前电影/剧集选择（默认剧集）；
   资源反馈档若已在修正信息/其他反馈内则不动当前选择 */
function setSegGroup(g: SegGroup): void {
  if (g === 'seek') setType('seek')
  else if (g === 'chase') setType(chaseMedia.value === 'movie' ? 'chase_movie' : 'chase_series')
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
let hotSeq = 0
async function loadHot(): Promise<void> {
  const seq = ++hotSeq
  hotLoading.value = true
  hotError.value = ''
  try {
    const r = await mediaSeekApi.hot()
    if (seq !== hotSeq) return
    hotRequests.value = r.requests || []
  } catch (e) {
    if (seq !== hotSeq) return
    hotError.value = humanizeError(e)
  } finally {
    if (seq === hotSeq) hotLoading.value = false
  }
}
let plazaSeq = 0
async function loadPlaza(): Promise<void> {
  if (plazaTypeHidden.value) {
    plazaGroups.value = []
    plazaError.value = ''
    plazaLoading.value = false
    return
  }
  const seq = ++plazaSeq
  plazaLoading.value = true
  plazaError.value = ''
  try {
    const params: { status?: string; kind?: string; q?: string } = {}
    if (plazaStateFilter.value !== 'all') params.status = plazaStateFilter.value
    if (plazaTypeFilter.value === 'seek') params.kind = 'seek'
    else if (plazaTypeFilter.value === 'chase_series') params.kind = 'chase'
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
  expandedPlaza.value = null
  void loadPlaza()
}
function togglePlaza(key: string): void {
  expandedPlaza.value = expandedPlaza.value === key ? null : key
}
watch([plazaStateFilter, plazaTypeFilter], () => {
  expandedPlaza.value = null
  void loadPlaza()
})
/* 已想看集合（只为已在库候选卡的「想看/已想看」状态服务，不渲染想看列表） */
async function loadWantIds(): Promise<void> {
  try {
    const res = await storeApi.getWants()
    wantedIds.value = new Set(
      (res.items || [])
        .filter((w) => w.state !== 'failed' && w.state !== 'missing_source')
        .map((w) => w.tmdb_id),
    )
  } catch { /* 商店未开启/未登录会员时不显示已想看态 */ }
}
function onWanted(tmdbId: string): void {
  wantedIds.value = new Set(wantedIds.value).add(tmdbId)
}
function highlight(key: string): void {
  justAddedKey.value = key
  window.setTimeout(() => { if (justAddedKey.value === key) justAddedKey.value = null }, 1700)
}
function onSeekSubmitted(payload: { created: boolean; requestId?: number; message?: string }): void {
  void loadMine(true)
  void loadHot()
  void loadPlaza()
  setTab('mine')
  if (payload.created && payload.requestId) highlight(`seek:${payload.requestId}`)
}
async function submitChase(): Promise<void> {
  const title = chaseTitle.value.trim()
  const message = chaseMessage.value.trim()
  if (!title || !message) { toast.error(`先写清楚${chaseCopy.value.titleLabel}和想追的内容，再提交`); return }
  if (chaseSending.value) return
  chaseSending.value = true
  try {
    const r = await ticketApi.create({ category: chaseMedia.value === 'movie' ? 'chase_movie' : 'chase_series', title, message })
    toast.success(r.message || '追新已提交')
    chaseTitle.value = ''
    chaseMessage.value = ''
    await loadMine(true)
    setTab('mine')
    highlight(`ticket:${r.ticket_id}`)
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    chaseSending.value = false
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

async function upvote(w: HotMediaSeekRequest): Promise<void> {
  if (w.voted || voting.value) return
  voting.value = w.id
  try {
    const r = await mediaSeekApi.vote(w.id)
    w.voted = r.voted
    w.vote_count = r.vote_count
    w.weighted_vote_count = r.weighted_vote_count
  } catch {
    toast.error('附议失败，再试一次')
  } finally {
    voting.value = null
  }
}
function toggleDetail(row: UnifiedRow): void {
  const key = row.source + ':' + row.id
  expandedKey.value = expandedKey.value === key ? null : key
}
function onThreadChanged(): void {
  void loadMine(true)
}
function goDetail(embyItemId: string): void {
  void router.push(`/media/${embyItemId}`)
}
watch(() => route.query, () => syncFromQuery())
onMounted(() => {
  syncFromQuery()
  void loadMine()
  void loadHot()
  void loadPlaza()
  void loadWantIds()
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
/* 「网页观影」胶囊：与「我的」页 .watch-pill 同款形态（深色 #312d2e／浅色近白
   粉水洗底＋彩虹三角），尺寸按本页页头收一号（高 44） */
.rf-watch {
  flex: none; display: inline-flex; align-items: center; gap: 8px;
  height: 44px; padding: 0 20px; border-radius: 999px;
  background: #312d2e; color: #ffffff;
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
  background: #1b1c1e; border-radius: 19px;
}
.rf-seg__btn {
  flex: 1; border: none; background: transparent; cursor: pointer;
  /* 退出 mobile.css 全局 button min-height:44px，否则钮高 44 溢出 38 高的段容器 */
  min-height: 0;
  font-size: 12px; color: #9ca3af; border-radius: 16px; padding: 0 14px; font-family: inherit; white-space: nowrap;
}
.rf-seg__btn--on { background: #2a2b2d; color: #fbfcfd; font-weight: 700; }
.rf-seg--mini { min-width: 160px; }
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
.rf-submit {
  width: 100%; height: 50px; border-radius: 25px; border: none; cursor: pointer;
  font-size: 15px; font-weight: 800; background: #fbfcfd; color: #111111;
}
.rf-submit:disabled { opacity: 0.5; cursor: default; }

.rf-list { display: flex; flex-direction: column; gap: 14px; }
/* 广场搜索行：与求片搜索同构（50 高圆角 25 输入＋85×50 白底彩虹描边「搜索」钮） */
.rf-searchbar { display: flex; gap: 10px; }
.rf-searchbar__input {
  flex: 1; min-width: 0; height: 50px; padding: 0 18px;
  font-size: 15px; color: #fbfcfd; font-family: inherit;
  background: #242526; border: 1px solid #232426; border-radius: 25px; outline: none;
}
.rf-searchbar__input::placeholder { color: #9ca3af; }
.rf-searchbar__input:focus { border-color: #6b7280; }
.rf-searchbar__btn {
  flex: none; width: 85px; height: 50px; border-radius: 25px;
  font-size: 14px; font-weight: 700; color: #111111; cursor: pointer; font-family: inherit;
  border: 1.5px solid transparent;
  background: linear-gradient(#ffffff, #ffffff) padding-box, var(--uhd-rainbow-border) border-box;
}
.rf-filter { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
/* 状态 chips（原站截图形态）：未选＝透明底＋彩色圆点＋文字；选中＝胶囊
   （深色 #2a2b2d／浅色白底＋轻阴影）；一行放不下时横向滚动 */
.rf-statchips {
  display: flex; gap: 4px; max-width: 100%; padding: 2px;
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
.rf-rows { display: flex; flex-direction: column; gap: 10px; }
.rf-row {
  background: #17181a; border: 1px solid #26262a; border-radius: 16px; padding: 14px;
}
.rf-row--new { border-color: #f59e0b; animation: rf-glow 1.6s; }
@keyframes rf-glow { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
.rf-row__head {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
  width: 100%; background: none; border: none; cursor: pointer; text-align: left;
  font-family: inherit; color: #fbfcfd; padding: 0;
}
.rf-row__head--static { cursor: default; }
.rf-row__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.rf-row__title { font-size: 14px; font-weight: 700; word-break: break-word; display: flex; align-items: center; gap: 8px; }
.rf-row__meta { font-size: 12px; color: #9ca3af; }
.rf-row__chev { font-size: 20px; color: #9ca3af; line-height: 1; transition: transform 0.15s ease; }
.rf-row__chev--open { transform: rotate(90deg); }
.rf-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.rf-dot--pending { background: #9ca3af; }
.rf-dot--doing { background: #f59e0b; }
.rf-dot--done { background: #16a34a; }
.rf-dot--rejected { background: #ef4444; }
.rf-dot--withdrawn { background: #9ca3af; }
.rf-row__detail { margin-top: 12px; border-top: 1px dotted #333336; padding-top: 12px; }
.rf-detail__line { margin: 0 0 6px; font-size: 13px; color: #d4d4d8; }
.rf-link { background: none; border: none; color: #6ea8fe; font-size: 13px; font-weight: 700; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; padding: 0; }
/* 广场媒体聚合卡（原站截图形态）：海报 84×112＋片名行（徽章）＋计数行＋右侧「查看 ›」，
   点开后是组内可附议的 pending 明细行 */
.rf-topics { display: flex; flex-direction: column; gap: 10px; }
.rf-topic { background: #17181a; border: 1px solid #26262a; border-radius: 16px; padding: 14px; }
.rf-topic__main { display: flex; gap: 14px; align-items: center; }
.rf-topic__poster {
  flex: none; width: 84px; height: 112px; border-radius: 10px; object-fit: cover; background: #1e1f21;
}
.rf-topic__ph {
  flex: none; width: 84px; height: 112px; border-radius: 10px; background: #1e1f21;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 28px; font-weight: 800; color: #55575c;
}
.rf-topic__info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.rf-topic__titlerow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rf-topic__title { margin: 0; font-size: 16px; font-weight: 700; color: #fbfcfd; word-break: break-word; }
.rf-badge {
  flex: none; display: inline-flex; align-items: center; height: 22px; padding: 0 8px;
  border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap;
}
.rf-badge--green { background: rgba(22, 163, 74, 0.16); color: #4ade80; }
.rf-badge--gray { background: #26262a; color: #9ca3af; }
.rf-topic__counts { margin: 0; font-size: 13px; color: #9ca3af; }
.rf-topic__sep { margin: 0 6px; }
.rf-topic__view {
  flex: none; display: inline-flex; align-items: center; gap: 2px;
  background: none; border: none; cursor: pointer; padding: 6px 2px;
  font-size: 14px; font-weight: 700; color: #fbfcfd; font-family: inherit; white-space: nowrap;
}
.rf-topic__chev { font-size: 18px; color: #9ca3af; line-height: 1; transition: transform 0.15s ease; }
.rf-topic__chev--open { transform: rotate(90deg); }
.rf-topic__detail { margin-top: 12px; border-top: 1px dotted #333336; padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.rf-topic__req { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.rf-topic__reqmain { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.rf-topic__reqtitle { font-size: 13px; font-weight: 700; color: #fbfcfd; }
.rf-topic__reqmeta { font-size: 12px; color: #9ca3af; }
.rf-topic__missing { margin: 2px 0 0; font-size: 12px; color: #9ca3af; line-height: 1.6; }
.rf-topic__noreq { margin: 0; font-size: 13px; color: #9ca3af; }
.rf-vote {
  width: 100%; margin-top: 10px; min-height: 44px; border-radius: 10px; border: none; cursor: pointer;
  font-size: 13px; font-weight: 700; background: #fbfcfd; color: #111111;
}
.rf-vote--sm { flex: none; width: auto; margin-top: 0; min-height: 36px; padding: 0 14px; border-radius: 999px; }
.rf-vote--done { background: #16a34a; color: #ffffff; }
.rf-vote:disabled { cursor: default; }
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
  .rf-filter__select { width: 120px; }
  /* 五类型分段在窄屏收紧内边距，保证一排排得下 */
  .rf-seg__btn { padding: 0 8px; }
}

html[data-theme='light'] .rf-page { background: #f8f8f8; }
html[data-theme='light'] .rf-head h1 { color: #111111; }
html[data-theme='light'] .rf-head p { color: #6b7280; }
html[data-theme='light'] .rf-tabs { border-bottom-color: #e5e5e5; }
html[data-theme='light'] .rf-tabs__btn { color: #6b7280; }
html[data-theme='light'] .rf-tabs__btn--on { color: #06070a; }
html[data-theme='light'] .rf-tabs__btn--on::after { background: #06070a; }
html[data-theme='light'] .rf-seg { background: #f5f5f6; }
html[data-theme='light'] .rf-seg__btn { color: #6b7280; }
html[data-theme='light'] .rf-seg__btn--on { background: #ffffff; color: #111111; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
html[data-theme='light'] .rf-form__label { color: #111111; }
html[data-theme='light'] .rf-form__input, html[data-theme='light'] .rf-form__area { background: #f5f5f5; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-form__input::placeholder, html[data-theme='light'] .rf-form__area::placeholder { color: #9ca3af; }
html[data-theme='light'] .rf-form__input:focus, html[data-theme='light'] .rf-form__area:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-type { background: #ffffff; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-type.on { border-color: #111111; background: #f9f9f9; }
html[data-theme='light'] .rf-submit { background: #111111; color: #ffffff; }
html[data-theme='light'] .rf-watch { background: linear-gradient(180deg, #fdf8f8 0%, #f9f1f1 100%); color: #111111; }
html[data-theme='light'] .rf-quota { color: #6b7280; }
html[data-theme='light'] .rf-searchbar__input { background: #f0f0f1; border-color: #e8e8ea; color: #111111; }
html[data-theme='light'] .rf-searchbar__input::placeholder { color: #9ca3af; }
html[data-theme='light'] .rf-searchbar__input:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-statchip { color: #6b7280; }
html[data-theme='light'] .rf-statchip--on { background: #ffffff; border-color: #e5e5e5; color: #111111; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
html[data-theme='light'] .rf-topic { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rf-topic__poster, html[data-theme='light'] .rf-topic__ph { background: #f0f0f1; }
html[data-theme='light'] .rf-topic__ph { color: #9ca3af; }
html[data-theme='light'] .rf-topic__title { color: #111111; }
html[data-theme='light'] .rf-badge--green { background: #e6f4ea; color: #16a34a; }
html[data-theme='light'] .rf-badge--gray { background: #f0f0f1; color: #6b7280; }
html[data-theme='light'] .rf-topic__counts { color: #6b7280; }
html[data-theme='light'] .rf-topic__view { color: #111111; }
html[data-theme='light'] .rf-topic__chev { color: #9ca3af; }
html[data-theme='light'] .rf-topic__detail { border-top-color: #e5e5e5; }
html[data-theme='light'] .rf-topic__reqtitle { color: #111111; }
html[data-theme='light'] .rf-topic__reqmeta, html[data-theme='light'] .rf-topic__missing, html[data-theme='light'] .rf-topic__noreq { color: #6b7280; }
html[data-theme='light'] .rf-filter__select { background: #f6f8fa; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-row { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rf-row__head { color: #111111; }
html[data-theme='light'] .rf-row__meta { color: #6b7280; }
html[data-theme='light'] .rf-row__chev { color: #9ca3af; }
html[data-theme='light'] .rf-row__detail { border-top-color: #e5e5e5; }
html[data-theme='light'] .rf-detail__line { color: #55575c; }
html[data-theme='light'] .rf-link { color: #2563eb; }
html[data-theme='light'] .rf-vote { background: #111111; color: #ffffff; }
html[data-theme='light'] .rf-vote--done { background: #16a34a; color: #ffffff; }
html[data-theme='light'] .rf-empty { border-color: #d4d4d8; color: #8a8f96; }
html[data-theme='light'] .rf-err { color: #dc2626; }
html[data-theme='light'] .rf-retry { color: #111111; }
html[data-theme='light'] .rf-skeleton { background: #f0f0f1; }
</style>
