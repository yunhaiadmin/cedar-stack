<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳；手机原样 -->
  <UhdAccountShellGate>
  <div class="tm-page">
    <div class="tm-inner">
      <header class="tm-head">
        <h1 class="tm-title">团队管理</h1>
      </header>

      <div v-if="loadError" class="tm-err">
        {{ loadError }}<button class="tm-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="tm-skeleton" aria-hidden="true"></div>

      <template v-else>
        <!-- 说明卡：盾牌标题＋三行统计＋客服审核提示＋整宽彩虹钮（照友站团队页结构） -->
        <section class="tm-card tm-intro">
          <div class="tm-intro__head">
            <span class="tm-intro__icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3l7 2.8v5.4c0 4.5-3 7.7-7 9.3-4-1.6-7-4.8-7-9.3V5.8L12 3z" />
                <path d="M9 11.6l2.2 2.2 4-4.2" />
              </svg>
            </span>
            <div class="tm-intro__text">
              <h2 class="tm-intro__title">为团队分配独立账号</h2>
              <p class="tm-intro__desc">
                开通团队套餐后，按套餐总流量给成员分独立账号，成员配额会按套餐规则自动计算。
              </p>
            </div>
          </div>

          <div class="tm-stats">
            <div class="tm-stat">
              <p class="tm-stat__label">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M4.5 15.5a8 8 0 1 1 15 0" />
                  <path d="M12 15.5l4.2-4.2" />
                  <circle cx="12" cy="15.5" r="1.3" fill="currentColor" stroke="none" />
                </svg>
                套餐最低起订
              </p>
              <p class="tm-stat__value">{{ minTrafficText }}</p>
            </div>
            <div class="tm-stat">
              <p class="tm-stat__label">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <circle cx="9" cy="8" r="3.4" />
                  <path d="M3.5 19.5c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5" />
                  <circle cx="17.2" cy="9" r="2.5" />
                  <path d="M16.4 14.6c2.3.3 3.7 1.9 4.1 4.4" />
                </svg>
                子账号上限
              </p>
              <p class="tm-stat__value">{{ config?.team_plan_max_members }} 个</p>
            </div>
            <div class="tm-stat">
              <p class="tm-stat__label">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M13 2.8L4.8 13.6h5.7l-1.2 7.6 8.2-10.8h-5.7l1.2-7.6z" />
                </svg>
                成员配额
              </p>
              <p class="tm-stat__value">自动计算</p>
            </div>
          </div>

          <div class="tm-review">
            <p class="tm-review__title">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="8.6" />
                <path d="M8.4 12.4l2.4 2.4 4.8-5.2" />
              </svg>
              支付前请先联系客服审核
            </p>
            <p class="tm-review__text">
              申请提交后可随时查看进度。为保障服务安全，请等管理员审核通过后再付款；
              购买成功后还需联系客服开通播放权限，否则无法使用。
            </p>
            <button type="button" class="tm-create" @click="openApply">
              <span aria-hidden="true">＋</span>{{ createBtnText }}<span aria-hidden="true">→</span>
            </button>
          </div>
        </section>

        <!-- 成员账号：未加入/未开通时也在，空态照友站文案 -->
        <section class="tm-members">
          <div class="tm-members__head">
            <h2 class="tm-sec-title">成员账号</h2>
            <button type="button" class="tm-add-btn" @click="addMember">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="10" cy="8" r="3.4" />
                <path d="M4.5 19.5c.6-3.2 2.8-5 5.5-5 1.5 0 2.9.5 3.9 1.5" />
                <path d="M18.5 15v6M15.5 18h6" />
              </svg>
              添加成员
            </button>
          </div>

          <div class="tm-search-row">
            <label class="tm-search">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">
                <circle cx="11" cy="11" r="6.5" />
                <path d="M16 16l4.5 4.5" />
              </svg>
              <input
                v-model="memberDraft"
                type="text"
                placeholder="输入成员名称"
                aria-label="输入成员名称"
                @keyup.enter="applySearch"
              />
            </label>
            <button type="button" class="tm-search-btn" @click="applySearch">搜索</button>
          </div>

          <div class="tm-filter-row">
            <label class="tm-select">
              <select v-model="memberSort" aria-label="成员排序">
                <option value="name">按名称</option>
                <option value="joined">按加入时间</option>
              </select>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M6 9.5l6 6 6-6" />
              </svg>
            </label>
            <label class="tm-select">
              <select v-model="memberRole" aria-label="成员状态筛选">
                <option value="all">全部状态</option>
                <option value="owner">团长</option>
                <option value="member">成员</option>
              </select>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M6 9.5l6 6 6-6" />
              </svg>
            </label>
          </div>

          <div v-if="!team" class="tm-card tm-empty-card">请先开通团队套餐</div>
          <div v-else class="tm-card tm-list-card">
            <div v-for="m in visibleMembers" :key="m.user_id" class="tm-row">
              <span class="tm-avatar" aria-hidden="true">{{ initial(m.username) }}</span>
              <span class="tm-row__name">{{ m.username }}</span>
              <span class="tm-row__tag">{{ m.role === 'owner' ? '团长' : '成员' }}</span>
              <span class="tm-row__value">{{ fmtDate(m.joined_at) }}</span>
              <button
                v-if="team.my_role === 'owner' && m.role !== 'owner'"
                type="button"
                class="tm-mini-btn tm-mini-btn--danger"
                :disabled="busy"
                @click="askRemove(m)"
              >
                移除
              </button>
            </div>
            <p v-if="!visibleMembers.length" class="tm-no-match">没有符合条件的成员</p>
          </div>
        </section>

        <!-- 已加入：我的团队（套餐与邀请码、团队操作） -->
        <section v-if="team" class="tm-sec">
          <h2 class="tm-sec-title">我的团队</h2>
          <div class="tm-card tm-list-card">
            <div class="tm-row">
              <span class="tm-row__name">团队名称</span>
              <span class="tm-row__value">{{ team.name }}</span>
            </div>
            <div class="tm-row">
              <span class="tm-row__name">团长</span>
              <span class="tm-row__value">{{ team.owner_username }}</span>
            </div>
            <div class="tm-row">
              <span class="tm-row__name">成员人数</span>
              <span class="tm-row__value">{{ team.member_count }} / {{ team.member_limit }} 人</span>
            </div>
            <div class="tm-row">
              <span class="tm-row__name">套餐状态</span>
              <span class="tm-row__value">{{ planStatusText }}</span>
            </div>
            <div v-if="team.plan_status === 'active'" class="tm-row">
              <span class="tm-row__name">套餐流量</span>
              <span class="tm-row__value">
                {{ fmtBytes(team.plan_traffic_bytes) }}<template v-if="team.plan_line"> · {{ team.plan_line }}</template>
              </span>
            </div>
            <div class="tm-row">
              <span class="tm-row__name">邀请码</span>
              <span class="tm-row__value tm-code">{{ team.invite_code }}</span>
              <button type="button" class="tm-mini-btn" @click="copyCode">复制</button>
            </div>
            <p class="tm-note">把邀请码发给要拉进团队的人，对方在本页「加入团队」里填码即可。</p>
            <div class="tm-card-actions">
              <button
                v-if="team.my_role === 'member'"
                type="button"
                class="tm-btn tm-btn--danger"
                :disabled="busy"
                @click="leaveOpen = true"
              >
                退出团队
              </button>
              <button
                v-if="team.my_role === 'owner'"
                type="button"
                class="tm-btn tm-btn--danger"
                :disabled="busy"
                @click="dissolveOpen = true"
              >
                解散团队
              </button>
            </div>
          </div>
        </section>

        <!-- 未加入：创建 / 加入团队 -->
        <template v-if="!team">
          <section class="tm-sec">
            <h2 class="tm-sec-title">创建团队</h2>
            <form class="tm-form-row" @submit.prevent="doCreate">
              <input
                v-model="createName"
                class="tm-input"
                type="text"
                maxlength="100"
                placeholder="给团队起个名字"
                aria-label="团队名称"
              />
              <button type="submit" class="tm-btn" :disabled="busy || !createName.trim()">
                创建
              </button>
            </form>
            <p class="tm-note">
              未开通套餐的团队可先拉 {{ config?.team_default_member_limit }} 人（后台可调）。
            </p>
          </section>

          <section class="tm-sec">
            <h2 class="tm-sec-title">加入团队</h2>
            <form class="tm-form-row" @submit.prevent="doJoin">
              <input
                v-model="joinCode"
                class="tm-input"
                type="text"
                maxlength="20"
                placeholder="输入团长给的邀请码"
                aria-label="团队邀请码"
              />
              <button type="submit" class="tm-btn" :disabled="busy || !joinCode.trim()">
                加入
              </button>
            </form>
          </section>
        </template>

        <!-- 套餐申请表单（走管理端审核流） -->
        <section v-if="applyOpen" class="tm-sec" id="team-apply">
          <h2 class="tm-sec-title">团队套餐申请</h2>
          <div class="tm-card tm-apply-card">
            <form class="tm-apply" @submit.prevent="doApply">
              <label v-if="!team" class="tm-field">
                <span class="tm-field__label">团队名称</span>
                <input
                  v-model="applyForm.team_name"
                  class="tm-input"
                  type="text"
                  maxlength="100"
                  placeholder="审核通过后按这个名字建团"
                />
              </label>
              <label class="tm-field">
                <span class="tm-field__label">线路（选填）</span>
                <input
                  v-model="applyForm.line"
                  class="tm-input"
                  type="text"
                  maxlength="100"
                  placeholder="如无指定可留空，客服开通时确认"
                />
              </label>
              <label class="tm-field">
                <span class="tm-field__label">总流量（TB）</span>
                <input
                  v-model.number="applyForm.traffic_tb"
                  class="tm-input"
                  type="number"
                  min="0"
                  step="1"
                  inputmode="numeric"
                />
                <span class="tm-field__hint">起订 {{ minTrafficText }}，按总流量计价。</span>
              </label>
              <label class="tm-field">
                <span class="tm-field__label">子账号数</span>
                <input
                  v-model.number="applyForm.member_limit"
                  class="tm-input"
                  type="number"
                  min="1"
                  step="1"
                  inputmode="numeric"
                />
                <span class="tm-field__hint">最多 {{ config?.team_plan_max_members }} 个。</span>
              </label>
              <label class="tm-field">
                <span class="tm-field__label">备注（选填）</span>
                <textarea
                  v-model="applyForm.note"
                  class="tm-input tm-textarea"
                  rows="3"
                  maxlength="2000"
                  placeholder="联系方式、期望开通时间等"
                ></textarea>
              </label>
              <p class="tm-apply__notice">
                套餐开通走人工审核：提交后管理员审核，通过即开通登记，付款与开通细节由客服联系你确认。
                本站暂未接入团队流量计量与在线支付，申请里的流量是套餐额度登记，不会在线扣款。
              </p>
              <div class="tm-actions">
                <button type="submit" class="tm-btn tm-btn--primary" :disabled="busy">
                  提交申请
                </button>
                <button type="button" class="tm-btn" :disabled="busy" @click="applyOpen = false">
                  取消
                </button>
              </div>
            </form>
          </div>
        </section>

        <!-- 我的申请 -->
        <section v-if="applications.length" class="tm-sec">
          <h2 class="tm-sec-title">我的套餐申请</h2>
          <div class="tm-card tm-list-card">
            <div v-for="a in applications" :key="a.id" class="tm-row tm-row--wrap">
              <span class="tm-row__name">{{ a.team_name || '团队套餐' }}</span>
              <span class="tm-row__value">
                {{ fmtBytes(a.traffic_bytes) }} · {{ a.member_limit }} 个子账号
              </span>
              <span class="tm-chip" :class="`tm-chip--${a.status}`">{{ appStatusText(a.status) }}</span>
              <span v-if="a.review_note" class="tm-row__note">{{ a.review_note }}</span>
            </div>
          </div>
        </section>
      </template>
    </div>

    <!-- 退出确认 -->
    <Modal v-model="leaveOpen" size="sm">
      <template #header><h3 class="tm-modal-title">退出团队</h3></template>
      <p class="tm-modal-text">退出后将不再共享团队套餐，确定退出吗？</p>
      <template #footer>
        <div class="tm-modal-actions">
          <button type="button" class="tm-btn" @click="leaveOpen = false">再想想</button>
          <button type="button" class="tm-btn tm-btn--danger" :disabled="busy" @click="doLeave">
            确定退出
          </button>
        </div>
      </template>
    </Modal>

    <!-- 解散确认 -->
    <Modal v-model="dissolveOpen" size="sm">
      <template #header><h3 class="tm-modal-title">解散团队</h3></template>
      <p class="tm-modal-text">解散后全部成员都会离开团队、邀请码失效，确定解散吗？</p>
      <template #footer>
        <div class="tm-modal-actions">
          <button type="button" class="tm-btn" @click="dissolveOpen = false">再想想</button>
          <button type="button" class="tm-btn tm-btn--danger" :disabled="busy" @click="doDissolve">
            确定解散
          </button>
        </div>
      </template>
    </Modal>

    <!-- 移除成员确认 -->
    <Modal v-model="removeOpen" size="sm">
      <template #header><h3 class="tm-modal-title">移除成员</h3></template>
      <p class="tm-modal-text">把 {{ pendingRemove?.username }} 移出团队吗？</p>
      <template #footer>
        <div class="tm-modal-actions">
          <button type="button" class="tm-btn" @click="removeOpen = false">再想想</button>
          <button type="button" class="tm-btn tm-btn--danger" :disabled="busy" @click="doRemove">
            确定移除
          </button>
        </div>
      </template>
    </Modal>
  </div>
  </UhdAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 团队管理（/game/account/team）——2026-11-13 照 UHDNow 团队页落地（#120），
 * 细节对齐单 C 再按友站真机截图重排结构：页题 → 说明卡（盾牌标题＋
 * 「套餐最低起订／子账号上限／成员配额」三行统计＋客服审核提示＋整宽
 * 彩虹钮）→ 成员账号区（添加成员钮＋搜索＋排序/状态筛选＋空态或成员表）。
 * 友站统计前两行是双线（Global/Premium）起订量，本站无双线概念，映射为
 * 我方配置行：起订读 team_plan_min_traffic_bytes、子账号上限读
 * team_plan_max_members（均后台可调，不写死数值）。
 * 机制仍是 #120 口径：建团与邀请码入团直接可用；「添加成员」即复制邀请码；
 * 团队套餐走「提交申请 → 管理端审核 → 开通登记」的人工流（友站同样是
 * 联系客服审核开通）。流量计量与在线支付本站未接入，申请表单里如实标注。
 * 页面跟随用户外观（浅/深两套本地变量，与概览页同口径）。
 */
import './team-view.css'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import UhdAccountShellGate from '@/components/uhd/UhdAccountShellGate.vue'
import Modal from '@/components/ui/Modal.vue'
import { teamApi, type MyTeam, type TeamApplication, type TeamConfig, type TeamMemberRow } from '@/api/team'
import { useToast } from '@/composables/useToast'

const toast = useToast()

const loading = ref(true)
const loadError = ref('')
const busy = ref(false)
const team = ref<MyTeam | null>(null)
const config = ref<TeamConfig | null>(null)
const applications = ref<TeamApplication[]>([])

const createName = ref('')
const joinCode = ref('')

const memberDraft = ref('')
const memberQuery = ref('')
const memberSort = ref<'name' | 'joined'>('name')
const memberRole = ref<'all' | 'owner' | 'member'>('all')

const applyOpen = ref(false)
const applyForm = reactive({
  team_name: '',
  line: '',
  traffic_tb: 10,
  member_limit: 10,
  note: '',
})

const leaveOpen = ref(false)
const dissolveOpen = ref(false)
const removeOpen = ref(false)
const pendingRemove = ref<TeamMemberRow | null>(null)

const TB = 10 ** 12

const minTrafficText = computed(() => {
  const min = config.value?.team_plan_min_traffic_bytes
  return min ? fmtBytes(min) : '—'
})

const planStatusText = computed(() => {
  if (!team.value) return ''
  if (team.value.plan_status === 'active') return '已开通'
  if (team.value.plan_status === 'pending') return '审核中'
  return '未开通'
})

const createBtnText = computed(() => {
  if (team.value?.plan_status === 'active') return '变更团队套餐'
  return '创建团队套餐'
})

const visibleMembers = computed<TeamMemberRow[]>(() => {
  if (!team.value) return []
  const q = memberQuery.value.trim().toLowerCase()
  const rows = team.value.members.filter((m) => {
    if (memberRole.value !== 'all' && m.role !== memberRole.value) return false
    if (q && !m.username.toLowerCase().includes(q)) return false
    return true
  })
  return [...rows].sort((a, b) => {
    if (a.role !== b.role) return a.role === 'owner' ? -1 : 1
    if (memberSort.value === 'name') return a.username.localeCompare(b.username, 'zh')
    return (a.joined_at || '').localeCompare(b.joined_at || '')
  })
})

function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  if (bytes >= TB) {
    const tb = bytes / TB
    return `${Number.isInteger(tb) ? tb : tb.toFixed(1)} TB`
  }
  const gb = bytes / 10 ** 9
  return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`
}

function fmtDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function initial(name: string): string {
  return (name.trim().charAt(0) || '团').toUpperCase()
}

function appStatusText(status: TeamApplication['status']): string {
  if (status === 'approved') return '已通过'
  if (status === 'rejected') return '已驳回'
  return '待审核'
}

function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await teamApi.mine()
    team.value = data.team
    config.value = data.config
    applications.value = data.applications
    // 申请表单默认值跟配置走（起订 TB / 子账号默认取起订口径的整数档）
    if (data.config) {
      applyForm.traffic_tb = Math.max(1, Math.round(data.config.team_plan_min_traffic_bytes / TB))
    }
  } catch (e) {
    loadError.value = errText(e, '团队信息加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function doCreate() {
  if (busy.value || !createName.value.trim()) return
  busy.value = true
  try {
    const res = await teamApi.create(createName.value.trim())
    team.value = res.team
    createName.value = ''
    toast.success('团队已创建')
  } catch (e) {
    toast.error(errText(e, '创建失败，请稍后重试'))
  } finally {
    busy.value = false
  }
}

async function doJoin() {
  if (busy.value || !joinCode.value.trim()) return
  busy.value = true
  try {
    const res = await teamApi.join(joinCode.value.trim())
    team.value = res.team
    joinCode.value = ''
    toast.success('已加入团队')
  } catch (e) {
    toast.error(errText(e, '加入失败，请检查邀请码'))
  } finally {
    busy.value = false
  }
}

async function copyCode() {
  if (!team.value) return
  try {
    await navigator.clipboard.writeText(team.value.invite_code)
    toast.success('邀请码已复制')
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

/** 添加成员＝本站的邀请码机制：团长点按即复制邀请码；其余状态给人话提示。 */
function addMember() {
  if (!team.value) {
    toast.error('请先开通团队套餐')
    return
  }
  if (team.value.my_role !== 'owner') {
    toast.error('只有团长可以添加成员')
    return
  }
  void copyCode()
}

function applySearch() {
  memberQuery.value = memberDraft.value
}

function askRemove(m: TeamMemberRow) {
  pendingRemove.value = m
  removeOpen.value = true
}

async function doRemove() {
  if (!pendingRemove.value) return
  busy.value = true
  try {
    await teamApi.removeMember(pendingRemove.value.user_id)
    removeOpen.value = false
    pendingRemove.value = null
    toast.success('成员已移除')
    await load()
  } catch (e) {
    toast.error(errText(e, '移除失败，请稍后重试'))
  } finally {
    busy.value = false
  }
}

async function doLeave() {
  busy.value = true
  try {
    await teamApi.leave()
    leaveOpen.value = false
    toast.success('已退出团队')
    await load()
  } catch (e) {
    toast.error(errText(e, '退出失败，请稍后重试'))
  } finally {
    busy.value = false
  }
}

async function doDissolve() {
  busy.value = true
  try {
    await teamApi.dissolve()
    dissolveOpen.value = false
    toast.success('团队已解散')
    await load()
  } catch (e) {
    toast.error(errText(e, '解散失败，请稍后重试'))
  } finally {
    busy.value = false
  }
}

function openApply() {
  if (team.value && team.value.my_role === 'member') {
    toast.error('团队套餐由团长申请')
    return
  }
  applyOpen.value = true
  void nextTick(() => {
    document.getElementById('team-apply')?.scrollIntoView({ behavior: 'smooth' })
  })
}

async function doApply() {
  if (busy.value) return
  const trafficBytes = Math.round((applyForm.traffic_tb || 0) * TB)
  busy.value = true
  try {
    await teamApi.apply({
      team_name: team.value ? null : applyForm.team_name.trim() || null,
      line: applyForm.line.trim() || null,
      traffic_bytes: trafficBytes,
      member_limit: applyForm.member_limit || 0,
      note: applyForm.note.trim() || null,
    })
    applyOpen.value = false
    toast.success('申请已提交，等待管理员审核')
    await load()
  } catch (e) {
    toast.error(errText(e, '提交失败，请稍后重试'))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* 团队管理：跟随用户外观的卡片结构（细节对齐单 C 照友站团队页重排）。
   本地主题变量：缺省深色，html[data-theme='light'] 时整套换浅。 */
.tm-page {
  min-height: 100dvh;
  background: #050505;
  color: #ffffff;
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.tm-inner {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 20px 96px;
}
.tm-head { margin-bottom: 26px; }
.tm-title {
  margin: 0;
  font-size: 30px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
.tm-card {
  border: 1px solid var(--tm-line);
  border-radius: 22px;
  background: var(--tm-card);
}
.tm-sec-title {
  margin: 0;
  font-size: 23px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
.tm-btn {
  flex: 0 0 auto;
  min-height: 40px;
  padding: 0 18px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 14.5px;
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
.tm-btn:active { opacity: 0.72; }
.tm-btn:disabled { opacity: 0.45; cursor: default; }
/* 主按钮（默认深色面）：彩虹边框＋深色毛玻璃底＋白字——照 UhdButton 配方，
   填充色必须包进 linear-gradient，裸色值放非末层会让整条 background 失效 */
.tm-btn--primary {
  border: 1.5px solid transparent;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  color: #ffffff;
  font-weight: var(--uhd-fw-semibold);
}
.tm-btn--danger { color: #f87171; border-color: rgba(220, 38, 38, 0.4); }
.tm-err {
  margin-top: 28px;
  font-size: 14.5px;
  color: #f87171;
}
.tm-retry {
  margin-left: 10px;
  border: none;
  background: none;
  color: inherit;
  text-decoration: underline;
  cursor: pointer;
  font-family: inherit;
  font-size: inherit;
}
.tm-skeleton {
  height: 220px;
  border-radius: 22px;
  background: var(--tm-card);
  opacity: 0.6;
}

/* —— 说明卡 —— */
.tm-intro { padding: 22px 20px; overflow: hidden; }
.tm-intro__head {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.tm-intro__icon {
  flex: 0 0 auto;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: var(--tm-input-bg);
  display: grid;
  place-items: center;
}
.tm-intro__icon svg { width: 26px; height: 26px; }
.tm-intro__title {
  margin: 2px 0 0;
  font-size: 21px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
.tm-intro__desc {
  margin: 7px 0 0;
  font-size: 14.5px;
  line-height: 1.6;
  color: var(--tm-text-2);
}
.tm-stats {
  margin-top: 24px;
  display: grid;
  gap: 22px;
}
.tm-stat__label {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
  color: var(--tm-text-2);
}
.tm-stat__label svg { width: 17px; height: 17px; }
.tm-stat__value {
  margin: 6px 0 0;
  font-size: 27px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
/* 卡底灰区：客服审核提示＋整宽创建钮（照友站卡内底部分区） */
.tm-review {
  margin: 24px -20px -22px;
  padding: 20px 20px 22px;
  background: var(--tm-input-bg);
}
.tm-review__title {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16.5px;
  font-weight: var(--uhd-fw-semibold);
}
.tm-review__title svg { width: 20px; height: 20px; flex: 0 0 auto; }
.tm-review__text {
  margin: 9px 0 0;
  font-size: 14px;
  line-height: 1.65;
  color: var(--tm-text-2);
}
/* 创建钮（默认深色面）：彩虹描边＋深色玻璃填充，与主按钮同配方 */
.tm-create {
  margin-top: 18px;
  width: 100%;
  min-height: 54px;
  border-radius: 999px;
  border: 1.5px solid transparent;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  color: #ffffff;
  font-family: inherit;
  font-size: 16.5px;
  font-weight: var(--uhd-fw-semibold);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  cursor: pointer;
}
.tm-create:active { opacity: 0.8; }

/* —— 成员账号 —— */
.tm-members { margin-top: 36px; }
.tm-members__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.tm-add-btn {
  flex: 0 0 auto;
  min-height: 40px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  background: var(--tm-card);
  color: inherit;
  font-family: inherit;
  font-size: 14px;
  font-weight: var(--uhd-fw-medium);
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
}
.tm-add-btn svg { width: 17px; height: 17px; }
.tm-add-btn:active { opacity: 0.72; }
.tm-search-row {
  margin-top: 16px;
  display: flex;
  gap: 10px;
}
.tm-search {
  flex: 1;
  min-width: 0;
  min-height: 48px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  border-radius: 14px;
  border: 1px solid var(--tm-line);
  background: var(--tm-card);
}
.tm-search svg { width: 18px; height: 18px; flex: 0 0 auto; color: var(--tm-text-3); }
.tm-search input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 15px;
  outline: none;
}
.tm-search-btn {
  flex: 0 0 auto;
  min-height: 48px;
  padding: 0 22px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  background: var(--tm-card);
  color: inherit;
  font-family: inherit;
  font-size: 15px;
  cursor: pointer;
}
.tm-search-btn:active { opacity: 0.72; }
.tm-filter-row {
  margin-top: 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.tm-select {
  position: relative;
  display: block;
}
.tm-select select {
  width: 100%;
  min-height: 48px;
  padding: 0 40px 0 18px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  background: var(--tm-card);
  color: inherit;
  font-family: inherit;
  font-size: 15px;
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
}
.tm-select > svg {
  position: absolute;
  right: 16px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  pointer-events: none;
  color: var(--tm-text-3);
}
.tm-empty-card {
  margin-top: 16px;
  min-height: 190px;
  display: grid;
  place-items: center;
  font-size: 15.5px;
  color: var(--tm-text-3);
}
.tm-list-card {
  margin-top: 16px;
  padding: 4px 18px;
}
.tm-no-match {
  margin: 0;
  padding: 26px 0;
  text-align: center;
  font-size: 14.5px;
  color: var(--tm-text-3);
}

/* —— 列表行 / 表单 —— */
.tm-sec { margin-top: 40px; }
.tm-sec .tm-sec-title { margin-bottom: 2px; }
.tm-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 56px;
  padding: 6px 0;
  border-bottom: 1px solid var(--tm-line);
  font-size: 15px;
}
.tm-list-card .tm-row:last-of-type { border-bottom: none; }
.tm-row--wrap { flex-wrap: wrap; }
.tm-row__name { font-weight: var(--uhd-fw-medium); }
.tm-row__value { margin-left: auto; color: var(--tm-text-2); white-space: nowrap; }
.tm-row__tag { font-size: 13px; color: var(--tm-text-3); }
.tm-row__note { width: 100%; font-size: 13px; color: var(--tm-text-2); }
.tm-code { font-family: var(--uhd-font-mono); letter-spacing: 0.08em; }
.tm-avatar {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--tm-input-bg);
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
}
.tm-mini-btn {
  flex: 0 0 auto;
  min-height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.tm-mini-btn--danger { color: #f87171; }
.tm-note {
  margin: 12px 0 0;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--tm-text-3);
}
.tm-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 14px 0 16px;
}
.tm-chip {
  flex: 0 0 auto;
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  font-size: 13px;
  color: var(--tm-text-2);
}
.tm-chip--approved { color: var(--uhd-success); border-color: rgba(22, 163, 74, 0.45); }
.tm-chip--pending { color: var(--uhd-warn-text-2); border-color: rgba(217, 119, 6, 0.45); }
.tm-chip--rejected { color: #f87171; border-color: rgba(220, 38, 38, 0.45); }
.tm-form-row {
  margin-top: 16px;
  display: flex;
  gap: 10px;
}
.tm-input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  padding: 0 14px;
  border-radius: 12px;
  border: 1px solid var(--tm-line);
  background: var(--tm-input-bg);
  color: inherit;
  font-family: inherit;
  font-size: 15px;
  box-sizing: border-box;
}
.tm-textarea {
  padding: 10px 14px;
  resize: vertical;
}
.tm-actions {
  margin-top: 4px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.tm-apply-card { margin-top: 16px; padding: 18px; }
.tm-apply { display: grid; gap: 14px; }
.tm-field { display: grid; gap: 6px; }
.tm-field__label { font-size: 14px; font-weight: var(--uhd-fw-medium); }
.tm-field__hint { font-size: 12.5px; color: var(--tm-text-3); }
.tm-apply__notice {
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--tm-input-bg);
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--tm-text-2);
}
.tm-modal-title { margin: 0; font-size: 17px; font-weight: var(--uhd-fw-semibold); }
.tm-modal-text { margin: 0; font-size: 14.5px; line-height: 1.6; }
.tm-modal-actions { display: flex; justify-content: flex-end; gap: 10px; }

@media (max-width: 640px) {
  .tm-row__value { white-space: normal; text-align: right; }
}

/* 浅色覆盖：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .tm-page {
  background: #f8f8f8;
  color: #111111;
}
html[data-theme='light'] .tm-card {
  box-shadow: 0 1px 2px rgba(17, 17, 17, 0.04), 0 10px 30px rgba(17, 17, 17, 0.05);
}
html[data-theme='light'] .tm-btn--danger { color: #dc2626; }
html[data-theme='light'] .tm-btn--primary {
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  background-color: var(--uhd-l-surface);
  color: #111111;
}
html[data-theme='light'] .tm-mini-btn--danger { color: #dc2626; }
html[data-theme='light'] .tm-err { color: #dc2626; }
html[data-theme='light'] .tm-chip--rejected { color: #dc2626; }
/* 创建钮（浅色面）：粉彩虹填充＋彩虹描边＋深字——照友站整宽钮形态 */
html[data-theme='light'] .tm-create {
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  background-color: var(--uhd-l-surface);
  color: #111111;
}
</style>
