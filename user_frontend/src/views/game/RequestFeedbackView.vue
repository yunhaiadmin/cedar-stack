<template>
  <UhdAccountLayout>
    <!-- 求片反馈（2026-11-13 合并页）：许愿池＋求救信号合并为一页。
         三页签（发起请求/我的请求/请求广场）×三类型（求片/追新/资源反馈），
         视觉照 UHDNow 原站求片反馈页 1:1（实测清单 §10）；页面跟随用户外观。 -->
    <div class="rf-page">
      <div class="rf-inner">
      <header class="rf-head">
        <h1>求片反馈</h1>
        <p>想看的片、想追的新集、资源有问题，都在这里提交和查看进度</p>
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
        <div class="rf-seg" role="group" aria-label="请求类型">
          <button
            v-for="t in reqTypes"
            :key="t.key"
            type="button"
            class="rf-seg__btn"
            :class="{ 'rf-seg__btn--on': reqType === t.key }"
            :aria-pressed="reqType === t.key"
            @click="setType(t.key)"
          >{{ t.label }}</button>
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
          <NoticeCards kind="seek" :monthly-remaining="monthlyRemaining" />
        </div>

        <div v-else-if="reqType === 'chase'" class="rf-create__body">
          <div class="rf-form">
            <label class="rf-form__label" for="rf-chase-title">剧名</label>
            <input
              id="rf-chase-title"
              v-model="chaseTitle"
              class="rf-form__input"
              type="text"
              placeholder="比如：《一念永恒》"
              maxlength="100"
            />
            <label class="rf-form__label" for="rf-chase-msg">想追的季 / 集</label>
            <textarea
              id="rf-chase-msg"
              v-model="chaseMessage"
              class="rf-form__area"
              placeholder="比如：第 3 季第 5 集，什么时候更新？"
              maxlength="1000"
            ></textarea>
            <button class="rf-submit" :disabled="chaseSending" @click="submitChase">{{ chaseSending ? '提交中…' : '提交追新' }}</button>
          </div>
          <NoticeCards kind="chase" />
        </div>

        <div v-else class="rf-create__body">
          <div class="rf-form">
            <div class="rf-form__label">问题类型</div>
            <div class="rf-types" role="radiogroup" aria-label="问题类型">
              <button
                v-for="c in feedbackCategories"
                :key="c.value"
                type="button"
                class="rf-type"
                :class="{ on: fbCategory === c.value }"
                role="radio"
                :aria-checked="fbCategory === c.value"
                @click="fbCategory = c.value"
              >{{ c.label }}</button>
            </div>
            <label class="rf-form__label" for="rf-fb-title">一句话标题</label>
            <input
              id="rf-fb-title"
              v-model="fbTitle"
              class="rf-form__input"
              type="text"
              placeholder="比如：《一念永恒》第 3 集播放卡住"
              maxlength="100"
            />
            <label class="rf-form__label" for="rf-fb-msg">问题描述</label>
            <textarea
              id="rf-fb-msg"
              v-model="fbMessage"
              class="rf-form__area"
              placeholder="比如：《一念永恒》看到第 3 集，转圈半天出不来…"
              maxlength="1000"
            ></textarea>
            <button class="rf-submit" :disabled="fbSending" @click="submitFeedback">{{ fbSending ? '提交中…' : '提交反馈' }}</button>
          </div>
          <NoticeCards kind="feedback" />
        </div>
      </section>

      <!-- 我的请求 -->
      <section v-else-if="tab === 'mine'" class="rf-list">
        <div class="rf-filter">
          <div class="rf-filter__caps" role="group" aria-label="按状态筛选">
            <button
              v-for="s in stateOptions"
              :key="s.key"
              type="button"
              class="rf-filter__cap"
              :class="{ 'rf-filter__cap--on': stateFilter === s.key }"
              :aria-pressed="stateFilter === s.key"
              @click="stateFilter = s.key"
            >{{ s.label }}</button>
          </div>
          <select v-model="typeFilter" class="rf-filter__select" aria-label="按类型筛选">
            <option value="all">全部类型</option>
            <option value="seek">求片</option>
            <option value="chase">追新</option>
            <option value="feedback">资源反馈</option>
          </select>
        </div>

        <div v-if="mineLoading" class="rf-loading">
          <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 76px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="mineError" class="rf-err">
          请求记录读取失败：{{ mineError }}<br />
          <button class="rf-retry" @click="() => loadMine()">重试</button>
        </div>
        <div v-else-if="filteredMine.length === 0" class="rf-empty">还没有提交过请求</div>
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

      <!-- 请求广场 -->
      <section v-else class="rf-list">
        <p class="rf-plaza__note">附议越多，处理优先级越高</p>
        <div class="rf-filter">
          <div class="rf-filter__caps" role="group" aria-label="按状态筛选">
            <button
              v-for="s in stateOptions"
              :key="s.key"
              type="button"
              class="rf-filter__cap"
              :class="{ 'rf-filter__cap--on': plazaStateFilter === s.key }"
              :aria-pressed="plazaStateFilter === s.key"
              @click="plazaStateFilter = s.key"
            >{{ s.label }}</button>
          </div>
          <select v-model="plazaTypeFilter" class="rf-filter__select" aria-label="按类型筛选">
            <option value="all">全部类型</option>
            <option value="seek">求片</option>
            <option value="chase">追新</option>
            <option value="feedback">资源反馈</option>
          </select>
        </div>

        <div v-if="hotLoading" class="rf-loading">
          <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 76px; margin-bottom: 10px"></div>
        </div>
        <div v-else-if="hotError" class="rf-err">
          广场加载失败：{{ hotError }}<br />
          <button class="rf-retry" @click="loadHot">重试</button>
        </div>
        <div v-else-if="plazaTypeFilter === 'chase'" class="rf-empty">追新请求仅自己可见，不在这里公开显示</div>
        <div v-else-if="plazaTypeFilter === 'feedback'" class="rf-empty">资源反馈仅自己可见，不在这里公开显示</div>
        <div v-else-if="filteredPlaza.length === 0" class="rf-empty">暂时没有公开的求片</div>
        <div v-else class="rf-rows">
          <div v-for="w in filteredPlaza" :key="w.id" class="rf-row">
            <div class="rf-row__head rf-row__head--static">
              <span class="rf-row__main">
                <span class="rf-row__title">
                  <span class="rf-dot rf-dot--pending" aria-hidden="true"></span>
                  {{ w.movie_name }}
                </span>
                <span class="rf-row__meta">
                  {{ w.year ? w.year + ' · ' : '' }}{{ w.type === 'series' ? '剧集' : '电影' }}
                  · {{ w.vote_count > 0 ? w.vote_count + ' 人也想看' : '还没有人附议' }}
                  · {{ formatDate(w.created_at) }}
                </span>
              </span>
            </div>
            <button
              class="rf-vote"
              :class="{ 'rf-vote--done': w.voted }"
              :disabled="w.voted || voting !== null"
              @click="upvote(w)"
            >{{ w.voted ? '✓ 已附议' : voting === w.id ? '附议中' : '+1 附议' }}</button>
          </div>
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
  type HotMediaSeekRequest, type MediaSeekQuota, type MediaSeekRequest, type Ticket,
} from '@/api'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

type TabKey = 'create' | 'mine' | 'plaza'
type ReqType = 'seek' | 'chase' | 'feedback'
type UnifiedState = 'pending' | 'doing' | 'done' | 'rejected' | 'withdrawn'

interface UnifiedRow {
  source: 'seek' | 'ticket'
  id: number
  type: ReqType
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
const reqTypes: { key: ReqType; label: string }[] = [
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
const typeFilter = ref<'all' | ReqType>('all')
const plazaStateFilter = ref<'all' | UnifiedState>('all')
const plazaTypeFilter = ref<'all' | ReqType>('all')

const chaseTitle = ref('')
const chaseMessage = ref('')
const chaseSending = ref(false)
const fbCategory = ref('other')
const fbTitle = ref('')
const fbMessage = ref('')
const fbSending = ref(false)

function stateLabel(s: UnifiedState): string {
  return { pending: '待处理', doing: '处理中', done: '已完成', rejected: '已拒绝', withdrawn: '已撤回' }[s]
}
function typeLabel(t: ReqType): string {
  return t === 'seek' ? '求片' : t === 'chase' ? '追新' : '资源反馈'
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
    const isChase = t.category === 'chase'
    rows.push({
      source: 'ticket', id: t.id, type: isChase ? 'chase' : 'feedback', title: t.title,
      created_at: t.created_at, updated_at: t.updated_at, state: ticketState(t.status),
      categoryLabel: isChase ? undefined : feedbackCategoryLabel(t.category),
      rawTicket: t,
    })
  }
  rows.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  return rows
})
const filteredMine = computed(() => unifiedMine.value.filter((r) => {
  if (stateFilter.value !== 'all' && r.state !== stateFilter.value) return false
  if (typeFilter.value !== 'all' && r.type !== typeFilter.value) return false
  return true
}))
/* 广场只有求片有数据源（mediaSeekApi.hot）；追新/反馈因工单私密显示空态 */
const filteredPlaza = computed(() => hotRequests.value.filter((w) => {
  if (plazaStateFilter.value !== 'all' && plazaStateFilter.value !== 'pending') return false
  if (plazaTypeFilter.value !== 'all' && plazaTypeFilter.value !== 'seek') return false
  return true
}))

function syncFromQuery(): void {
  const t = route.query.tab
  if (t === 'mine' || t === 'plaza' || t === 'create') tab.value = t
  const ty = route.query.type
  if (ty === 'seek' || ty === 'chase' || ty === 'feedback') reqType.value = ty
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
  setTab('mine')
  if (payload.created && payload.requestId) highlight(`seek:${payload.requestId}`)
}
async function submitChase(): Promise<void> {
  const title = chaseTitle.value.trim()
  const message = chaseMessage.value.trim()
  if (!title || !message) { toast.error('先写清楚剧名和想追的季 / 集，再提交'); return }
  if (chaseSending.value) return
  chaseSending.value = true
  try {
    const r = await ticketApi.create({ category: 'chase', title, message })
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
async function submitFeedback(): Promise<void> {
  const title = fbTitle.value.trim()
  const message = fbMessage.value.trim()
  if (!title || !message) { toast.error('先写清楚标题和问题描述，再提交'); return }
  if (fbSending.value) return
  fbSending.value = true
  try {
    const r = await ticketApi.create({ category: fbCategory.value, title, message })
    toast.success(r.message || '反馈已提交，我们会尽快处理')
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
.rf-head { margin-bottom: 18px; }
.rf-head h1 { margin: 0 0 6px; font-size: 31px; font-weight: 800; color: #fbfcfd; line-height: 1.2; }
.rf-head p { margin: 0; font-size: 14px; color: #9ca3af; }
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
  font-size: 12px; color: #9ca3af; border-radius: 16px; padding: 0 14px; font-family: inherit; white-space: nowrap;
}
.rf-seg__btn--on { background: #2a2b2d; color: #fbfcfd; font-weight: 700; }
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
.rf-plaza__note { margin: 0; font-size: 13px; color: #9ca3af; }
.rf-filter { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.rf-filter__caps {
  display: flex; flex-wrap: wrap; gap: 2px; max-width: 481px; padding: 3px;
  background: #1b1c1e; border-radius: 19px;
}
.rf-filter__cap {
  border: none; background: transparent; cursor: pointer; font-size: 12px;
  color: #9ca3af; border-radius: 16px; padding: 7px 12px; font-family: inherit; white-space: nowrap;
}
.rf-filter__cap--on { background: #2a2b2d; color: #fbfcfd; font-weight: 700; }
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
.rf-vote {
  width: 100%; margin-top: 10px; min-height: 44px; border-radius: 10px; border: none; cursor: pointer;
  font-size: 13px; font-weight: 700; background: #fbfcfd; color: #111111;
}
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
html[data-theme='light'] .rf-plaza__note { color: #6b7280; }
html[data-theme='light'] .rf-filter__caps { background: #f5f5f7; }
html[data-theme='light'] .rf-filter__cap { color: #6b7280; }
html[data-theme='light'] .rf-filter__cap--on { background: #ffffff; color: #111111; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
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
