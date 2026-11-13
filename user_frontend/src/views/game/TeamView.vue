<template>
  <div class="tm-page">
    <div class="tm-inner">
      <!-- 页头：标题＋副标（照友站团队页），右上「创建团队套餐」 -->
      <header class="tm-head">
        <div class="tm-head__text">
          <h1 class="tm-title">团队管理</h1>
          <p class="tm-sub">创建或加入一个团队，与成员共享订阅与流量。</p>
        </div>
        <button type="button" class="tm-btn tm-btn--primary" @click="openApply">
          创建团队套餐
        </button>
      </header>

      <div v-if="loadError" class="tm-err">
        {{ loadError }}<button class="tm-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="tm-skeleton" aria-hidden="true"></div>

      <template v-else>
        <!-- 未加入：空态＋两张说明卡＋建团/入团 -->
        <template v-if="!team">
          <section class="tm-empty">
            <p class="tm-empty__title">尚未加入任何团队</p>
            <p class="tm-empty__text">
              成为会员中心独享个性化议价；加入或创建团队后，可共享订阅时长、流量与子账户权益。
            </p>
            <button type="button" class="tm-btn" @click="openApply">了解团体方案</button>
          </section>

          <div class="tm-cards">
            <div class="tm-info-card">
              <h3 class="tm-info-card__title">团队成员数</h3>
              <p class="tm-info-card__text">
                团长开通团队套餐后，按套餐的子账号数给成员分独立账号；子账号上限
                {{ config?.team_plan_max_members }} 个（后台可调）。未开通套餐的团队可先拉
                {{ config?.team_default_member_limit }} 人。
              </p>
            </div>
            <div class="tm-info-card">
              <h3 class="tm-info-card__title">共享账户数</h3>
              <p class="tm-info-card__text">
                成员共享团队套餐的订阅与流量。团队套餐按总流量计价，起订
                {{ minTrafficText }}（后台可调），提交申请后由管理员审核开通。
              </p>
            </div>
          </div>

          <section class="tm-sec">
            <div class="tm-sec__head"><h2 class="tm-sec__title">创建团队</h2></div>
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
          </section>

          <section class="tm-sec">
            <div class="tm-sec__head"><h2 class="tm-sec__title">加入团队</h2></div>
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

        <!-- 已加入：我的团队 -->
        <template v-else>
          <section class="tm-teamhead">
            <div class="tm-teamhead__main">
              <h2 class="tm-teamhead__name">{{ team.name }}</h2>
              <p class="tm-teamhead__meta">
                团长 {{ team.owner_username }} · {{ team.member_count }} / {{ team.member_limit }} 人
              </p>
            </div>
            <span class="tm-chip" :class="`tm-chip--${team.my_role}`">
              {{ team.my_role === 'owner' ? '团长' : '成员' }}
            </span>
          </section>

          <section class="tm-sec">
            <div class="tm-sec__head"><h2 class="tm-sec__title">团队套餐</h2></div>
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
          </section>

          <section class="tm-sec">
            <div class="tm-sec__head"><h2 class="tm-sec__title">成员</h2></div>
            <div v-for="m in team.members" :key="m.user_id" class="tm-row">
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
          </section>

          <section class="tm-sec">
            <div class="tm-sec__head"><h2 class="tm-sec__title">团队操作</h2></div>
            <div class="tm-actions">
              <button
                v-if="team.my_role === 'owner'"
                type="button"
                class="tm-btn"
                @click="openApply"
              >
                {{ team.plan_status === 'active' ? '变更团队套餐' : '申请团队套餐' }}
              </button>
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
          </section>
        </template>

        <!-- 套餐申请表单 -->
        <section v-if="applyOpen" class="tm-sec" id="team-apply">
          <div class="tm-sec__head"><h2 class="tm-sec__title">团队套餐申请</h2></div>
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
        </section>

        <!-- 我的申请 -->
        <section v-if="applications.length" class="tm-sec">
          <div class="tm-sec__head"><h2 class="tm-sec__title">我的套餐申请</h2></div>
          <div v-for="a in applications" :key="a.id" class="tm-row tm-row--wrap">
            <span class="tm-row__name">{{ a.team_name || '团队套餐' }}</span>
            <span class="tm-row__value">
              {{ fmtBytes(a.traffic_bytes) }} · {{ a.member_limit }} 个子账号
            </span>
            <span class="tm-chip" :class="`tm-chip--${a.status}`">{{ appStatusText(a.status) }}</span>
            <span v-if="a.review_note" class="tm-row__note">{{ a.review_note }}</span>
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
</template>

<script setup lang="ts">
/**
 * 团队管理（/game/account/team）——2026-11-13 照 UHDNow 团队页落地：
 * 标题/副标/右上「创建团队套餐」/未加入空态/两张说明卡与友站同形；
 * 机制按本站口径：建团与邀请码入团直接可用，团队套餐走「提交申请 →
 * 管理端审核 → 开通登记」的人工流（友站同样是联系客服审核开通）。
 * 流量计量与在线支付本站未接入，申请表单里如实标注、不假装已接通。
 * 页面跟随用户外观（浅/深两套本地变量，与概览页同口径）。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
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
/* 团队管理：跟随用户外观的平面结构。本地主题变量：缺省深色，
   html[data-theme='light'] 时整套换浅（与概览页同口径）。 */
.tm-page {
  --tm-line: rgba(255, 255, 255, 0.1);
  --tm-card: #17181c;
  --tm-input-bg: rgba(255, 255, 255, 0.06);
  --tm-text-2: rgba(255, 255, 255, 0.62);
  --tm-text-3: rgba(255, 255, 255, 0.42);
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
.tm-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.tm-title {
  margin: 0;
  font-size: 30px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
.tm-sub {
  margin: 8px 0 0;
  font-size: 15px;
  color: var(--tm-text-2);
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
  margin-top: 32px;
  height: 220px;
  border-radius: 16px;
  background: var(--tm-card);
  opacity: 0.6;
}
.tm-empty {
  margin-top: 40px;
  text-align: left;
}
.tm-empty__title {
  margin: 0;
  font-size: 24px;
  font-weight: var(--uhd-fw-bold);
}
.tm-empty__text {
  margin: 12px 0 20px;
  font-size: 15px;
  line-height: 1.6;
  color: var(--tm-text-2);
  max-width: 560px;
}
.tm-cards {
  margin-top: 28px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.tm-info-card {
  border: 1px solid var(--tm-line);
  border-radius: 16px;
  padding: 18px;
  background: var(--tm-card);
}
.tm-info-card__title {
  margin: 0;
  font-size: 16px;
  font-weight: var(--uhd-fw-semibold);
}
.tm-info-card__text {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--tm-text-2);
}
.tm-sec { margin-top: 42px; }
.tm-sec__head {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--tm-line);
}
.tm-sec__title {
  margin: 0;
  font-size: 22px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
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
.tm-teamhead {
  margin-top: 36px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}
.tm-teamhead__name {
  margin: 0;
  font-size: 26px;
  font-weight: var(--uhd-fw-bold);
}
.tm-teamhead__meta {
  margin: 8px 0 0;
  font-size: 14.5px;
  color: var(--tm-text-2);
}
.tm-chip {
  flex: 0 0 auto;
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--tm-line);
  font-size: 13px;
  color: var(--tm-text-2);
}
.tm-chip--owner, .tm-chip--approved { color: var(--uhd-success); border-color: rgba(22, 163, 74, 0.45); }
.tm-chip--pending { color: var(--uhd-warn-text-2); border-color: rgba(217, 119, 6, 0.45); }
.tm-chip--rejected { color: #f87171; border-color: rgba(220, 38, 38, 0.45); }
.tm-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 56px;
  padding: 6px 2px;
  border-bottom: 1px solid var(--tm-line);
  font-size: 15px;
}
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
.tm-actions {
  margin-top: 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.tm-apply { margin-top: 16px; display: grid; gap: 14px; }
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
  .tm-cards { grid-template-columns: 1fr; }
  .tm-head { flex-direction: column; }
  .tm-row__value { white-space: normal; text-align: right; }
}

/* 浅色覆盖：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .tm-page {
  background: #f8f8f8;
  color: #111111;
  --tm-line: #e7e7e7;
  --tm-card: #ffffff;
  --tm-input-bg: #f5f5f5;
  --tm-text-2: #55575c;
  --tm-text-3: #9a9da3;
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
</style>
