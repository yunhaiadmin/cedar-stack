<template>
  <UhdAccountLayout>
    <!-- 作品请求详情（2026-11-15 按作品组织重做，照友站 2.4）：作品头卡
         （标题上方状态徽标＋TMDB 外链）＋该作品全部请求（后端
         GET /media-seek/work 按作品汇集：求片/追新计数、我的未完结指针、
         补充说明与管理员回复都在一条口里）＋底部「没有涵盖你的问题」卡
         （求片/追新/资源反馈三胶囊，同类型未完成时拦截提示）。
         本人未完结行可补充说明、可撤回；他人处理中行可 +1 附议。
         页面跟随用户外观。 -->
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

        <div v-if="loading" class="rmd-loading">
          <div class="rmd-skeleton" style="height: 170px; margin-bottom: 10px"></div>
          <div class="rmd-skeleton" style="height: 92px"></div>
        </div>
        <div v-else-if="loadError" class="rmd-err">
          请求记录读取失败：{{ loadError }}<br />
          <button class="rmd-retry" @click="loadAll">重试</button>
        </div>
        <template v-else-if="detail">
          <section class="rmd-media" aria-label="影片信息">
            <img
              v-if="detail.work.poster_url"
              class="rmd-media__poster"
              :src="imageAuthUrl(detail.work.poster_url, 300)"
              :alt="detail.work.name"
              loading="lazy"
            />
            <span v-else class="rmd-media__ph" aria-hidden="true">{{ (detail.work.name || '片').slice(0, 1) }}</span>
            <div class="rmd-media__info">
              <span v-if="detail.work.in_library" class="rmd-pill rmd-pill--green">✓ 库内已有</span>
              <span v-else class="rmd-pill rmd-pill--outline">库外求片</span>
              <h1 class="rmd-media__name">{{ detail.work.name }}</h1>
              <p class="rmd-media__meta">{{ kindLabel }}<template v-if="detail.work.year"> / {{ detail.work.year }}</template></p>
              <div class="rmd-media__actions">
                <a
                  v-if="tmdbUrl"
                  class="rmd-media__tmdb"
                  :href="tmdbUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                >TMDB ↗</a>
                <button
                  v-if="detail.work.in_library && detail.work.library_item_id"
                  type="button"
                  class="rmd-media__watch"
                  @click="goWatch"
                >站内观看</button>
              </div>
            </div>
          </section>

          <div class="rmd-reqhead">
            <h2 class="rmd-reqhead__title">请求</h2>
            <span class="rmd-reqhead__count">{{ detail.counts.total }} 条</span>
          </div>
          <p class="rmd-hint">同一部片已经有人在求时，不用重复提交，给已有请求 +1 附议就行；处理进度都会在这里更新。</p>

          <div v-if="detail.requests.length === 0" class="rmd-empty">
            <p>这部片还没有人提交过请求</p>
          </div>
          <div v-else class="rmd-rows">
            <article v-for="row in detail.requests" :key="row.id" class="rmd-card">
              <div class="rmd-card__head">
                <span class="rmd-pill rmd-pill--type">{{ row.kind === 'chase' ? '追新' : '求片' }}</span>
                <span class="rmd-pill" :class="'rmd-pill--' + row.state">{{ stateLabel(row.state) }}</span>
                <span v-if="row.mine" class="rmd-pill rmd-pill--mine">我提交的</span>
                <span class="rmd-card__time">{{ formatTime(row.created_at) }}</span>
              </div>
              <p class="rmd-card__body">{{ rowBody(row) }}</p>
              <p v-if="row.missing_text" class="rmd-card__sub">{{ row.missing_text }}</p>
              <div class="rmd-card__records">
                <p v-if="row.admin_note" class="rmd-rec rmd-rec--staff">
                  <span class="rmd-rec__tag">处理回复</span>{{ row.admin_note }}
                </p>
                <p v-for="s in row.supplements" :key="s.id" class="rmd-rec">
                  <span class="rmd-rec__tag">{{ s.mine ? '我的补充' : '补充说明' }}</span>{{ s.body }}
                  <span class="rmd-rec__time">{{ formatTime(s.created_at) }}</span>
                </p>
                <p v-if="!row.admin_note && row.supplements.length === 0" class="rmd-rec rmd-rec--empty">
                  暂无补充说明或处理记录
                </p>
              </div>
              <div v-if="row.mine && isActive(row)" class="rmd-card__mine">
                <button type="button" class="rmd-actbtn" @click="toggleSupplement(row.id)">
                  {{ supplementOpenId === row.id ? '收起' : '补充说明' }}
                </button>
                <button type="button" class="rmd-actbtn rmd-actbtn--danger" :disabled="withdrawing === row.id" @click="withdrawRow(row)">
                  {{ withdrawing === row.id ? '撤回中…' : '撤回请求' }}
                </button>
              </div>
              <div v-if="row.mine && supplementOpenId === row.id" class="rmd-supform">
                <textarea
                  v-model="supplementText"
                  class="rmd-supform__area"
                  maxlength="2000"
                  rows="3"
                  placeholder="把新的情况补在这里，处理的人能直接看到"
                ></textarea>
                <button type="button" class="rmd-supform__btn" :disabled="supplementing" @click="submitSupplement(row)">
                  {{ supplementing ? '提交中…' : '提交补充' }}
                </button>
              </div>
              <div v-if="!row.mine && isActive(row)" class="rmd-card__foot">
                <span class="rmd-card__votes">{{ row.vote_count > 0 ? row.vote_count + ' 人附议' : '还没有人附议' }}</span>
                <button
                  class="rmd-vote"
                  :class="{ 'rmd-vote--done': row.voted }"
                  :disabled="row.voted || voting !== null"
                  @click="upvote(row)"
                >{{ row.voted ? '✓ 已附议' : voting === row.id ? '附议中' : '+1 附议' }}</button>
              </div>
            </article>
          </div>

          <section class="rmd-more" aria-label="继续提交">
            <h3 class="rmd-more__title">现有请求没有涵盖你的问题？</h3>
            <div class="rmd-more__row">
              <button type="button" class="rmd-more__pill" @click="openKind('seek')">求片</button>
              <button type="button" class="rmd-more__pill" @click="openKind('chase')">追新</button>
              <button type="button" class="rmd-more__pill" @click="goFeedback">资源反馈</button>
            </div>
            <p v-if="interceptText" class="rmd-more__note">{{ interceptText }}</p>
          </section>
        </template>

        <RequestSubmitModal
          :open="modalOpen"
          :mode="modalMode"
          :work="modalWork"
          @close="modalOpen = false"
          @submitted="onModalSubmitted"
        />
      </div>
    </div>
  </UhdAccountLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft } from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import RequestSubmitModal, { type SubmitWork } from '@/components/requests/RequestSubmitModal.vue'
import {
  mediaSeekApi,
  type WorkDetail, type WorkRequestRow,
} from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

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

const detail = ref<WorkDetail | null>(null)
const loading = ref(true)
const loadError = ref('')
const voting = ref<number | null>(null)
const withdrawing = ref<number | null>(null)
const supplementOpenId = ref<number | null>(null)
const supplementText = ref('')
const supplementing = ref(false)
const modalOpen = ref(false)
const modalMode = ref<'seek' | 'chase'>('seek')
const modalWork = ref<SubmitWork | null>(null)
/* 底部卡点过的类型（拦截提示按它显示） */
const pickedKind = ref<'seek' | 'chase' | null>(null)

function stateLabel(s: WorkRequestRow['state']): string {
  return { pending: '待处理', doing: '处理中', done: '已完成', rejected: '已拒绝' }[s]
}
function isActive(row: WorkRequestRow): boolean {
  return row.state === 'pending' || row.state === 'doing'
}
/* 请求卡正文：提交时的具体说明优先；老行没说明时用片名＋季信息兜底 */
function rowBody(row: WorkRequestRow): string {
  if (row.note) return row.note
  return row.season_label ? `${row.movie_name} · ${row.season_label}` : row.movie_name
}
/* 时间照友站详情态：M/D HH:mm */
function formatTime(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (Number.isNaN(d.getTime())) return ''
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`
}

async function loadAll(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    detail.value = await mediaSeekApi.work({
      ...(tmdbId.value ? { tmdb_id: tmdbId.value } : {}),
      ...(mediaName.value ? { name: mediaName.value } : {}),
      ...(mediaYear.value ? { year: mediaYear.value } : {}),
      ...(mediaType.value ? { type: mediaType.value } : {}),
    })
  } catch (e) {
    loadError.value = humanizeError(e)
  } finally {
    loading.value = false
  }
}

async function upvote(row: WorkRequestRow): Promise<void> {
  if (row.voted || voting.value) return
  voting.value = row.id
  try {
    const r = await mediaSeekApi.vote(row.id)
    row.voted = r.voted
    row.vote_count = r.vote_count
  } catch {
    toast.error('附议失败，再试一次')
  } finally {
    voting.value = null
  }
}

async function withdrawRow(row: WorkRequestRow): Promise<void> {
  if (withdrawing.value) return
  withdrawing.value = row.id
  try {
    await mediaSeekApi.withdraw(row.id)
    toast.success('已撤回')
    await loadAll()
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    withdrawing.value = null
  }
}

function toggleSupplement(id: number): void {
  supplementOpenId.value = supplementOpenId.value === id ? null : id
  supplementText.value = ''
}
async function submitSupplement(row: WorkRequestRow): Promise<void> {
  const text = supplementText.value.trim()
  if (!text) { toast.error('先写下要补充的内容'); return }
  if (supplementing.value) return
  supplementing.value = true
  try {
    await mediaSeekApi.supplement(row.id, text)
    toast.success('补充说明已提交')
    supplementOpenId.value = null
    supplementText.value = ''
    await loadAll()
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    supplementing.value = false
  }
}

/* 底部三胶囊：同类型已有未完成请求先拦截（后端 my_open 指针），
   不让用户重复开单；追新只许库内作品（与提交口同一口径） */
const interceptText = computed(() => {
  if (!detail.value || !pickedKind.value) return ''
  const open = detail.value.my_open[pickedKind.value]
  if (!open) return ''
  return pickedKind.value === 'seek'
    ? '你已经有一条未完成的求片了，处理完再来提交新的。'
    : '你已经有一条未完成的追新了，处理完再来提交新的。'
})
function openKind(kind: 'seek' | 'chase'): void {
  if (!detail.value) return
  pickedKind.value = kind
  if (detail.value.my_open[kind]) return
  if (kind === 'chase' && !detail.value.work.in_library) {
    toast.error('这部作品还没在站里，先去求片吧')
    return
  }
  const w = detail.value.work
  modalMode.value = kind
  modalWork.value = {
    tmdb_id: w.tmdb_id || tmdbId.value || null,
    name: w.name,
    year: w.year || undefined,
    kind: (w.type === 'movie' ? 'movie' : 'series'),
    poster_url: w.poster_url,
  }
  modalOpen.value = true
}
function goFeedback(): void {
  void router.push({
    path: '/game/requests',
    query: { tab: 'create', type: 'feedback', q: detail.value?.work.name || mediaName.value },
  })
}
function onModalSubmitted(): void {
  modalOpen.value = false
  pickedKind.value = null
  void loadAll()
}

function goBack(): void {
  if (window.history.length > 1) router.back()
  else void router.push('/game/requests')
}
function goWatch(): void {
  const id = detail.value?.work.library_item_id
  if (id) void router.push(`/media/${id}`)
}

onMounted(() => { void loadAll() })
</script>

<style scoped>
/* 作品请求详情：与求片反馈页同壳（负边距铺满＋深/浅两套）。 */
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
  position: relative;
  flex: none; display: inline-flex; align-items: center; gap: 8px;
  height: 40px; padding: 0 16px; border-radius: 999px;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d));
  color: #ffffff;
  box-shadow: var(--uhd-shadow-sheet);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  font-size: 14px; font-weight: 700; text-decoration: none; white-space: nowrap;
}
/* 彩虹环切（texture-tokens-3）：彩虹只走 1.5px 细环（::before mask 环切），
   钮面只留玻璃层；浅色覆盖只换面背景，环由本处继承。 */
.rmd-watch::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--uhd-rainbow-border-w);
  background: var(--uhd-rainbow);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
}
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
  position: relative;
  height: 36px; padding: 0 16px; border-radius: 999px; cursor: pointer; font-family: inherit;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d));
  color: #ffffff; font-size: 13px; font-weight: 800;
  box-shadow: var(--uhd-shadow-sheet);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 彩虹环切（texture-tokens-3）：彩虹只走 1.5px 细环（::before mask 环切），
   钮面只留玻璃层；浅色覆盖只换面背景，环由本处继承。 */
.rmd-media__watch::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--uhd-rainbow-border-w);
  background: var(--uhd-rainbow);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
}
.rmd-pill {
  flex: none; display: inline-flex; align-items: center; height: 22px; padding: 0 8px;
  border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap;
  border: 1px solid #333336; color: #9ca3af; background: transparent;
}
.rmd-pill--green { background: rgba(22, 163, 74, 0.16); color: #4ade80; border-color: rgba(22, 163, 74, 0.5); }
.rmd-pill--outline { background: transparent; color: #d4d4d8; border-color: #4b5563; }
.rmd-pill--type { background: #26262a; color: #d4d4d8; border-color: #26262a; }
.rmd-pill--mine { background: transparent; color: #6ea8fe; border-color: #6ea8fe; }
.rmd-pill--pending { color: #9ca3af; border-color: #3a3b3f; }
.rmd-pill--doing { color: #f59e0b; border-color: #f59e0b; }
.rmd-pill--done { color: #4ade80; border-color: #16a34a; }
.rmd-pill--rejected { color: #f87171; border-color: #ef4444; }
.rmd-reqhead { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.rmd-reqhead__title { margin: 0; font-size: 17px; font-weight: 800; color: #fbfcfd; }
.rmd-reqhead__count { font-size: 13px; color: #9ca3af; }
.rmd-hint { margin: 8px 0 14px; font-size: 12px; line-height: 1.6; color: #9ca3af; }
.rmd-rows { display: flex; flex-direction: column; gap: 10px; }
.rmd-card { background: #17181a; border: 1px solid #26262a; border-radius: 16px; padding: 14px; }
.rmd-card__head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rmd-card__time { margin-left: auto; font-size: 12px; color: #9ca3af; white-space: nowrap; }
.rmd-card__body { margin: 10px 0 0; font-size: 14px; font-weight: 700; color: #fbfcfd; word-break: break-word; line-height: 1.6; }
.rmd-card__sub { margin: 4px 0 0; font-size: 12px; color: #9ca3af; line-height: 1.6; }
/* 记录区（照友站 2.4）：卡内分隔线下放处理回复与补充说明 */
.rmd-card__records { margin-top: 12px; border-top: 1px solid #26262a; padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.rmd-rec { margin: 0; font-size: 13px; color: #d4d4d8; line-height: 1.6; word-break: break-word; }
.rmd-rec__tag {
  display: inline-block; margin-right: 8px; padding: 1px 7px; border-radius: 999px;
  background: #26262a; color: #9ca3af; font-size: 11px; font-weight: 700; vertical-align: 1px;
}
.rmd-rec--staff .rmd-rec__tag { background: rgba(22, 163, 74, 0.16); color: #4ade80; }
.rmd-rec__time { margin-left: 8px; font-size: 11px; color: #9ca3af; }
.rmd-rec--empty { color: #9ca3af; font-size: 12px; }
.rmd-card__mine { margin-top: 12px; display: flex; gap: 8px; }
.rmd-actbtn {
  min-height: 34px; padding: 0 14px; border-radius: 999px; cursor: pointer;
  background: transparent; border: 1.5px solid #4b5563; color: #fbfcfd;
  font-size: 13px; font-weight: 700; font-family: inherit;
}
.rmd-actbtn--danger { border-color: rgba(239, 68, 68, 0.55); color: #f87171; }
.rmd-actbtn:disabled { opacity: 0.5; cursor: default; }
.rmd-supform { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.rmd-supform__area {
  width: 100%; padding: 10px 12px; resize: none;
  font-size: 13px; line-height: 1.6; color: #fbfcfd; font-family: inherit;
  background: #101012; border: 1px solid #26262a; border-radius: 12px; outline: none;
}
.rmd-supform__area:focus { border-color: #6b7280; }
.rmd-supform__btn {
  align-self: flex-end; min-height: 36px; padding: 0 16px; border-radius: 999px; cursor: pointer;
  background: #fbfcfd; color: #111111; border: none;
  font-size: 13px; font-weight: 700; font-family: inherit;
}
.rmd-supform__btn:disabled { opacity: 0.5; cursor: default; }
.rmd-card__foot { margin-top: 10px; display: flex; align-items: center; gap: 10px; }
.rmd-card__votes { flex: 1; font-size: 12px; color: #9ca3af; }
.rmd-vote {
  flex: none; min-height: 36px; padding: 0 14px; border-radius: 999px; cursor: pointer;
  font-size: 13px; font-weight: 700; font-family: inherit;
  background: #fbfcfd; color: #111111; border: none;
}
.rmd-vote--done { background: #16a34a; color: #ffffff; }
.rmd-vote:disabled { cursor: default; }
/* 底部卡（照友站 2.4）：一问＋三胶囊＋拦截提示 */
.rmd-more {
  margin-top: 18px; background: #17181a; border: 1px solid #26262a; border-radius: 16px; padding: 16px;
}
.rmd-more__title { margin: 0 0 12px; font-size: 15px; font-weight: 800; color: #fbfcfd; }
.rmd-more__row { display: flex; gap: 10px; flex-wrap: wrap; }
.rmd-more__pill {
  min-height: 38px; padding: 0 20px; border-radius: 999px; cursor: pointer;
  background: #1e1f21; border: 1.5px solid #333336; color: #fbfcfd;
  font-size: 14px; font-weight: 700; font-family: inherit;
}
.rmd-more__note { margin: 12px 0 0; font-size: 12px; color: #f59e0b; line-height: 1.6; }
.rmd-empty {
  display: flex; flex-direction: column; align-items: center; gap: 12px; text-align: center;
  min-height: 180px; justify-content: center;
  border: 1px dashed #3a3b3f; border-radius: 16px; padding: 20px;
  font-size: 14px; color: #8a8f96;
}
.rmd-empty p { margin: 0; }
.rmd-err { font-size: 13px; color: #fca5a5; line-height: 1.6; }
.rmd-retry { margin-top: 6px; padding: 3px 12px; font-size: 12px; color: #fbfcfd; background: transparent; border: 1px solid currentColor; border-radius: 999px; cursor: pointer; opacity: 0.8; }
.rmd-skeleton { background: var(--uhd-skel-bg); border-radius: 10px; }
@media (max-width: 768px) {
  .rmd-page { margin: -20px -16px -120px; padding: 20px 16px 120px; }
}

html[data-theme='light'] .rmd-page { background: #f8f8f8; }
html[data-theme='light'] .rmd-topbar__title { color: #111111; }
html[data-theme='light'] .rmd-back { color: #111111; }
html[data-theme='light'] .rmd-back:active { background: rgba(0, 0, 0, 0.05); }
html[data-theme='light'] .rmd-watch {
  background: var(--uhd-rainbow-soft);
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
  background: var(--uhd-rainbow-soft);
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rmd-pill { border-color: #e5e5e5; color: #6b7280; }
html[data-theme='light'] .rmd-pill--green { background: #e6f4ea; color: #16a34a; border-color: #16a34a; }
html[data-theme='light'] .rmd-pill--outline { color: #55575c; border-color: #c9ccd1; }
html[data-theme='light'] .rmd-pill--type { background: #f0f0f1; color: #55575c; border-color: #f0f0f1; }
html[data-theme='light'] .rmd-pill--mine { color: #2563eb; border-color: #2563eb; }
html[data-theme='light'] .rmd-pill--doing { color: #d97706; border-color: #f59e0b; }
html[data-theme='light'] .rmd-pill--done { color: #16a34a; border-color: #16a34a; }
html[data-theme='light'] .rmd-pill--rejected { color: #dc2626; border-color: #ef4444; }
html[data-theme='light'] .rmd-reqhead__title { color: #111111; }
html[data-theme='light'] .rmd-reqhead__count, html[data-theme='light'] .rmd-hint { color: #6b7280; }
html[data-theme='light'] .rmd-card { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rmd-card__time { color: #6b7280; }
html[data-theme='light'] .rmd-card__body { color: #111111; }
html[data-theme='light'] .rmd-card__sub, html[data-theme='light'] .rmd-card__votes { color: #6b7280; }
html[data-theme='light'] .rmd-card__records { border-top-color: #e5e5e5; }
html[data-theme='light'] .rmd-rec { color: #55575c; }
html[data-theme='light'] .rmd-rec__tag { background: #f0f0f1; color: #6b7280; }
html[data-theme='light'] .rmd-rec--staff .rmd-rec__tag { background: #e6f4ea; color: #16a34a; }
html[data-theme='light'] .rmd-rec__time, html[data-theme='light'] .rmd-rec--empty { color: #6b7280; }
html[data-theme='light'] .rmd-actbtn { border-color: #c9ccd1; color: #111111; }
html[data-theme='light'] .rmd-actbtn--danger { border-color: rgba(220, 38, 38, 0.5); color: #dc2626; }
html[data-theme='light'] .rmd-supform__area { background: #f8f8f8; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rmd-supform__area:focus { border-color: #9ca3af; }
html[data-theme='light'] .rmd-supform__btn { background: #111111; color: #ffffff; }
html[data-theme='light'] .rmd-vote { background: #111111; color: #ffffff; }
html[data-theme='light'] .rmd-vote--done { background: #16a34a; color: #ffffff; }
html[data-theme='light'] .rmd-more { background: #ffffff; border-color: #e5e5e5; }
html[data-theme='light'] .rmd-more__title { color: #111111; }
html[data-theme='light'] .rmd-more__pill { background: #f5f5f5; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rmd-more__note { color: #d97706; }
html[data-theme='light'] .rmd-empty { border-color: #d4d4d8; color: #8a8f96; }
html[data-theme='light'] .rmd-err { color: #dc2626; }
html[data-theme='light'] .rmd-retry { color: #111111; }
</style>
