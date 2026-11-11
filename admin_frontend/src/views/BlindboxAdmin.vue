<script setup lang="ts">
/**
 * 盲盒管理（双服通用，PR-2）：总开关 + 按服档位/奖池编辑 + 估值表
 * + 抽盒统计 + 失败补发。配置契约与校验都在 backend/blindbox
 * （保存时后端复核：公益专属奖限制、期望返还率 >100% 拒绝），
 * 本页的前端试算只作即时提示，不替代后端校验。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Gift, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard } from '@/components/ui'
import { parseServerTime } from '@/utils/serverTime'
import { toAdminError } from '@/utils/errorMessage'
import { useRealmStore } from '@/stores/realm'
import {
  fetchBlindboxConfig,
  fetchBlindboxDraws,
  fetchBlindboxStats,
  redeliverBlindboxDraw,
  saveBlindboxConfig,
  saveBlindboxMaster,
  saveBlindboxPrizeValues,
  type BlindboxDrawRow,
  type BlindboxPrize,
  type BlindboxPrizeKind,
  type BlindboxPrizeValues,
  type BlindboxRealmConfig,
  type BlindboxStats,
  type BlindboxTier,
} from '@/api/blindbox'

const PAGE_SIZE = 50

const KIND_OPTIONS: { value: BlindboxPrizeKind; label: string }[] = [
  { value: 'points', label: '积分' },
  { value: 'vitality', label: '活力值（公益专属）' },
  { value: 'access_days', label: '天数奖（资格/订阅）' },
  { value: 'reg_code', label: '注册码' },
  { value: 'sub_code', label: '订阅码' },
  { value: 'whitelist', label: '白名单（公益专属）' },
]
const WELFARE_ONLY: BlindboxPrizeKind[] = ['vitality', 'whitelist']
const FALLBACK_FORBIDDEN: BlindboxPrizeKind[] = ['whitelist', 'access_days']
const AMOUNT_KINDS: BlindboxPrizeKind[] = ['points', 'vitality']
const DAYS_KINDS: BlindboxPrizeKind[] = ['access_days', 'reg_code', 'sub_code']

function kindLabel(kind: string): string {
  return KIND_OPTIONS.find((o) => o.value === kind)?.label ?? kind
}

function prizeLabel(prize: { kind: string; params?: { amount?: number; days?: number } }): string {
  const p = prize.params ?? {}
  switch (prize.kind) {
    case 'points': return `${p.amount ?? 0} 积分`
    case 'vitality': return `${p.amount ?? 0} 活力`
    case 'access_days': return `天数奖 ${p.days ?? 0} 天`
    case 'reg_code': return `注册码 ${p.days ?? 0} 天`
    case 'sub_code': return `订阅码 ${p.days ?? 0} 天`
    case 'whitelist': return '白名单'
    default: return prize.kind
  }
}

const realmStore = useRealmStore()
const selectedRealmId = ref<number | undefined>(undefined)
const selectedRealm = computed(
  () => realmStore.realms.find((r) => r.id === selectedRealmId.value) ?? null,
)
const isFree = computed(() => selectedRealm.value?.access_mode === 'free')

const masterEnabled = ref(false)
const masterSaving = ref(false)
const cfg = ref<BlindboxRealmConfig | null>(null)
const prizeValues = ref<BlindboxPrizeValues | null>(null)
const dayValueRows = ref<{ days: number; value: number }[]>([])
const whitelistRemaining = ref<number | null>(null)
const cfgLoading = ref(false)
const cfgSaving = ref(false)
const valuesSaving = ref(false)

const stats = ref<BlindboxStats | null>(null)

const drawFilters = reactive({
  status: '' as string,
  userId: undefined as number | undefined,
})
const draws = ref<BlindboxDrawRow[]>([])
const drawsTotal = ref(0)
const drawsOffset = ref(0)
const drawsLoading = ref(false)
const redelivering = ref<number | null>(null)

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function fmtPct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

/** 与后端 prize_value 同口径的积分等值试算（只作前端即时提示）。 */
function prizeValue(kind: BlindboxPrizeKind, params: { amount?: number; days?: number }): number {
  const v = prizeValues.value
  if (!v) return 0
  if (kind === 'points') return params.amount ?? 0
  if (kind === 'vitality') return (params.amount ?? 0) * v.vitality_per_point
  if (kind === 'whitelist') return v.whitelist
  const days = params.days ?? 0
  const hit = v.access_day_values[String(days)]
  return hit !== undefined ? hit : days * v.access_day_rate
}

function tierReturnRate(tier: BlindboxTier): number {
  if (!tier.price || tier.price <= 0) return 0
  const totalWeight = tier.prizes.reduce((s, p) => s + (Number(p.weight) || 0), 0)
  if (totalWeight <= 0) return 0
  const expect = tier.prizes.reduce(
    (s, p) => s + ((Number(p.weight) || 0) / totalWeight) * prizeValue(p.kind, p.params),
    0,
  )
  return expect / tier.price
}

async function loadConfig() {
  if (selectedRealmId.value === undefined) return
  cfgLoading.value = true
  try {
    const res = await fetchBlindboxConfig(selectedRealmId.value)
    masterEnabled.value = res.master_enabled
    cfg.value = JSON.parse(JSON.stringify(res.config)) as BlindboxRealmConfig
    prizeValues.value = JSON.parse(JSON.stringify(res.prize_values)) as BlindboxPrizeValues
    dayValueRows.value = Object.entries(res.prize_values.access_day_values)
      .map(([days, value]) => ({ days: Number(days), value: Number(value) }))
      .sort((a, b) => a.days - b.days)
    whitelistRemaining.value = res.whitelist_stock_remaining
  } catch (err) {
    ElMessage.error(toAdminError(err, '盲盒配置加载失败'))
    cfg.value = null
  } finally {
    cfgLoading.value = false
  }
}

async function loadStats() {
  if (selectedRealmId.value === undefined) return
  try {
    stats.value = await fetchBlindboxStats(selectedRealmId.value)
  } catch {
    stats.value = null
  }
}

async function loadDraws() {
  if (selectedRealmId.value === undefined) return
  drawsLoading.value = true
  try {
    const res = await fetchBlindboxDraws({
      realm_id: selectedRealmId.value,
      status: drawFilters.status || undefined,
      user_id: drawFilters.userId,
      limit: PAGE_SIZE,
      offset: drawsOffset.value,
    })
    draws.value = res.items
    drawsTotal.value = res.total
  } catch (err) {
    ElMessage.error(toAdminError(err, '抽盒记录加载失败'))
    draws.value = []
    drawsTotal.value = 0
  } finally {
    drawsLoading.value = false
  }
}

async function reloadAll() {
  await loadConfig()
  await loadStats()
  drawsOffset.value = 0
  await loadDraws()
}

async function onMasterChange(value: boolean) {
  masterSaving.value = true
  try {
    const res = await saveBlindboxMaster(value)
    masterEnabled.value = res.master_enabled
    ElMessage.success(value ? '盲盒总开关已打开' : '盲盒总开关已关闭')
    await loadStats()
  } catch (err) {
    masterEnabled.value = !value
    ElMessage.error(toAdminError(err, '总开关保存失败'))
  } finally {
    masterSaving.value = false
  }
}

async function saveCfg() {
  if (!cfg.value || selectedRealmId.value === undefined) return
  cfgSaving.value = true
  try {
    const res = await saveBlindboxConfig(selectedRealmId.value, cfg.value)
    cfg.value = JSON.parse(JSON.stringify(res.config)) as BlindboxRealmConfig
    ElMessage.success('该服盲盒配置已保存')
    await loadStats()
  } catch (err) {
    ElMessage.error(toAdminError(err, '配置保存失败'))
  } finally {
    cfgSaving.value = false
  }
}

async function saveValues() {
  if (!prizeValues.value) return
  valuesSaving.value = true
  try {
    const values: BlindboxPrizeValues = {
      ...prizeValues.value,
      access_day_values: Object.fromEntries(
        dayValueRows.value
          .filter((r) => Number.isFinite(r.days) && r.days > 0)
          .map((r) => [String(r.days), Number(r.value) || 0]),
      ),
    }
    const res = await saveBlindboxPrizeValues(values)
    prizeValues.value = res.prize_values
    ElMessage.success('估值表已保存')
  } catch (err) {
    ElMessage.error(toAdminError(err, '估值表保存失败'))
  } finally {
    valuesSaving.value = false
  }
}

function newPrize(): BlindboxPrize {
  return { kind: 'points', weight: 1, params: { amount: 10 } }
}

function addTier() {
  if (!cfg.value) return
  const n = cfg.value.tiers.length + 1
  cfg.value.tiers.push({
    key: `tier${n}`,
    name: `新档位 ${n}`,
    price: 100,
    enabled: true,
    sort: n,
    fallback_prize: { kind: 'points', weight: 1, params: { amount: 10 } },
    prizes: [newPrize()],
  })
}

function removeTier(idx: number) {
  cfg.value?.tiers.splice(idx, 1)
}

function addPrize(tier: BlindboxTier) {
  tier.prizes.push(newPrize())
}

function removePrize(tier: BlindboxTier, idx: number) {
  tier.prizes.splice(idx, 1)
}

function searchDraws() {
  drawsOffset.value = 0
  void loadDraws()
}

function prevDrawsPage() {
  if (drawsOffset.value <= 0) return
  drawsOffset.value = Math.max(0, drawsOffset.value - PAGE_SIZE)
  void loadDraws()
}

function nextDrawsPage() {
  if (drawsOffset.value + PAGE_SIZE >= drawsTotal.value) return
  drawsOffset.value += PAGE_SIZE
  void loadDraws()
}

async function redeliver(row: BlindboxDrawRow) {
  redelivering.value = row.id
  try {
    await redeliverBlindboxDraw(row.id)
    ElMessage.success(`记录 #${row.id} 已补发`)
    await loadDraws()
    await loadStats()
  } catch (err) {
    ElMessage.error(toAdminError(err, '补发失败'))
  } finally {
    redelivering.value = null
  }
}

function statusTag(status: string): string {
  if (status === 'delivered') return '已发放'
  if (status === 'delivery_failed') return '已扣分未发放'
  if (status === 'failed') return '失败（未扣分）'
  return status
}

watch(selectedRealmId, () => {
  void reloadAll()
})

onMounted(async () => {
  if (!realmStore.loaded) {
    await realmStore.load()
  }
  selectedRealmId.value =
    realmStore.activeId ?? realmStore.realms[0]?.id
})
</script>

<template>
  <div class="blindbox-page">
    <PageHeader
      eyebrow="经营"
      title="盲盒"
      description="双服通用：每个服一套档位和奖池，分别记账。概率购买前全公布；活力值、白名单是公益专属奖，付费服奖池不能选。保存时后端会按估值表复核期望返还率，超过 100% 直接拒绝。"
    />

    <SectionCard title="总开关与选服">
      <div class="top-grid">
        <label class="field">
          <span>盲盒总开关（全站）</span>
          <el-switch
            :model-value="masterEnabled"
            :loading="masterSaving"
            @change="onMasterChange"
          />
        </label>
        <label class="field grow">
          <span>当前编辑的服</span>
          <el-select v-model="selectedRealmId" placeholder="选择服">
            <el-option
              v-for="r in realmStore.realms"
              :key="r.id"
              :label="`${r.name}（${r.access_mode === 'free' ? '公益服' : '付费服'}）`"
              :value="r.id"
            />
          </el-select>
        </label>
        <div class="field">
          <span>服类型</span>
          <div class="static-val">
            {{ isFree ? '公益服：全部奖品可用' : '付费服：活力值 / 白名单不可选' }}
          </div>
        </div>
      </div>
      <el-alert
        v-if="!isFree && selectedRealm"
        type="warning"
        :closable="false"
        show-icon
        title="当前是付费服：奖池里选了活力值或白名单，后端会拒绝保存；天数奖给有在期订阅的用户顺延订阅，没订阅的改发该档兜底奖。"
      />
    </SectionCard>

    <SectionCard title="该服配置">
      <div v-if="cfg" v-loading="cfgLoading">
        <div class="cfg-grid">
          <label class="field">
            <span>该服盲盒开启</span>
            <el-switch v-model="cfg.enabled" />
          </label>
          <label class="field">
            <span>活动开始（ISO 时间，可空）</span>
            <el-input v-model="cfg.starts_at" placeholder="2026-11-11T20:00:00" />
          </label>
          <label class="field">
            <span>活动结束（ISO 时间，可空）</span>
            <el-input v-model="cfg.ends_at" placeholder="2026-11-18T23:59:59" />
          </label>
          <label class="field">
            <span>每人每日消费上限（积分，0 = 不限）</span>
            <el-input-number v-model="cfg.daily_spend_cap" :min="0" controls-position="right" />
          </label>
          <label class="field">
            <span>天数奖每日预算（天，0 = 不限）</span>
            <el-input-number v-model="cfg.access_days_daily_budget" :min="0" controls-position="right" />
          </label>
          <label class="field">
            <span>白名单库存总量（0 = 不发白名单）</span>
            <el-input-number v-model="cfg.whitelist_stock_total" :min="0" controls-position="right" />
          </label>
          <div class="field">
            <span>白名单剩余库存</span>
            <div class="static-val">
              {{ whitelistRemaining === null ? '未设总量' : `${whitelistRemaining} 个` }}
            </div>
          </div>
        </div>

        <div class="tiers-head">
          <h4>档位与奖池</h4>
          <el-button size="small" @click="addTier">
            <Plus :size="14" /> 加一档
          </el-button>
        </div>

        <div v-for="(tier, ti) in cfg.tiers" :key="ti" class="tier-block">
          <div class="tier-head">
            <el-input v-model="tier.name" placeholder="档位名" class="w-name" />
            <el-input v-model="tier.key" placeholder="key（英文唯一）" class="w-key" />
            <label class="mini-field">
              售价（积分）
              <el-input-number v-model="tier.price" :min="1" controls-position="right" />
            </label>
            <label class="mini-field">
              排序
              <el-input-number v-model="tier.sort" controls-position="right" />
            </label>
            <label class="mini-field">
              上架
              <el-switch v-model="tier.enabled" />
            </label>
            <span
              class="rate"
              :class="{ warn: tierReturnRate(tier) > 0.85, bad: tierReturnRate(tier) > 1 }"
            >
              期望返还 {{ fmtPct(tierReturnRate(tier)) }}
              <template v-if="tier.expected_return_rate !== undefined">
                （上次保存时后端算 {{ fmtPct(tier.expected_return_rate) }}）
              </template>
            </span>
            <el-button size="small" type="danger" plain @click="removeTier(ti)">
              <Trash2 :size="14" /> 删除档位
            </el-button>
          </div>
          <p v-if="tierReturnRate(tier) > 0.85 && tierReturnRate(tier) <= 1" class="rate-note warn">
            期望返还率超过 85%，请确认奖池没配错。
          </p>
          <p v-if="tierReturnRate(tier) > 1" class="rate-note bad">
            期望返还率超过 100%，后端会拒绝保存。
          </p>

          <div class="prize-rows">
            <div v-for="(prize, pi) in tier.prizes" :key="pi" class="prize-row">
              <el-select v-model="prize.kind" class="w-kind">
                <el-option
                  v-for="o in KIND_OPTIONS"
                  :key="o.value"
                  :label="o.label"
                  :value="o.value"
                  :disabled="!isFree && WELFARE_ONLY.includes(o.value)"
                />
              </el-select>
              <el-input-number
                v-if="AMOUNT_KINDS.includes(prize.kind)"
                v-model="prize.params.amount"
                :min="1"
                placeholder="数量"
                controls-position="right"
              />
              <el-input-number
                v-else-if="DAYS_KINDS.includes(prize.kind)"
                v-model="prize.params.days"
                :min="1"
                placeholder="天数"
                controls-position="right"
              />
              <span v-else class="static-val">—</span>
              <label class="mini-field">
                权重
                <el-input-number v-model="prize.weight" :min="0" :step="1" controls-position="right" />
              </label>
              <span class="dim">
                估值 {{ prizeValue(prize.kind, prize.params) }} 积分
              </span>
              <el-button size="small" plain @click="removePrize(tier, pi)">
                <Trash2 :size="14" />
              </el-button>
            </div>
            <el-button size="small" @click="addPrize(tier)">
              <Plus :size="14" /> 加奖品
            </el-button>
          </div>

          <div class="fallback-row">
            <span class="dim">兜底奖（库存/预算不足或付费服无订阅时改发，不能是白名单/天数奖）：</span>
            <el-select v-model="tier.fallback_prize.kind" class="w-kind">
              <el-option
                v-for="o in KIND_OPTIONS"
                :key="o.value"
                :label="o.label"
                :value="o.value"
                :disabled="(!isFree && WELFARE_ONLY.includes(o.value)) || FALLBACK_FORBIDDEN.includes(o.value)"
              />
            </el-select>
            <el-input-number
              v-if="AMOUNT_KINDS.includes(tier.fallback_prize.kind)"
              v-model="tier.fallback_prize.params.amount"
              :min="1"
              placeholder="数量"
              controls-position="right"
            />
            <el-input-number
              v-else-if="DAYS_KINDS.includes(tier.fallback_prize.kind)"
              v-model="tier.fallback_prize.params.days"
              :min="1"
              placeholder="天数"
              controls-position="right"
            />
          </div>
        </div>

        <EmptyState
          v-if="!cfg.tiers.length"
          compact
          :icon="Gift"
          title="还没有档位"
          description="点「加一档」配第一个盒子；没配档位时用户端不会出现盲盒。"
        />

        <div class="actions">
          <el-button type="primary" :loading="cfgSaving" @click="saveCfg">
            保存该服配置
          </el-button>
        </div>
      </div>
      <div v-else class="dim">配置加载中或加载失败，换个服或刷新页面再试。</div>
    </SectionCard>

    <SectionCard title="奖品估值表（全站共用，只影响返还率试算与统计折算）">
      <div v-if="prizeValues" class="cfg-grid">
        <label class="field">
          <span>活力 1 点 = 多少积分</span>
          <el-input-number v-model="prizeValues.vitality_per_point" :min="0" controls-position="right" />
        </label>
        <label class="field">
          <span>天数奖每其它天数折算（积分/天）</span>
          <el-input-number v-model="prizeValues.access_day_rate" :min="0" controls-position="right" />
        </label>
        <label class="field">
          <span>白名单记账值（积分）</span>
          <el-input-number v-model="prizeValues.whitelist" :min="0" controls-position="right" />
        </label>
      </div>
      <div v-if="prizeValues" class="day-values">
        <div v-for="(row, i) in dayValueRows" :key="i" class="day-row">
          <el-input-number v-model="row.days" :min="1" controls-position="right" />
          <span class="dim">天 =</span>
          <el-input-number v-model="row.value" :min="0" controls-position="right" />
          <span class="dim">积分</span>
          <el-button size="small" plain @click="dayValueRows.splice(i, 1)">
            <Trash2 :size="14" />
          </el-button>
        </div>
        <el-button size="small" @click="dayValueRows.push({ days: 30, value: 450 })">
          <Plus :size="14" /> 加一行
        </el-button>
      </div>
      <div v-if="prizeValues" class="actions">
        <el-button type="primary" :loading="valuesSaving" @click="saveValues">
          保存估值表
        </el-button>
      </div>
    </SectionCard>

    <SectionCard title="抽盒统计（该服，已发放口径）">
      <div class="stat-grid">
        <div>
          <div class="stat-num">{{ stats?.draws ?? '—' }}</div>
          <div class="dim">总抽数</div>
        </div>
        <div>
          <div class="stat-num">{{ stats?.revenue ?? '—' }}</div>
          <div class="dim">流水（积分）</div>
        </div>
        <div>
          <div class="stat-num">{{ stats?.payout_value ?? '—' }}</div>
          <div class="dim">派奖估值（积分）</div>
        </div>
        <div>
          <div class="stat-num">
            {{ stats ? fmtPct(stats.actual_return_rate) : '—' }}
          </div>
          <div class="dim">实际返还率</div>
        </div>
        <div>
          <div class="stat-num">{{ stats?.failed_draws ?? '—' }}</div>
          <div class="dim">失败/未发放记录</div>
        </div>
      </div>
      <div v-if="stats && Object.keys(stats.by_kind).length" class="by-kind">
        <span v-for="(v, kind) in stats.by_kind" :key="kind" class="kind-chip">
          {{ kindLabel(kind) }}：{{ v.draws }} 次 · {{ v.value }} 积分
        </span>
      </div>
    </SectionCard>

    <SectionCard :title="`抽盒记录（共 ${drawsTotal} 条）`">
      <div class="filter-grid">
        <el-select v-model="drawFilters.status" placeholder="全部状态" clearable>
          <el-option label="已发放" value="delivered" />
          <el-option label="已扣分未发放" value="delivery_failed" />
          <el-option label="失败（未扣分）" value="failed" />
        </el-select>
        <el-input-number
          v-model="drawFilters.userId"
          placeholder="按用户 ID"
          :min="1"
          controls-position="right"
          class="user-id-input"
        />
        <div class="filter-actions">
          <el-button type="primary" :loading="drawsLoading" @click="searchDraws">
            <Search :size="14" /> 查询
          </el-button>
          <el-button @click="reloadAll">
            <RefreshCw :size="14" /> 刷新整页
          </el-button>
        </div>
      </div>
      <el-table v-if="draws.length" :data="draws" size="small" v-loading="drawsLoading">
        <el-table-column label="ID" width="70">
          <template #default="{ row }">{{ row.id }}</template>
        </el-table-column>
        <el-table-column label="用户" width="80">
          <template #default="{ row }">#{{ row.user_id }}</template>
        </el-table-column>
        <el-table-column label="档位" min-width="110">
          <template #default="{ row }">{{ row.tier_name }}</template>
        </el-table-column>
        <el-table-column label="售价" width="70">
          <template #default="{ row }">{{ row.price }}</template>
        </el-table-column>
        <el-table-column label="奖品" min-width="170">
          <template #default="{ row }">
            {{ prizeLabel(row.prize) }}
            <span v-if="row.prize.fallback_from" class="dim">
              （由{{ kindLabel(row.prize.fallback_from) }}兜底改发）
            </span>
            <div v-if="row.prize.code" class="mono">{{ row.prize.code }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            {{ statusTag(row.status) }}
            <div v-if="row.error" class="dim err-text">{{ row.error }}</div>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            <span class="mono">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'delivery_failed' && row.charged"
              size="small"
              type="primary"
              :loading="redelivering === row.id"
              @click="redeliver(row)"
            >
              补发
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else-if="!drawsLoading"
        compact
        :icon="Gift"
        title="还没有抽盒记录"
        description="用户抽盒后，记录会出现在这里；已扣分未发放的可以补发。"
      />
      <div class="pager">
        <el-button :disabled="drawsOffset <= 0 || drawsLoading" @click="prevDrawsPage">
          上一页
        </el-button>
        <span class="dim">
          第 {{ Math.floor(drawsOffset / PAGE_SIZE) + 1 }} 页 · 本页 {{ draws.length }} 条
        </span>
        <el-button
          :disabled="drawsOffset + PAGE_SIZE >= drawsTotal || drawsLoading"
          @click="nextDrawsPage"
        >
          下一页
        </el-button>
      </div>
    </SectionCard>
  </div>
</template>

<style scoped>
.blindbox-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.top-grid,
.cfg-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 16px;
  align-items: end;
  margin-bottom: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--au-text-2);
}

.field.grow {
  min-width: 200px;
}

.static-val {
  font-size: 13px;
  color: var(--au-text);
  padding: 6px 0;
}

.dim {
  color: var(--au-text-3);
  font-size: 12px;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.tiers-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
}

.tiers-head h4 {
  margin: 0;
  font-size: 14px;
  color: var(--au-text);
}

.tier-block {
  border: 1px solid var(--au-border, #e5e0d5);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
}

.tier-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
}

.w-name {
  width: 160px;
}

.w-key {
  width: 140px;
}

.w-kind {
  width: 210px;
}

.mini-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--au-text-2);
}

.rate {
  font-size: 13px;
  font-weight: 600;
  color: var(--au-text);
}

.rate.warn,
.rate-note.warn {
  color: #b45309;
}

.rate.bad,
.rate-note.bad {
  color: #b91c1c;
}

.rate-note {
  margin: 6px 0 0;
  font-size: 12px;
}

.prize-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.prize-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
}

.fallback-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 12px;
}

.actions {
  display: flex;
  margin-top: 12px;
}

.day-values {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}

.day-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 16px;
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: var(--au-text);
  line-height: 1.2;
}

.by-kind {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.kind-chip {
  font-size: 12px;
  border: 1px solid var(--au-border, #e5e0d5);
  border-radius: 999px;
  padding: 2px 10px;
  color: var(--au-text-2);
}

.filter-grid {
  display: grid;
  grid-template-columns: 200px 200px 1fr;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}

.filter-actions {
  display: flex;
  gap: 8px;
}

.err-text {
  word-break: break-all;
}

.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}
</style>
