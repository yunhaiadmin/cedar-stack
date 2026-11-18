<template>
  <!-- 提交弹层（照友站 2.3/2.5 形态）：作品卡＋具体说明（预填「求片：片名」/
       「追新：片名」）＋2000 字计数＋确认/取消；提交中按钮转 spinner，
       成功后由父级跳作品请求详情页。剧集求片保留我方按整季申请（季选
       在弹层内、作品卡下）。 -->
  <Teleport to="body">
    <div v-if="open && work" class="rsm-mask" @click.self="onClose">
      <div class="rsm-card" role="dialog" aria-modal="true" :aria-label="title">
        <h3 class="rsm-title">{{ title }}</h3>
        <p class="rsm-sub">提交后不能修改，请一次写清需要处理的内容。</p>
        <button type="button" class="rsm-x" aria-label="关闭" @click="onClose">✕</button>

        <div class="rsm-work">
          <img v-if="work.poster_url" :src="imageAuthUrl(work.poster_url, 160)" :alt="work.name" class="rsm-work__poster" />
          <span v-else class="rsm-work__ph" aria-hidden="true">{{ work.name.slice(0, 1) }}</span>
          <div class="rsm-work__info">
            <b>{{ work.name }}</b>
            <span v-if="metaLine" class="rsm-work__meta">{{ metaLine }}</span>
          </div>
        </div>

        <div v-if="showSeasons" class="rsm-seasons">
          <div v-if="seasons.length > 0" class="rsm-seasons__label">选季（可多选）</div>
          <div v-if="seasons.length > 0" class="rsm-seasons__row">
            <button type="button" class="rsm-chipbtn" :class="{ on: seasonAll }" @click="toggleSeasonAll">全季</button>
            <button
              v-for="s in seasons"
              :key="s.season_number"
              type="button"
              class="rsm-chipbtn"
              :class="{ on: chosenSeasons.includes(s.season_number) }"
              @click="toggleSeason(s.season_number)"
            >第{{ s.season_number }}季（共{{ s.episode_count }}集）</button>
          </div>
          <div v-if="loadingSeasons" class="rsm-seasons__hint">正在加载季列表…</div>
          <div v-else-if="seasonsError !== ''" class="rsm-seasons__err">
            <span>季列表没取到：{{ seasonsError }}</span>
            <button type="button" class="rsm-chipbtn" @click="retrySeasons">重试</button>
          </div>
          <div v-else-if="seasonsLoaded && seasons.length === 0" class="rsm-seasons__hint">这部剧没有季数据，将按全季提交</div>
        </div>

        <label class="rsm-label" for="rsm-note">具体说明</label>
        <div class="rsm-area-wrap">
          <textarea
            id="rsm-note"
            v-model="note"
            class="rsm-area"
            maxlength="2000"
            rows="6"
          ></textarea>
          <span class="rsm-count">{{ note.length }}/2000</span>
        </div>

        <button type="button" class="rsm-confirm" :disabled="submitting" @click="submit">
          <span v-if="submitting" class="rsm-spinner" aria-hidden="true"></span>
          <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M22 2 11 13" />
            <path d="M22 2 15 22l-4-9-9-4z" />
          </svg>
          {{ submitting ? '提交中…' : '确认提交' }}
        </button>
        <button type="button" class="rsm-cancel" :disabled="submitting" @click="onClose">取消</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { mediaSeekApi, type TvSeason } from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import '../../styles/modal-glass.css'

export interface SubmitWork {
  tmdb_id?: string | null
  name: string
  year?: string
  /** movie / series；原名兜底（没绑候选）时为空 */
  kind?: 'movie' | 'series' | ''
  poster_url?: string | null
}

const props = defineProps<{
  open: boolean
  mode: 'seek' | 'chase'
  work: SubmitWork | null
}>()
const emit = defineEmits<{
  close: []
  /** 提交完成（新建或转附议都算完成）：父级据此刷新并跳作品详情页 */
  submitted: [payload: { created: boolean; requestId?: number; message?: string }]
}>()

const toast = useToast()
const note = ref('')
const submitting = ref(false)
const seasons = ref<TvSeason[]>([])
const loadingSeasons = ref(false)
const seasonsError = ref('')
const seasonsLoaded = ref(false)
const seasonAll = ref(true)
const chosenSeasons = ref<number[]>([])
let seasonsSeq = 0

const title = computed(() => (props.mode === 'seek' ? '提交求片' : '提交追新'))
const metaLine = computed(() => {
  const w = props.work
  if (!w) return ''
  const parts: string[] = []
  if (w.kind) parts.push(w.kind === 'movie' ? '电影' : '剧集')
  if (w.year) parts.push(w.year)
  return parts.join(' / ')
})
/* 季选只在求片＋剧集＋绑了候选时出现（追新不选季、原名兜底没候选） */
const showSeasons = computed(() => (
  props.mode === 'seek' && !!props.work?.tmdb_id && props.work?.kind === 'series'
))
const seasonParam = computed<string | undefined>(() => {
  if (!showSeasons.value || seasons.value.length === 0) return undefined
  if (seasonAll.value) return 'all'
  if (chosenSeasons.value.length === 0) return undefined
  return [...chosenSeasons.value].sort((a, b) => a - b).join(',')
})

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen || !props.work) return
    note.value = `${props.mode === 'seek' ? '求片' : '追新'}：${props.work.name}`
    seasons.value = []
    seasonsError.value = ''
    seasonsLoaded.value = false
    seasonAll.value = true
    chosenSeasons.value = []
    if (showSeasons.value && props.work.tmdb_id) void loadSeasons(props.work.tmdb_id)
  },
)

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
  if (props.work?.tmdb_id) void loadSeasons(props.work.tmdb_id)
}
function toggleSeasonAll(): void { seasonAll.value = true; chosenSeasons.value = [] }
function toggleSeason(n: number): void {
  seasonAll.value = false
  const i = chosenSeasons.value.indexOf(n)
  if (i >= 0) chosenSeasons.value.splice(i, 1)
  else chosenSeasons.value.push(n)
  if (chosenSeasons.value.length === 0) seasonAll.value = true
}

function onClose(): void {
  if (submitting.value) return
  emit('close')
}

async function submit(): Promise<void> {
  const w = props.work
  if (!w || submitting.value) return
  const text = note.value.trim()
  if (!text) { toast.error('先写清楚具体说明，再提交'); return }
  submitting.value = true
  try {
    if (props.mode === 'seek') {
      const r = await mediaSeekApi.create({
        movie_name: w.name,
        ...(w.year ? { year: w.year } : {}),
        ...(w.kind ? { type: w.kind } : {}),
        ...(w.tmdb_id ? { tmdb_id: w.tmdb_id } : {}),
        ...(seasonParam.value ? { season: seasonParam.value } : {}),
        note: text,
        ...(w.poster_url ? { poster_url: w.poster_url } : {}),
      })
      toast.success(r.created === false ? (r.message || '已附议') : '✓ 求片已提交')
      emit('submitted', {
        created: r.created !== false,
        requestId: r.request_id,
        message: r.message,
      })
    } else {
      if (!w.tmdb_id) { toast.error('缺少作品信息，请重新搜索选择'); return }
      const r = await mediaSeekApi.createChase({
        tmdb_id: w.tmdb_id,
        note: text,
        ...(w.kind ? { type: w.kind } : {}),
        ...(w.poster_url ? { poster_url: w.poster_url } : {}),
      })
      toast.success(r.created === false ? (r.message || '已附议') : '✓ 追新已提交')
      emit('submitted', {
        created: r.created !== false,
        requestId: r.request_id,
        message: r.message,
      })
    }
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
/* 提交弹层：与求片反馈页同色板（缺省深色、浅色换浅），尺寸照友站弹层
   （整卡圆角约 20、作品海报约 58×84、确认钮 50 高圆角 25）。 */
.rsm-mask {
  position: fixed; inset: 0; z-index: 1200;
  background: rgba(0, 0, 0, 0.62);
  display: flex; align-items: center; justify-content: center;
  padding: 18px;
}
.rsm-card {
  position: relative; width: 100%; max-width: 560px; max-height: 88dvh; overflow-y: auto;
  /* 面板玻璃化（2026-11-15 弹窗收尾批）：实心 #17181a/浅 #fff 换玻璃
     五件，令牌 styles/modal-glass.css（深浅随 data-theme）；尺寸/
     圆角/内边距不动，卡内元素配色照旧。 */
  background: var(--modal-glass-bg);
  border: 1px solid var(--modal-glass-edge);
  border-radius: 20px;
  box-shadow:
    inset 0 1px 0 var(--modal-glass-hi),
    inset 0 0 0 1px var(--modal-glass-ring),
    var(--modal-glass-shadow);
  backdrop-filter: var(--modal-glass-filter);
  -webkit-backdrop-filter: var(--modal-glass-filter);
  padding: 22px 20px 20px; color: #fbfcfd;
}
.rsm-title { margin: 0; text-align: center; font-size: 19px; font-weight: 800; }
.rsm-sub { margin: 8px 0 0; text-align: center; font-size: 13px; color: #9ca3af; line-height: 1.6; }
.rsm-x {
  position: absolute; top: 14px; right: 14px; width: 32px; height: 32px;
  border: none; border-radius: 999px; background: transparent; cursor: pointer;
  color: #9ca3af; font-size: 15px; font-family: inherit;
}
.rsm-x:active { background: rgba(255, 255, 255, 0.08); }
.rsm-work {
  display: flex; gap: 12px; align-items: center;
  margin-top: 18px; padding-top: 16px; border-top: 1px solid #26262a;
}
.rsm-work__poster { flex: none; width: 58px; height: 84px; object-fit: cover; border-radius: 10px; background: #1e1f21; }
.rsm-work__ph {
  flex: none; width: 58px; height: 84px; border-radius: 10px; background: #1e1f21;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 800; color: #55575c;
}
.rsm-work__info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.rsm-work__info b { font-size: 16px; word-break: break-word; }
.rsm-work__meta { font-size: 12px; color: #9ca3af; }
.rsm-seasons { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.rsm-seasons__label { font-size: 13px; font-weight: 700; }
.rsm-seasons__row { display: flex; flex-wrap: wrap; gap: 8px; }
.rsm-chipbtn {
  font-size: 12px; padding: 6px 12px; border-radius: 20px; cursor: pointer;
  background: #1e1f21; color: #d4d4d8; border: 1px solid #333336; font-family: inherit;
}
.rsm-chipbtn.on { background: #fbfcfd; color: #111111; border-color: #fbfcfd; font-weight: 700; }
.rsm-seasons__hint { font-size: 12px; color: #9ca3af; }
.rsm-seasons__err { font-size: 12px; color: #fca5a5; display: flex; gap: 8px; align-items: center; }
.rsm-label { display: block; margin-top: 18px; font-size: 14px; font-weight: 700; }
.rsm-area-wrap { position: relative; margin-top: 10px; }
.rsm-area {
  width: 100%; min-height: 150px; padding: 12px 14px 26px; resize: none;
  font-size: 14px; line-height: 1.7; color: #fbfcfd; font-family: inherit;
  background: #101012; border: 1px solid #26262a; border-radius: 14px; outline: none;
}
.rsm-area:focus { border-color: #6b7280; }
.rsm-count { position: absolute; right: 12px; bottom: 10px; font-size: 12px; color: #9ca3af; }
.rsm-confirm {
  position: relative;
  width: 100%; height: 50px; margin-top: 16px; border-radius: 25px; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  font-size: 15px; font-weight: 800; color: #ffffff; font-family: inherit;
  border: 1.5px solid transparent;
  background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d));
  box-shadow: var(--uhd-shadow-sheet);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 彩虹环切（texture-tokens-3）：彩虹只走 1.5px 细环（::before mask 环切），
   钮面只留玻璃层；浅色覆盖只换面背景，环由本处继承。 */
.rsm-confirm::before {
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
.rsm-confirm:disabled { opacity: 0.6; cursor: default; }
/* 禁用态彩虹退场：环随之一并隐藏 */
.rsm-confirm:disabled::before {
  display: none;
}
.rsm-spinner {
  width: 16px; height: 16px; border-radius: 50%; flex: none;
  border: 2px solid rgba(255, 255, 255, 0.35); border-top-color: #ffffff;
  animation: rsm-spin 0.8s linear infinite;
}
@keyframes rsm-spin { to { transform: rotate(360deg); } }
.rsm-cancel {
  width: 100%; height: 50px; margin-top: 10px; border-radius: 25px; cursor: pointer;
  font-size: 15px; font-weight: 700; color: #fbfcfd; font-family: inherit;
  background: #1e1f21; border: 1px solid #333336;
}
.rsm-cancel:disabled { opacity: 0.6; cursor: default; }

html[data-theme='light'] .rsm-card { color: #111111; }
html[data-theme='light'] .rsm-sub, html[data-theme='light'] .rsm-work__meta, html[data-theme='light'] .rsm-count, html[data-theme='light'] .rsm-seasons__hint { color: #6b7280; }
html[data-theme='light'] .rsm-work { border-top-color: #e5e5e5; }
html[data-theme='light'] .rsm-work__poster, html[data-theme='light'] .rsm-work__ph { background: #f0f0f1; }
html[data-theme='light'] .rsm-work__ph { color: #9ca3af; }
html[data-theme='light'] .rsm-chipbtn { background: #f5f5f5; color: #55575c; border-color: #e5e5e5; }
html[data-theme='light'] .rsm-chipbtn.on { background: #111111; color: #ffffff; border-color: #111111; }
html[data-theme='light'] .rsm-seasons__err { color: #dc2626; }
html[data-theme='light'] .rsm-area { background: #f8f8f8; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rsm-area:focus { border-color: #9ca3af; }
html[data-theme='light'] .rsm-confirm {
  background: var(--uhd-rainbow-soft);
  color: #111111;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l);
}
html[data-theme='light'] .rsm-spinner { border-color: rgba(0, 0, 0, 0.25); border-top-color: #111111; }
html[data-theme='light'] .rsm-cancel { background: #f5f5f5; border-color: #e5e5e5; color: #111111; }
</style>
