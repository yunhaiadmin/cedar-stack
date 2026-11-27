<script setup lang="ts">
/**
 * 系统设置
 *
 * v2.4.0 注册策略（开放 / 关闭 + 关闭提示文案）与经济配置分组
 * v2.5.2 付费墙分组（要求有效订阅 + 拦截提示文案）
 * v2.6.0 下载与设备风控分组（下载开关 / 设备上限 / 自动踢人 / 日志保留）
 * v2.19.0 **外部服务能力中心**：代理 / 邮件与模板 / Telegram / IP 归属地
 * （人机验证已于 2026-11-15 整体摘除，能力卡随之后端下线）
 *
 * v2.42.10（Phase 5·危险操作独立 tab）：页面改成三个页签——「能力与服务」「运营参数」
 * 「**危险操作**」。前两个装的都是日常要改的东西，第三个装的是**会立刻改变线上数据、
 * 且大多不能撤销**的操作（手动恢复配额熔断器 / 清理登录与安全日志）。
 * 把它们从仪表盘、登录日志页面里搬到这里，是为了让「不可撤销」这件事
 * 在**一个地方**说清楚：先备份、再确认、再执行。原来两处的按钮改成深链指过来
 * （如 `/settings?tab=danger&op=logs`），**执行逻辑只在这里一份**。
 *
 * 能力中心的口径：**项目只提供能力，凭据一律由管理员自己填**。字段表由后端下发，
 * 这里只按类型渲染控件，所以后端加字段不需要改这个页面，也不会出现「界面漏了字段」。
 * 密钥字段后端只回 ******，留空即保持原值不变。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  Coins, Globe, KeyRound, Layers, Mail, MapPin, Network, Palette, RefreshCw, Save,
  ShieldCheck, Send, Sparkles, TicketCheck, UserPlus, Wallet, Lock, Zap,
  History, TriangleAlert, SlidersHorizontal, PlugZap, Rocket, HardDrive, Gauge,
  Download, Cpu, MessageSquareText, PanelBottom, ScrollText, Bot,
} from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard } from '@/components/ui'
import { useBreakpoint } from '@/composables/useBreakpoint'
import {
  fetchRegistrationSettings, updateRegistrationSettings,
  fetchSoftdecodeConfig, updateSoftdecodeConfig, type SoftdecodeConfig,
  fetchDanmakuConfig, updateDanmakuConfig, type DanmakuConfig,
  fetchDanmuApiLines, updateDanmuApiLines, type DanmuApiLine,
  fetchLoginLockoutConfig, updateLoginLockoutConfig, type LoginLockoutConfig,
  fetchSiteDrawerConfig, updateSiteDrawerConfig,
  type SiteDrawerConfig, type SiteDrawerConfigResponse,
  fetchPrivacyPolicy, updatePrivacyPolicy, type PrivacyPolicyResponse,
} from '@/api/admin'
import { useAuthStore } from '@/stores/auth'
import { useQueryFilter } from '@/composables/useQueryFilter'
import { useDangerOps } from '@/composables/useDangerOps'
import { fetchEconomySettings, updateEconomySettings, type EconomySettings } from '@/api/economy'
import { buildEconomyPayload } from '@/utils/buildEconomyPayload'
import {
  fetchHotlinkConfig, updateHotlinkConfig, type HotlinkConfigPayload,
  type HotlinkConfigGet, fetchItemsDedupCacheConfig, updateItemsDedupCacheConfig,
  type ItemsDedupCacheConfigPayload, type ItemsDedupCacheConfig,
  fetchTransferConfig, updateTransferConfig, type TransferConfig,
  fetchMountServingConfig, updateMountServingConfig,
  type MountServingConfigPayload, type MountServingConfig,
  fetchDriveApiConfig, updateDriveApiConfig, type DriveApiConfig,
  fetchDeliveryPlanConfig, updateDeliveryPlanConfig, type DeliveryPlanConfig,
} from '@/api/economy'
import {
  fetchCapabilities, fetchCapability, saveCapability, testCapability,
  type CapabilityCard, type CapabilityField, type CapabilityTestResult,
} from '@/api/capabilities'
import {
  fetchLicenseStatus, saveLicenseConfig, verifyLicenseNow,
  type LicenseStatus,
} from '@/api/license'
import {
  fetchInstallAppConfig, updateInstallAppConfig,
  type InstallAppConfigResponse, type InstallAppPlatformConfig,
} from '@/api/installApp'
import {
  fetchWelfareConfig, saveWelfareConfig,
} from '@/api/welfare'
import NoticePanel from '@/components/NoticePanel.vue'

const route = useRoute()
const auth = useAuthStore()
/** 手机上能力配置抽屉铺满整屏 */
const { isPhone } = useBreakpoint()
const capDrawerSize = computed(() => (isPhone.value ? '100%' : '520px'))

/** ==================== 页签（Phase 5） ==================== */

const TAB_KEYS = ['capabilities', 'params', 'danger']
const tab = ref<string>('capabilities')
// 深链：/settings?tab=danger（仪表盘 / 命令面板 / 其它页面的「去危险操作」都指这里）
useQueryFilter(tab, 'tab')
watch(
  tab,
  (value) => {
    // 非法值回默认页签：宁可回到第一页，也不要停在一个不存在的页签上（空白页）
    if (!TAB_KEYS.includes(value)) tab.value = 'capabilities'
  },
  { immediate: true },
)

type FieldType = 'bool' | 'int' | 'str' | 'secret'

interface Field {
  key: string
  label: string
  type: FieldType
  hint?: string
  suffix?: string
  /** 有值时渲染成下拉（type 仍是 str，保存走同一串行化分支） */
  choices?: Array<{ value: string; label: string }>
  /**
   * 高级调优项：收进卡内「高级调优」折叠区，默认收起。
   * 只放"调了也不影响主流程"的参数（窗口/超时/分块大小/惩罚幅度等），
   * 总开关与日常要改的核心值永远留在外面。
   */
  advanced?: boolean
}

interface Group {
  id: string
  title: string
  desc: string
  icon: unknown
  fields: Field[]
}

// 开关从简第二批（2026-11-23）：求片自动批准/订阅购买/QQ 钱包/钱包总开关/
// 手填汇率覆盖/退款回钱包/余额自动续订 7 个 bool 已摘面板，改走文件配置
// （env，见 backend/integrations/store.py PANEL_REMOVED_SWITCHES 第二批）。
const GROUPS: Group[] = [
  {
    id: 'exchange',
    title: '兑换码 · 求片',
    desc: '卡码默认套餐与求片额度（日限 + 付费月限都在这一张卡）',
    icon: TicketCheck,
    fields: [
      {
        key: 'code_default_plan_id',
        label: '卡码默认套餐',
        type: 'int',
        suffix: '套餐 ID',
        hint: '卡码授予天数时优先用的套餐 ID；0=不指定，按服自动推导（r40 补后台入口）',
        advanced: true,
      },
      {
        key: 'media_seek_daily_limit',
        label: '每日求片上限',
        type: 'int',
        suffix: '条/天',
        hint: '用户端提交求片时的硬性限额，未设置时默认 5 条',
      },
      {
        key: 'media_seek_fulfill_interval_minutes',
        label: '到货自动核对间隔',
        type: 'int',
        suffix: '分钟',
        hint: '后台每隔几分钟把处理中的求片与片库对一遍，对上就自动置已入库并通知求片人，默认 5 分钟',
      },
      {
        key: 'media_seek_daily_vote_limit',
        label: '每日附议上限',
        type: 'int',
        suffix: '次/天',
        hint: '用户每天附议次数上限（按当日现存附议行计，取消即释放名额），未设置时默认 20 次',
      },
      {
        key: 'paid_request_monthly',
        label: '付费服每月求片上限',
        type: 'int',
        suffix: '条/月',
        hint: '付费用户每月最多求片条数，未设置时默认 10 条',
      },
    ],
  },
  {
    id: 'payment',
    title: '充值 · 支付',
    desc: '易支付兼容网关；留空的密钥项表示保持不变',
    icon: Wallet,
    fields: [
      { key: 'payment_gateway_url', label: '网关地址', type: 'str', hint: '例如 https://pay.example.com/' },
      { key: 'payment_partner_id', label: '商户 ID', type: 'str', hint: '易支付后台「商户信息」里的商户号' },
      { key: 'payment_partner_key', label: '商户密钥', type: 'secret', hint: '已配置时显示为 ******，留空不修改' },
      { key: 'site_url', label: '站点地址', type: 'str', hint: '用于支付回调，必须是外网可访问地址' },
      { key: 'max_pending_orders_per_user', label: '待支付订单上限', type: 'int', suffix: '单/人', hint: '每人同时未支付的订单上限，默认 5，r40 补后台入口' },
      // 钱包（批 1，2026-11-14）：余额/流水在「钱包管理」页，这里只放配置；
      // 无库行时按代码默认生效（开/$10/0/5%/不限/分档·门槛$50·二档1%/$0.30/开/关，见钱包管理页）
      { key: 'wallet_min_topup_amount', label: '最低充值额（美分）', type: 'int', suffix: '美分', hint: '默认 100 ＝ $1，在线充值（批 3）用' },
      { key: 'wallet_topup_fee_pct', label: '充值手续费比例（%）', type: 'int', suffix: '%', hint: '默认 0' },
      { key: 'wallet_topup_cny_rate', label: '充值汇率（人民币/美元）', type: 'str', hint: '手填价：自动抓市场价失败时的兜底；开启下方强制覆盖后以此为准。默认 7.2' },
      { key: 'wallet_topup_rate_buffer_pct', label: '汇率缓冲（%）', type: 'int', suffix: '%', hint: '生效汇率 ＝ 市场价 ×（1＋缓冲%），默认 2' },
      { key: 'wallet_topup_max_amount', label: '单笔充值上限（美分）', type: 'int', suffix: '美分', hint: '默认 7000 ＝ $70，0 为不限；法币单笔上限，下单接口同样拦截' },
      { key: 'wallet_invite_rebate_percent', label: '邀请返利比例（%）', type: 'int', suffix: '%', hint: '默认 5，好友充值到账后返给邀请人（批 2 计提）' },
      { key: 'wallet_invite_rebate_cap', label: '单人返利上限（美分）', type: 'int', suffix: '美分', hint: '默认 0 ＝ 不限' },
      {
        key: 'wallet_invite_rebate_mode',
        label: '返利模式',
        type: 'str',
        choices: [
          { value: 'tiered', label: '分档（tiered）' },
          { value: 'flat', label: '统一比例（flat）' },
        ],
        hint: '默认分档：每位好友累计付费在分档门槛内按返利比例、超出部分按二档比例；统一比例 ＝ 全程按返利比例',
      },
      { key: 'wallet_invite_rebate_tier_threshold', label: '分档门槛（美分）', type: 'int', suffix: '美分', hint: '默认 5000 ＝ $50，按每位好友累计付费计，仅分档模式用' },
      { key: 'wallet_invite_rebate_tier_percent', label: '二档比例（%）', type: 'int', suffix: '%', hint: '默认 1，超出分档门槛的部分按此比例返，仅分档模式用' },
      { key: 'traffic_balance_price_per_100gb', label: '余额购流量单价（美分/100GB）', type: 'int', suffix: '美分', hint: '默认 30 ＝ $0.30/100GB（批 2 启用）' },
    ],
  },
  {
    id: 'paywall',
    title: '付费墙（会员门禁）',
    desc: '开启后，非会员无法播放与下载；管理员账号始终放行',
    icon: Lock,
    fields: [
      {
        key: 'subscription_required',
        label: '要求有效订阅',
        type: 'bool',
        hint: '关闭则所有人均可直接播放',
      },
      {
        key: 'subscription_gate_message',
        label: '拦截提示文案',
        type: 'str',
        hint: '留空使用默认：需要有效的会员订阅才能播放，请先开通会员',
      },
    ],
  },
  {
    id: 'invitation',
    title: '邀请奖励',
    desc: '邀请成功后给邀请人的推广奖励（会员天数）',
    icon: UserPlus,
    fields: [
      // 被邀请人积分奖励与消费返利已随货币下线（2026-11-13）；邀请只剩
      // 推广奖励（会员天数）与邀请关系本身。
      {
        key: 'promotion_reward_enabled',
        label: '推广奖励',
        type: 'bool',
        hint: '邀请成功后给邀请人发一笔会员天数；默认开启。天数为 0 时即使开启也不会发',
      },
      {
        key: 'promotion_reward_type',
        label: '推广奖励类型',
        type: 'str',
        choices: [
          { value: 'days', label: '有效期（天）' },
        ],
        hint: '选「有效期」时给邀请人叠加会员天数，归属当前服',
      },
      { key: 'promotion_reward_days', label: '推广奖励·有效期天数', type: 'int', suffix: '天', hint: '类型为「有效期」时生效；0 = 不发', advanced: true },
    ],
  },
  {
    id: 'stream_accel',
    title: '流媒体加速',
    desc: '一键开启：强制域名访问（防 IP 直连） + 自动信任 CF 真实 IP + 播放地址统一用加速域名',
    icon: Rocket,
    fields: [
      { key: 'stream_accel_enabled', label: '启用加速', type: 'bool', hint: '打开后只能用加速域名访问，IP 直连会被拒绝。默认开启' },
      {
        key: 'stream_accel_domain',
        label: '加速域名',
        type: 'str',
        hint: '例如 cdn.example.com，不带 https://；保存后最长 60 秒生效；开启后只能用该域名访问',
      },
    ],
  },
  {
    id: 'series_merge',
    title: '同剧合并',
    desc: '同部剧因标题虚词差异被拆成多条时自动合并（TMDB 编号 + 宽松标题识别，有生死守卫）；存量无 TMDB 编号的剧集也会尝试宽松重识别',
    icon: Layers,
    fields: [
      { key: 'series_merge_interval_sec', label: '合并间隔', type: 'int', suffix: '秒', hint: '最短 3600 秒；扫描完成后会自动提前跑一轮' },
      { key: 'series_merge_batch_limit', label: '每轮组数上限', type: 'int', hint: '单轮最多处理的合并组数' },
    ],
  },
]

/**
 * 分组归类：运营参数越加越多（邀请 / 支付 / 预热…），平铺卡片很难找。
 * 按「管什么」分成三段，目录与正文用同一份归类；注册策略固定放在第一段开头。
 */
interface GroupSection {
  id: string
  title: string
  groups: Group[]
}
const SECTION_DEFS: Array<{ id: string; title: string; groups: string[] }> = [
  { id: 'growth', title: '注册与福利', groups: ['invitation', 'exchange'] },
  { id: 'commerce', title: '付费与支付', groups: ['payment', 'paywall'] },
  { id: 'playback', title: '播放与风控', groups: ['stream_accel'] },
  { id: 'bot', title: '机器人', groups: [] },
  { id: 'library', title: '媒体库', groups: ['series_merge'] },
  { id: 'site', title: '站点与官网', groups: [] },
]
const SECTIONS: GroupSection[] = (() => {
  const used = new Set<string>()
  const sections = SECTION_DEFS.map((s) => ({
    id: s.id,
    title: s.title,
    groups: s.groups
      .map((id) => GROUPS.find((g) => g.id === id))
      .filter((g): g is Group => {
        if (g) used.add(g.id)
        return Boolean(g)
      }),
  }))
  // 新加的分组忘了归类时兜底放到「其它」，不至于从页面上消失
  const rest = GROUPS.filter((g) => !used.has(g.id))
  if (rest.length) sections.push({ id: 'other', title: '其它', groups: rest })
  return sections
})()

const loading = ref(true)
/** 运营参数读取失败：显示错误态，避免管理员对着空表单点保存 */
const paramsError = ref(false)
/** 分组目录横向滚动归零：移动端是横滑导航，数据重载后回到起点，避免首个标签停在被裁的位置 */
const groupNavRef = ref<HTMLElement | null>(null)
function resetGroupNavScroll() {
  nextTick(() => { if (groupNavRef.value) groupNavRef.value.scrollLeft = 0 })
}
const settings = reactive<EconomySettings>({})
const original = ref<EconomySettings>({})

/** 注册策略 */
const reg = reactive({
  mode: 'open',
  message: '',
  ratelimitMax: 5,
  ratelimitWindow: 3600,
})
const regFormRef = ref<FormInstance | FormInstance[]>()

/**
 * 注册表单位于 SECTIONS 的 v-for 模板内，模板 ref 会被收集成数组，
 * regFormRef.value 不一定是表单实例，取用前必须先解包。
 * （曾直接 regFormRef.value?.validate() 对数组调不存在的方法抛
 * TypeError，又被 catch 静默吞掉，注册策略保存每次必现失败。）
 */
function regFormInstance(): FormInstance | undefined {
  const value = regFormRef.value
  return Array.isArray(value) ? value[0] : value
}

/**
 * 注册策略的"干净"快照：load() 成功后同步，用于全局脏检查。
 */
const regOriginal = ref({
  mode: 'open',
  message: '',
  ratelimitMax: 5,
  ratelimitWindow: 3600,
})
const regDirty = computed(() =>
  reg.mode !== regOriginal.value.mode
  || reg.message !== regOriginal.value.message
  || reg.ratelimitMax !== regOriginal.value.ratelimitMax
  || reg.ratelimitWindow !== regOriginal.value.ratelimitWindow,
)
function snapshotReg() {
  regOriginal.value = {
    mode: reg.mode,
    message: reg.message,
    ratelimitMax: reg.ratelimitMax,
    ratelimitWindow: reg.ratelimitWindow,
  }
}
const regRules: FormRules = {
  ratelimitMax: [
    { type: 'number', min: 1, max: 10000, message: '次数需在 1–10000 之间', trigger: 'blur' },
  ],
  ratelimitWindow: [
    { type: 'number', min: 60, max: 86400, message: '窗口需在 60–86400 秒之间', trigger: 'blur' },
  ],
  message: [{ max: 200, message: '提示最多 200 个字', trigger: 'blur' }],
}

const regModeHint = computed(() => {
  const map: Record<string, string> = {
    open: '任何人都可以直接注册',
    closed: '关闭注册入口，仅显示提示文案',
  }
  return map[reg.mode] || ''
})

/**
 * 字段是否被后端接收：GET /economy/settings 按白名单下发全部可写键，
 * 不在返回里的键 PUT 时会被静默丢弃（保存提示成功、值却永远回不来）。
 * 这类字段置灰并说明，且不进保存载荷。读取失败（original 为空）时不做判断。
 */
function isSupported(key: string): boolean {
  const keys = Object.keys(original.value)
  return !keys.length || Object.prototype.hasOwnProperty.call(original.value, key)
}

// ==================== 外部服务能力中心 ====================

const CAP_ICONS: Record<string, unknown> = {
  proxy: Network, mail: Mail,
  telegram: Send, ai: Sparkles, geoip: MapPin, branding: Palette,
}

/** 少数能力测试需要额外输入（收件地址 / 要查的 IP / 探测地址） */
const TEST_INPUTS: Record<string, { key: string; label: string; placeholder: string }> = {
  mail: { key: 'to', label: '收件地址', placeholder: '留空则发给发件地址' },
  geoip: { key: 'ip', label: '要查的 IP', placeholder: '默认 8.8.8.8' },
  proxy: { key: 'target', label: '探测地址', placeholder: '默认 https://www.gstatic.com/generate_204' },
}

const capabilities = ref<CapabilityCard[]>([])
const capsLoading = ref(true)
const capsError = ref(false)
const capGroups = computed(() => {
  const groups: { name: string; items: CapabilityCard[] }[] = []
  // 防御：API 返回异常时 capabilities 可能不是数组，避免整个页签白屏
  const list = Array.isArray(capabilities.value) ? capabilities.value : []
  for (const card of list) {
    if (!card || typeof card !== 'object') continue
    const groupName = (card as CapabilityCard).group || '其他'
    const found = groups.find((g) => g.name === groupName)
    if (found) found.items.push(card as CapabilityCard)
    else groups.push({ name: groupName, items: [card as CapabilityCard] })
  }
  return groups
})

const drawerOpen = ref(false)
const drawerSlug = ref('')
const drawerTitle = ref('')
const drawerDesc = ref('')
const drawerHint = ref('')
const drawerFields = ref<CapabilityField[]>([])
const drawerValues = reactive<Record<string, string>>({})
const drawerOriginal = ref<Record<string, string>>({})
const drawerTestLabel = ref('测试连接')
// 没有 test() 的能力（例如站点与品牌）不显示「测试」按钮——
// 点了只会回一句「不支持测试」的按钮，本质是界面在骗人
const drawerTestable = ref(true)
const capSaving = ref(false)
const capTesting = ref(false)
const testInput = reactive({ value: '' })
const testResult = ref<CapabilityTestResult | null>(null)

const drawerDirty = computed(() =>
  drawerFields.value.some((f) => String(drawerValues[f.key] ?? '') !== String(drawerOriginal.value[f.key] ?? ''))
)
const drawerTestInput = computed(() => TEST_INPUTS[drawerSlug.value])

function capStatus(card: CapabilityCard): { label: string; tone: 'ok' | 'warn' | 'idle' } {
  if (card.enabled) return { label: '已启用', tone: 'ok' }
  if (card.configured) return { label: '已配置 · 未启用', tone: 'warn' }
  return { label: '未配置', tone: 'idle' }
}

async function loadCapabilities() {
  capsLoading.value = true
  capsError.value = false
  try {
    const res = await fetchCapabilities()
    // 防御：后端返回格式异常时保证 capabilities 始终是数组，避免页签白屏
    const list = res && Array.isArray(res.capabilities) ? res.capabilities : []
    capabilities.value = list
    if (!list.length) {
      // 空数组不算错误，模板会显示"暂无可配置的外部能力"
      capsError.value = false
    }
  } catch {
    // 拦截器已提示
    capsError.value = !capabilities.value.length
  } finally {
    capsLoading.value = false
  }
}

async function openCapability(slug: string) {
  try {
    const detail = await fetchCapability(slug)
    drawerSlug.value = slug
    drawerTitle.value = detail.spec.title
    drawerDesc.value = detail.spec.desc
    drawerHint.value = detail.spec.docs_hint || ''
    drawerFields.value = detail.spec.fields
    drawerTestLabel.value = detail.item.test_label || '测试连接'
    drawerTestable.value = detail.item.testable !== false
    for (const key of Object.keys(drawerValues)) delete drawerValues[key]
    for (const f of detail.spec.fields) {
      drawerValues[f.key] = detail.values[f.key] ?? f.default ?? ''
    }
    drawerOriginal.value = { ...drawerValues }
    testResult.value = null
    testInput.value = ''
    drawerOpen.value = true
  } catch {
    // 拦截器已提示
  }
}

async function saveCurrentCapability() {
  capSaving.value = true
  try {
    await saveCapability(drawerSlug.value, { ...drawerValues })
    ElMessage.success(`「${drawerTitle.value}」已保存${drawerSlug.value === 'proxy' ? '，出站代理立即生效' : ''}`)
    drawerOriginal.value = { ...drawerValues }
    await Promise.all([loadCapabilities(), openCapability(drawerSlug.value), refreshEconomy()])
  } catch {
    // 拦截器已提示
  } finally {
    capSaving.value = false
  }
}

async function runCapabilityTest() {
  capTesting.value = true
  testResult.value = null
  try {
    const payload: Record<string, unknown> = {}
    if (drawerTestInput.value && testInput.value.trim()) {
      payload[drawerTestInput.value.key] = testInput.value.trim()
    }
    testResult.value = await testCapability(drawerSlug.value, payload)
  } catch {
    // 拦截器已提示
  } finally {
    capTesting.value = false
  }
}

/**
 * 只刷新运营参数的「生效值」，保留其它分组里还没保存的改动：
 * 以前保存任一分组都会整页 load()，把别的分组刚改的输入悄悄冲掉。
 * ``savedKeys`` 是刚保存的那批键，一律以服务端为准。
 */
async function refreshEconomy(savedKeys: string[] = []) {
  try {
    const econ = await fetchEconomySettings()
    const prev = original.value
    for (const [key, value] of Object.entries(econ.settings)) {
      const dirty = String(settings[key] ?? '') !== String(prev[key] ?? '')
      if (!dirty || savedKeys.includes(key)) settings[key] = value
    }
    original.value = { ...econ.settings }
  } catch {
    // 拦截器已提示；保留当前表单
  }
}

async function load() {
  loading.value = true
  paramsError.value = false
  try {
    const [econ, registration] = await Promise.all([
      fetchEconomySettings(),
      fetchRegistrationSettings(),
    ])
    Object.assign(settings, econ.settings)
    original.value = { ...econ.settings }
    // 注册两档（open / closed）后端归一后下发，直接展示
    reg.mode = registration.mode
    reg.message = registration.message || ''
    reg.ratelimitMax = typeof registration.ratelimit_max === 'number' && Number.isFinite(registration.ratelimit_max)
      ? Math.floor(registration.ratelimit_max)
      : 5
    reg.ratelimitWindow = typeof registration.ratelimit_window === 'number' && Number.isFinite(registration.ratelimit_window)
      ? Math.floor(registration.ratelimit_window)
      : 3600
    snapshotReg()
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
    paramsError.value = true
  } finally {
    loading.value = false
    // 分组目录回到横向起点（移动端横滑导航）
    resetGroupNavScroll()
  }
}

onMounted(async () => {
  await Promise.all([load(), loadCapabilities(), loadHotlink(), loadInstallApp(), loadItemsDedupCache(), loadTransfer(), loadMountServing(), loadDriveApi(), loadDeliveryPlan(), loadSoftdecode(), loadBotCfg(), loadDanmaku(), loadDanmuLines(), loadLoginLockout(), loadSiteDrawer(), loadPrivacyPolicy(), loadLicense()])
  // Batch D 入口收敛：邀请页 / 客户端策略等用 hash 深链跳进来（如 #group-invitation）
  // R40：querySelector 直接塞 hash 会在畸形值时抛 SyntaxError；改用 getElementById
  if (route.hash) {
    await nextTick()
    const el = document.getElementById(route.hash.slice(1))
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
})

/** ==================== 防盗链卡片（独立 endpoint，非通用分组） ==================== */
/** 用独立接口而非 GROUPS 通用分组：TTL 需要前后端双重范围校验（60~86400），
 * 通用分组的 int 字段只有 min=0 的粗校验，装不下。 */
const hotlink = reactive({ play_sign_ttl: 900, allowed_referers: '' })
const hotlinkOriginal = reactive({ play_sign_ttl: 900, allowed_referers: '' })
const hotlinkLoading = ref(false)

function applyHotlink(data: HotlinkConfigGet) {
  hotlink.play_sign_ttl = Number(data.play_sign_ttl) || 900
  // 后端回包是归一化后的域名数组；编辑态存逗号分隔串
  hotlink.allowed_referers = Array.isArray(data.allowed_referers)
    ? data.allowed_referers.join(', ')
    : String(data.allowed_referers ?? '')
  Object.assign(hotlinkOriginal, hotlink)
}

async function loadHotlink() {
  hotlinkLoading.value = true
  try {
    applyHotlink(await fetchHotlinkConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    hotlinkLoading.value = false
  }
}

const hotlinkDirty = computed(() =>
hotlink.play_sign_ttl !== hotlinkOriginal.play_sign_ttl
  || hotlink.allowed_referers !== hotlinkOriginal.allowed_referers,
)

/** 防盗链保存核心：返回是否成功，失败时拦截器/校验已提示。 */
async function saveHotlinkCore(): Promise<boolean> {
  // 前端先行校验：与后端 play_sign.write_config 的范围保持一致
  if (!Number.isInteger(hotlink.play_sign_ttl) || hotlink.play_sign_ttl < 60 || hotlink.play_sign_ttl > 86400) {
    ElMessage.error('播放签名有效期需在 60~86400 秒之间')
    return false
  }
  const payload: HotlinkConfigPayload = {
    play_sign_ttl: hotlink.play_sign_ttl,
    allowed_referers: hotlink.allowed_referers,
  }
  try {
    applyHotlink(await updateHotlinkConfig(payload))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 安装应用卡片（独立 endpoint，非通用分组） ==================== */
/** 用户端「安装应用」页的 6 平台客户端下载链接：页面只读后端下发的生效值
 * 渲染，链接不在前端写死、也没有代码级默认。某行清空保存 = 删行取消
 * 配置，未配的平台用户端整行不显示。 */
const installAppPlatforms = ref<InstallAppPlatformConfig[]>([])
const installAppUrls = ref<Record<string, string>>({})
const installAppUrlsOriginal = ref<Record<string, string>>({})
const installAppLoading = ref(false)

function applyInstallApp(data: InstallAppConfigResponse) {
  installAppPlatforms.value = data.platforms
  const urls = Object.fromEntries(data.platforms.map((p) => [p.key, p.url]))
  installAppUrls.value = urls
  installAppUrlsOriginal.value = { ...urls }
}

async function loadInstallApp() {
  installAppLoading.value = true
  try {
    applyInstallApp(await fetchInstallAppConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    installAppLoading.value = false
  }
}

const installAppDirty = computed(() =>
  installAppPlatforms.value.some(
    (p) => (installAppUrls.value[p.key] ?? '') !== (installAppUrlsOriginal.value[p.key] ?? ''),
  ),
)

/** 安装应用保存核心：只提交改过的平台；返回是否成功。 */
async function saveInstallAppCore(): Promise<boolean> {
  const changed: Record<string, string> = {}
  for (const p of installAppPlatforms.value) {
    const v = (installAppUrls.value[p.key] ?? '').trim()
    if (v !== (installAppUrlsOriginal.value[p.key] ?? '')) {
      // 前端先行校验，与后端端点层的 http(s) 形状校验保持一致
      if (v && !/^https?:\/\/.+/.test(v)) {
        ElMessage.error(`「${p.label}」的下载链接必须是 http(s) 链接`)
        return false
      }
      changed[p.key] = v
    }
  }
  if (!Object.keys(changed).length) return true
  try {
    applyInstallApp(await updateInstallAppConfig(changed))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 列表去重缓存卡片（独立 endpoint，非通用分组） ==================== */
/** 与防盗链卡片同构：TTL/条目上限需要前后端双重范围校验，通用分组装不下。 */
const itemsDedupCache = reactive({ ttl_seconds: 60, max_entries: 2000 })
const itemsDedupCacheOriginal = reactive({ ttl_seconds: 60, max_entries: 2000 })
const itemsDedupCacheLoading = ref(false)

function applyItemsDedupCache(data: ItemsDedupCacheConfig) {
  itemsDedupCache.ttl_seconds = Number(data.ttl_seconds) || 60
  itemsDedupCache.max_entries = Number(data.max_entries) || 2000
  Object.assign(itemsDedupCacheOriginal, itemsDedupCache)
}

async function loadItemsDedupCache() {
  itemsDedupCacheLoading.value = true
  try {
    applyItemsDedupCache(await fetchItemsDedupCacheConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    itemsDedupCacheLoading.value = false
  }
}

const itemsDedupCacheDirty = computed(() =>
itemsDedupCache.ttl_seconds !== itemsDedupCacheOriginal.ttl_seconds
  || itemsDedupCache.max_entries !== itemsDedupCacheOriginal.max_entries,
)

/** 列表去重缓存保存核心：返回是否成功，失败时拦截器/校验已提示。 */
async function saveItemsDedupCacheCore(): Promise<boolean> {
  // 前端先行校验：与后端 write_config 的范围保持一致
  if (!Number.isInteger(itemsDedupCache.ttl_seconds) || itemsDedupCache.ttl_seconds < 10 || itemsDedupCache.ttl_seconds > 3600) {
    ElMessage.error('缓存有效期需在 10~3600 秒之间')
    return false
  }
  if (!Number.isInteger(itemsDedupCache.max_entries) || itemsDedupCache.max_entries < 100 || itemsDedupCache.max_entries > 20000) {
    ElMessage.error('缓存条目上限需在 100~20000 之间')
    return false
  }
  const payload: ItemsDedupCacheConfigPayload = {
    ttl_seconds: itemsDedupCache.ttl_seconds,
    max_entries: itemsDedupCache.max_entries,
  }
  try {
    applyItemsDedupCache(await updateItemsDedupCacheConfig(payload))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 转存卡片（独立 endpoint，非通用分组） ==================== */
/** 与列表去重缓存卡片同构：并发上限需要前后端双重范围校验，通用分组装不下。 */
const transfer = reactive({
  cross_provider_transfer_enabled: true,
  transfer_cross_lane_concurrency: 2,
  transfer_series_preheat_enabled: true,
  transfer_series_preheat_daily_max: 2,
  transfer_series_preheat_next_count: 3,
  transfer_cross_aging_sec: 1800,
  transfer_fast_lane_concurrency: 8,
  transfer_per_owner_inflight: 2,
  transfer_rebind_waiver_window_days: 7,
  transfer_rebind_waiver_max_uses: 1,
  transfer_paused_categories: '[]',
})
const transferOriginal = reactive({ ...transfer })
const transferLoading = ref(false)

function applyTransfer(data: TransferConfig) {
  transfer.cross_provider_transfer_enabled = data.cross_provider_transfer_enabled !== false
  transfer.transfer_cross_lane_concurrency = Number(data.transfer_cross_lane_concurrency) || 2
  transfer.transfer_series_preheat_enabled = data.transfer_series_preheat_enabled !== false
  transfer.transfer_series_preheat_daily_max = Number(data.transfer_series_preheat_daily_max) || 2
  transfer.transfer_series_preheat_next_count = Number(data.transfer_series_preheat_next_count) || 3
  transfer.transfer_cross_aging_sec = Number(data.transfer_cross_aging_sec) || 1800
  transfer.transfer_fast_lane_concurrency = Number(data.transfer_fast_lane_concurrency) || 8
  transfer.transfer_per_owner_inflight = Number(data.transfer_per_owner_inflight) || 2
  transfer.transfer_rebind_waiver_window_days = Number(data.transfer_rebind_waiver_window_days) || 7
  transfer.transfer_rebind_waiver_max_uses = Number(data.transfer_rebind_waiver_max_uses) || 1
  transfer.transfer_paused_categories = typeof data.transfer_paused_categories === 'string'
    ? data.transfer_paused_categories : '[]'
  Object.assign(transferOriginal, transfer)
}

async function loadTransfer() {
  transferLoading.value = true
  try {
    applyTransfer(await fetchTransferConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    transferLoading.value = false
  }
}

const transferDirty = computed(() =>
transfer.cross_provider_transfer_enabled !== transferOriginal.cross_provider_transfer_enabled
  || transfer.transfer_cross_lane_concurrency !== transferOriginal.transfer_cross_lane_concurrency
  || transfer.transfer_series_preheat_enabled !== transferOriginal.transfer_series_preheat_enabled
  || transfer.transfer_series_preheat_daily_max !== transferOriginal.transfer_series_preheat_daily_max
  || transfer.transfer_series_preheat_next_count !== transferOriginal.transfer_series_preheat_next_count
  || transfer.transfer_cross_aging_sec !== transferOriginal.transfer_cross_aging_sec
  || transfer.transfer_fast_lane_concurrency !== transferOriginal.transfer_fast_lane_concurrency
  || transfer.transfer_per_owner_inflight !== transferOriginal.transfer_per_owner_inflight
  || transfer.transfer_rebind_waiver_window_days !== transferOriginal.transfer_rebind_waiver_window_days
  || transfer.transfer_rebind_waiver_max_uses !== transferOriginal.transfer_rebind_waiver_max_uses
  || transfer.transfer_paused_categories !== transferOriginal.transfer_paused_categories,
)

/** 转存保存核心：返回是否成功，失败时拦截器/校验已提示。 */
async function saveTransferCore(): Promise<boolean> {
  // 前端先行校验：与后端 config_schema 的 transfer 家族范围保持一致
  if (!Number.isInteger(transfer.transfer_cross_lane_concurrency)
    || transfer.transfer_cross_lane_concurrency < 1 || transfer.transfer_cross_lane_concurrency > 8) {
    ElMessage.error('跨 provider 并发上限需在 1~8 之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_series_preheat_daily_max)
    || transfer.transfer_series_preheat_daily_max < 1 || transfer.transfer_series_preheat_daily_max > 16) {
    ElMessage.error('剧集预热搬字节日帽需在 1~16 之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_series_preheat_next_count)
    || transfer.transfer_series_preheat_next_count < 1 || transfer.transfer_series_preheat_next_count > 10) {
    ElMessage.error('剧集预热候选集数需在 1~10 之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_cross_aging_sec)
    || transfer.transfer_cross_aging_sec < 60 || transfer.transfer_cross_aging_sec > 86400) {
    ElMessage.error('跨单排队提权阈值需在 60~86400 秒之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_fast_lane_concurrency)
    || transfer.transfer_fast_lane_concurrency < 1 || transfer.transfer_fast_lane_concurrency > 32) {
    ElMessage.error('转存道并发需在 1~32 之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_per_owner_inflight)
    || transfer.transfer_per_owner_inflight < 1 || transfer.transfer_per_owner_inflight > 16) {
    ElMessage.error('单用户在飞上限需在 1~16 之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_rebind_waiver_window_days)
    || transfer.transfer_rebind_waiver_window_days < 1 || transfer.transfer_rebind_waiver_window_days > 365) {
    ElMessage.error('换绑放行窗口需在 1~365 天之间')
    return false
  }
  if (!Number.isInteger(transfer.transfer_rebind_waiver_max_uses)
    || transfer.transfer_rebind_waiver_max_uses < 1 || transfer.transfer_rebind_waiver_max_uses > 20) {
    ElMessage.error('换绑放行次数需在 1~20 之间')
    return false
  }
  try {
    const pausedRules = JSON.parse(transfer.transfer_paused_categories || '[]')
    if (!Array.isArray(pausedRules)) throw new Error('not-array')
  } catch {
    ElMessage.error('类别级熔断规则必须是 JSON 数组，如 [{"provider": "drive", "error_kind": "*"}]')
    return false
  }
  try {
    applyTransfer(await updateTransferConfig({ ...transfer }))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 挂载直读出流卡片（独立 endpoint，非通用分组） ==================== */
/** 与列表缓存卡片同构：白名单/根映射的内容校验在后端 write_config，
 * 前端只做形态校验（id 为正整数、根为绝对路径）。 */
const mountServing = reactive({ enabled: true, mount_ids: '', local_roots: '' })
const mountServingOriginal = reactive({ enabled: true, mount_ids: '', local_roots: '' })
const mountServingLoading = ref(false)

function applyMountServing(data: MountServingConfig) {
  mountServing.enabled = data.enabled !== false
  mountServing.mount_ids = Array.isArray(data.mount_ids) ? data.mount_ids.join(',') : ''
  mountServing.local_roots = String(data.local_roots || '')
  Object.assign(mountServingOriginal, mountServing)
}

async function loadMountServing() {
  mountServingLoading.value = true
  try {
    applyMountServing(await fetchMountServingConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    mountServingLoading.value = false
  }
}

const mountServingDirty = computed(() =>
  mountServing.enabled !== mountServingOriginal.enabled
  || mountServing.mount_ids !== mountServingOriginal.mount_ids
  || mountServing.local_roots !== mountServingOriginal.local_roots,
)

/** 挂载直读保存核心：返回是否成功，失败时拦截器/校验已提示。 */
async function saveMountServingCore(): Promise<boolean> {
  const ids = mountServing.mount_ids.split(',').map((t) => t.trim()).filter(Boolean)
  if (ids.some((t) => !/^\d+$/.test(t) || Number(t) <= 0)) {
    ElMessage.error('白名单必须是逗号分隔的正整数挂载 id')
    return false
  }
  const roots = mountServing.local_roots.split(',').map((t) => t.trim()).filter(Boolean)
  if (roots.some((t) => !/^\d+=\//.test(t))) {
    ElMessage.error('本地根映射格式须为 id=/绝对路径，逗号分隔')
    return false
  }
  const payload: MountServingConfigPayload = {
    enabled: mountServing.enabled,
    mount_ids: ids.map(Number),
    local_roots: mountServing.local_roots,
  }
  try {
    applyMountServing(await updateMountServingConfig(payload))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== Drive API 限速卡片（独立 endpoint，非通用分组） ==================== */
const driveApi = reactive({ qps: 8 })
const driveApiOriginal = reactive({ qps: 8 })
const driveApiLoading = ref(false)

function applyDriveApi(data: DriveApiConfig) {
  driveApi.qps = Number(data.qps) || 8
  Object.assign(driveApiOriginal, driveApi)
}

async function loadDriveApi() {
  driveApiLoading.value = true
  try {
    applyDriveApi(await fetchDriveApiConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    driveApiLoading.value = false
  }
}

const driveApiDirty = computed(
  () => driveApi.qps !== driveApiOriginal.qps,
)

/** Drive API 限速保存核心：返回是否成功，失败时拦截器/校验已提示。 */
async function saveDriveApiCore(): Promise<boolean> {
  if (!Number.isFinite(driveApi.qps) || driveApi.qps < 1 || driveApi.qps > 20) {
    ElMessage.error('Drive API 速率上限须在 1~20 之间')
    return false
  }
  try {
    applyDriveApi(await updateDriveApiConfig({
      qps: driveApi.qps,
    }))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 机器人设置卡片（独立 endpoint，非通用分组） ====================
 * TG 机器人的行为开关，走 /api/admin/welfare/config（后端
 * welfare_admin.WELFARE_CONFIG_KEYS 白名单）。2026-11-24 查漏补缺
 * N-02：这批键此前只有端点没有页面，机器人提示里的「先在管理后台
 * 打开」指向不存在的入口，现补齐。字段默认值/类型/范围以后端注册表
 * 为准（WELFARE_CONFIG_KEYS 默认值 + config_schema.
 * WELFARE_CONFIG_SCHEMA 的 spec），前端不另立口径。
 * 同一端点下的充值两键（recharge_ratio / recharge_quick_amounts）
 * 不属于机器人设置，本卡不呈现，GET 回来也忽略、PUT 不发。 */
interface BotFieldGroup {
  id: string
  title: string
  fields: Field[]
}
const BOT_FIELD_GROUPS: BotFieldGroup[] = [
  {
    id: 'group-limit',
    title: '群与限流',
    fields: [
      { key: 'bot_group_ids', label: '机器人交流群', type: 'str', hint: '机器人提供服务的群 ID，逗号分隔（如 -1001234567890）；播报与排行都发到这里，留空则群功能不可用' },
      { key: 'bot_rate_limit_seconds', label: '命令限流间隔', type: 'int', suffix: '秒', hint: '同一用户两次命令的最小间隔，默认 3（范围 0~3600）' },
      { key: 'bot_group_rate_limit', label: '群消息限流', type: 'int', suffix: '条/分钟', hint: '每个群每分钟最多处理多少条消息，超限静默丢弃，默认 20（范围 0~100000）' },
    ],
  },
  {
    id: 'digest',
    title: '播报推送',
    fields: [
      { key: 'bot_update_digest_enabled', label: '入库播报', type: 'bool', hint: '新内容入库后攒批播报，默认开' },
      { key: 'bot_update_digest_interval_minutes', label: '播报攒批间隔', type: 'int', suffix: '分钟', hint: '攒够一批再发的间隔，默认 30（范围 1~1440）' },
      { key: 'bot_update_digest_chat_id', label: '播报发送目标', type: 'str', hint: '留空发交流群；填频道 @用户名或 -100 开头的频道 ID 则发到频道' },
      { key: 'bot_rankpush_enabled', label: '观影榜海报日推', type: 'bool', hint: '每天把观影榜海报推到交流群，默认开' },
    ],
  },
  {
    id: 'playrank',
    title: '每日播放排行榜',
    fields: [
      { key: 'bot_playrank_enabled', label: '排行榜总开关', type: 'bool', hint: '默认关；关时不定时推送，管理员在群里手动 /playrank 也会提示先在这里打开' },
      { key: 'bot_playrank_time', label: '发送时间', type: 'str', hint: '每天几点发（HH:MM，上海时间），默认 21:30；格式不对会回落 21:30' },
      { key: 'bot_playrank_top_n', label: '每榜条数', type: 'int', suffix: '条', hint: '每个榜取前几名，默认 5（范围 1~20）' },
      { key: 'bot_playrank_users_enabled', label: '观看时长榜', type: 'bool', hint: '按用户观看时长排名，默认开' },
      { key: 'bot_playrank_series_enabled', label: '剧集榜', type: 'bool', hint: '默认开' },
      { key: 'bot_playrank_movies_enabled', label: '电影榜', type: 'bool', hint: '默认开' },
      { key: 'bot_playrank_poster_enabled', label: '附带海报图', type: 'bool', hint: '推榜时带上海报图，默认开' },
    ],
  },
  {
    id: 'notify',
    title: '通知提醒',
    fields: [
      { key: 'tg_notify_store_arrival_enabled', label: '求片到货通知', type: 'bool', hint: '求片到货后私聊通知求片人，默认开' },
      { key: 'tg_notify_transfer_done_enabled', label: '转存完成通知', type: 'bool', hint: '转存到达终态时私聊通知，默认开' },
      { key: 'tg_notify_new_request_enabled', label: '新求片提醒管理员', type: 'bool', hint: '有新求片时私聊提醒管理员，默认开' },
      { key: 'tg_notify_merge_window_sec', label: '通知合并窗口', type: 'int', suffix: '秒', hint: '同一人的多条通知在窗口内合并成一条，默认 60（范围 0~3600）' },
      { key: 'tg_notify_group_arrival_enabled', label: '到货群报喜', type: 'bool', hint: '许愿到货时在群里报喜，默认开；关了只发私聊' },
    ],
  },
  {
    id: 'governance',
    title: '群治理',
    fields: [
      { key: 'bot_channel_guard_enabled', label: '频道马甲治理', type: 'bool', hint: '处理冒充官方的频道马甲发言，默认开' },
      { key: 'bot_channel_whitelist', label: '频道白名单', type: 'str', hint: '逗号分隔的频道 ID，白名单内的频道不受治理处理' },
      {
        key: 'bot_leave_action',
        label: '退群处置',
        type: 'str',
        choices: [
          { value: 'none', label: '不处理（none）' },
        ],
        hint: '成员退群后的处置动作；后端当前只开放「不处理」',
      },
    ],
  },
]
/** 默认值与后端 WELFARE_CONFIG_KEYS 逐字对齐（仅作加载前占位与缺值兜底，真实值以 GET 为准） */
const BOT_DEFAULTS: Record<string, string> = {
  bot_group_ids: '',
  bot_rate_limit_seconds: '3',
  bot_group_rate_limit: '20',
  bot_rankpush_enabled: 'true',
  bot_update_digest_enabled: 'true',
  bot_update_digest_interval_minutes: '30',
  bot_update_digest_chat_id: '',
  bot_playrank_enabled: 'false',
  bot_playrank_time: '21:30',
  bot_playrank_top_n: '5',
  bot_playrank_users_enabled: 'true',
  bot_playrank_series_enabled: 'true',
  bot_playrank_movies_enabled: 'true',
  bot_playrank_poster_enabled: 'true',
  tg_notify_store_arrival_enabled: 'true',
  tg_notify_transfer_done_enabled: 'true',
  tg_notify_new_request_enabled: 'true',
  tg_notify_merge_window_sec: '60',
  tg_notify_group_arrival_enabled: 'true',
  bot_channel_guard_enabled: 'true',
  bot_channel_whitelist: '',
  bot_leave_action: 'none',
}
const BOT_KEYS = Object.keys(BOT_DEFAULTS)
const BOT_FIELD_LABELS: Record<string, string> = {}
for (const g of BOT_FIELD_GROUPS) {
  for (const f of g.fields) BOT_FIELD_LABELS[f.key] = f.label
}
/** int 键范围与 config_schema.WELFARE_CONFIG_SCHEMA 逐字对齐 */
const BOT_INT_RANGES: Record<string, [number, number]> = {
  bot_rate_limit_seconds: [0, 3600],
  bot_group_rate_limit: [0, 100000],
  bot_update_digest_interval_minutes: [1, 1440],
  bot_playrank_top_n: [1, 20],
  tg_notify_merge_window_sec: [0, 3600],
}
const BOT_TIME_KEYS = [
  'bot_playrank_time',
]
const botCfg = reactive<Record<string, string>>({ ...BOT_DEFAULTS })
const botCfgOriginal = ref<Record<string, string>>({ ...BOT_DEFAULTS })
const botCfgLoading = ref(false)

function applyBotCfg(data: Record<string, string>) {
  for (const key of BOT_KEYS) {
    botCfg[key] = typeof data[key] === 'string' ? data[key] : BOT_DEFAULTS[key]
  }
  botCfgOriginal.value = { ...botCfg }
}

async function loadBotCfg() {
  botCfgLoading.value = true
  try {
    applyBotCfg(await fetchWelfareConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    botCfgLoading.value = false
  }
}

const botCfgDirty = computed(
  () => BOT_KEYS.some((key) => botCfg[key] !== botCfgOriginal.value[key]),
)

/** 保存前校验：与后端 schema 同口径，先在前端拦下，省一次往返 */
function validateBotCfg(): string | null {
  for (const [key, [min, max]] of Object.entries(BOT_INT_RANGES)) {
    const raw = String(botCfg[key] ?? '').trim()
    if (!/^-?\d+$/.test(raw) || Number(raw) < min || Number(raw) > max) {
      return `「${BOT_FIELD_LABELS[key]}」须为 ${min}~${max} 之间的整数`
    }
  }
  for (const key of BOT_TIME_KEYS) {
    const raw = String(botCfg[key] ?? '').trim()
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(raw)) {
      return `「${BOT_FIELD_LABELS[key]}」须为 HH:MM 格式（如 21:30）`
    }
  }
  return null
}

/** 机器人设置保存核心：只发改过的键；PUT 只回 updated 名单不回
 * 全量值，存完必须回读一遍核对才算数。 */
async function saveBotCfgCore(): Promise<boolean> {
  const err = validateBotCfg()
  if (err) {
    ElMessage.error(err)
    return false
  }
  const payload: Record<string, string> = {}
  for (const key of BOT_KEYS) {
    if (botCfg[key] === botCfgOriginal.value[key]) continue
    // int 键先归一成后端落库口径（去前导零），回读比对才不会假报警
    payload[key] = key in BOT_INT_RANGES
      ? String(Number.parseInt(String(botCfg[key]).trim(), 10))
      : botCfg[key]
  }
  if (!Object.keys(payload).length) return true
  try {
    await saveWelfareConfig(payload)
    const back = await fetchWelfareConfig()
    applyBotCfg(back)
    const mismatch = Object.keys(payload).filter(
      (key) => String(back[key] ?? '') !== payload[key],
    )
    if (mismatch.length) {
      ElMessage.warning(
        `机器人设置已保存，但回读核对不一致：${mismatch.map((k) => BOT_FIELD_LABELS[k]).join('、')}，请刷新页面确认`,
      )
    }
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 播放交付计划卡片（独立 endpoint，非通用分组） ====================
 * 交付编排器（delivery_plan）：播放前先算一份「交付计划」（走哪条腿、
 * 副本是否就绪等），计划按 TTL 缓存复用。保存后回读一次核对落库值。 */
const deliveryPlan = reactive({ enabled: true, ttl_sec: 600 })
const deliveryPlanOriginal = reactive({ enabled: true, ttl_sec: 600 })
const deliveryPlanLoading = ref(false)

function applyDeliveryPlan(data: DeliveryPlanConfig) {
  deliveryPlan.enabled = data.enabled !== false
  deliveryPlan.ttl_sec = Number(data.ttl_sec) || 600
  Object.assign(deliveryPlanOriginal, deliveryPlan)
}

async function loadDeliveryPlan() {
  deliveryPlanLoading.value = true
  try {
    applyDeliveryPlan(await fetchDeliveryPlanConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    deliveryPlanLoading.value = false
  }
}

const deliveryPlanDirty = computed(
  () => deliveryPlan.enabled !== deliveryPlanOriginal.enabled
    || deliveryPlan.ttl_sec !== deliveryPlanOriginal.ttl_sec,
)

/** 交付计划保存核心：PUT 后再 GET 回读，与提交值不一致视为没存上。 */
async function saveDeliveryPlanCore(): Promise<boolean> {
  if (!Number.isInteger(deliveryPlan.ttl_sec) || deliveryPlan.ttl_sec < 30 || deliveryPlan.ttl_sec > 3600) {
    ElMessage.error('交付计划 TTL 须为 30~3600 的整数秒')
    return false
  }
  const submitted = { enabled: deliveryPlan.enabled, ttl_sec: deliveryPlan.ttl_sec }
  try {
    applyDeliveryPlan(await updateDeliveryPlanConfig(submitted))
    // 回读校验：以服务端当前值为准比对，防「报成功但没落库」
    const readBack = await fetchDeliveryPlanConfig()
    applyDeliveryPlan(readBack)
    if (readBack.enabled !== submitted.enabled || Number(readBack.ttl_sec) !== submitted.ttl_sec) {
      ElMessage.error('交付计划保存后回读不一致，请重试')
      return false
    }
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 网页软解卡片（独立 endpoint，非通用分组） ====================
 * 浏览器硬解失败时的站内软解兜底：只在网页播放路径生效，解码器是站内自编
 * 的 LGPL wasm（/wasm/decode/）。默认关：关时保持现状出解码提示卡。 */
const softdecode = reactive({ enabled: false })
const softdecodeOriginal = reactive({ enabled: false })
const softdecodeLoading = ref(false)

function applySoftdecode(data: SoftdecodeConfig) {
  softdecode.enabled = data.enabled === true
  Object.assign(softdecodeOriginal, softdecode)
}

async function loadSoftdecode() {
  softdecodeLoading.value = true
  try {
    applySoftdecode(await fetchSoftdecodeConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    softdecodeLoading.value = false
  }
}

const softdecodeDirty = computed(
  () => softdecode.enabled !== softdecodeOriginal.enabled,
)

/** 网页软解保存核心：返回是否成功，失败时拦截器已提示。 */
async function saveSoftdecodeCore(): Promise<boolean> {
  try {
    applySoftdecode(await updateSoftdecodeConfig({ enabled: softdecode.enabled }))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 弹幕设置卡片（独立 endpoint，非通用分组） ====================
 * 站内自发弹幕的全部可调项（后端 SystemConfig 家族 danmaku）：
 * 站点开关默认关（关时用户端两口 404、播放器隐藏弹幕入口）、
 * 敏感词表（逗号分隔、空 = 不过滤）、最小发送间隔、单条字数上限。 */
const danmaku = reactive<DanmakuConfig>({
  enabled: false,
  blocked_words: '',
  min_interval_seconds: 5,
  max_text_length: 100,
})
const danmakuOriginal = reactive<DanmakuConfig>({ ...danmaku })
const danmakuLoading = ref(false)

function applyDanmaku(data: DanmakuConfig) {
  danmaku.enabled = data.enabled === true
  danmaku.blocked_words = data.blocked_words ?? ''
  danmaku.min_interval_seconds = data.min_interval_seconds ?? 5
  danmaku.max_text_length = data.max_text_length ?? 100
  Object.assign(danmakuOriginal, danmaku)
}

async function loadDanmaku() {
  danmakuLoading.value = true
  try {
    applyDanmaku(await fetchDanmakuConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    danmakuLoading.value = false
  }
}

const danmakuDirty = computed(
  () =>
    danmaku.enabled !== danmakuOriginal.enabled ||
    danmaku.blocked_words !== danmakuOriginal.blocked_words ||
    danmaku.min_interval_seconds !== danmakuOriginal.min_interval_seconds ||
    danmaku.max_text_length !== danmakuOriginal.max_text_length,
)

/** 弹幕设置保存核心：返回是否成功，失败时拦截器已提示。 */
async function saveDanmakuCore(): Promise<boolean> {
  try {
    applyDanmaku(await updateDanmakuConfig({ ...danmaku }))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 弹幕 API 线路卡片（独立 endpoint，非通用分组） ====================
 * 给第三方播放器用的弹幕 API 线路表（后端 SystemConfig 家族 danmu_api 的
 * danmu_api_lines）：用户在概览页生成弹幕 Token 后选一条线路拼地址，
 * 线路只换 host。GET 回的就是当前生效值（未配置时后端代入默认两条）；
 * 保存整批校验——至少一条、id 小写 slug 且全表唯一、名称非空、
 * host 为裸域名（不带协议/路径/端口），非法整批 400 不落库。
 * 每行带 enabled 开关（启用/暂停服务）：随整批一并保存，旧配置
 * 无此字段时读回默认启用。 */
const danmuLines = ref<DanmuApiLine[]>([])
const danmuLinesOriginal = ref('')
const danmuLinesLoading = ref(false)
/** 外部弹幕源两项（与线路同卡同存）：开关 + 上游 base 地址。 */
const danmuExternal = reactive({ enabled: false, url: '' })
const danmuExternalOriginal = ref('')
/** 外部源地址是否已配置：后端掩码口径下 GET 只回 "******" + external_url_set，
 * 真值不回填输入框；保存时留空且已配置 → 回传 "******" 表示不修改。 */
const danmuExternalConfigured = ref(false)

function danmuLinesSnapshot(): DanmuApiLine[] {
  return danmuLines.value.map((ln) => ({
    id: ln.id.trim(),
    name: ln.name.trim(),
    note: ln.note.trim(),
    host: ln.host.trim().toLowerCase(),
    enabled: ln.enabled !== false,
  }))
}

function danmuExternalSnapshot(): string {
  return JSON.stringify({
    enabled: danmuExternal.enabled,
    url: danmuExternal.url.trim(),
  })
}

function applyDanmuLines(config: {
  lines: DanmuApiLine[]
  external_enabled?: boolean
  external_url?: string
  external_url_set?: boolean
}) {
  danmuLines.value = (config.lines || []).map((ln) => ({
    id: ln.id ?? '',
    name: ln.name ?? '',
    note: ln.note ?? '',
    host: ln.host ?? '',
    // 旧配置无 enabled 字段时读回默认启用（后端归一化同口径）
    enabled: ln.enabled !== false,
  }))
  danmuLinesOriginal.value = JSON.stringify(danmuLinesSnapshot())
  danmuExternal.enabled = config.external_enabled ?? false
  const rawExternalUrl = config.external_url ?? ''
  if (rawExternalUrl === '******' || config.external_url_set) {
    // 掩码口径：已配置但真值不下发，输入框留空（占位提示已配置），不回填掩码串
    danmuExternalConfigured.value = true
    danmuExternal.url = ''
  } else {
    danmuExternalConfigured.value = !!rawExternalUrl
    danmuExternal.url = rawExternalUrl
  }
  danmuExternalOriginal.value = danmuExternalSnapshot()
}

async function loadDanmuLines() {
  danmuLinesLoading.value = true
  try {
    applyDanmuLines(await fetchDanmuApiLines())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    danmuLinesLoading.value = false
  }
}

const danmuLinesDirty = computed(
  () =>
    (danmuLinesOriginal.value !== ''
      && JSON.stringify(danmuLinesSnapshot()) !== danmuLinesOriginal.value)
    || (danmuExternalOriginal.value !== ''
      && danmuExternalSnapshot() !== danmuExternalOriginal.value),
)

function addDanmuLine() {
  danmuLines.value.push({ id: '', name: '', note: '', host: '', enabled: true })
}

/** 弹幕 API 线路保存核心：返回是否成功，失败时拦截器已提示（含后端校验原文）。 */
async function saveDanmuLinesCore(): Promise<boolean> {
  try {
    applyDanmuLines(
      await updateDanmuApiLines(danmuLinesSnapshot(), {
        external_enabled: danmuExternal.enabled,
        // 输入了新地址传新地址；留空且原本已配置传掩码表示不修改；未配置留空传空
        external_url: danmuExternal.url.trim()
          || (danmuExternalConfigured.value ? '******' : ''),
      }),
    )
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 登录锁定卡片（独立 endpoint，非通用分组） ====================
 * 门户 / Emby / 管理端三处密码登录共用的失败锁定（后端 SystemConfig 家族
 * login_lockout，#286 执法）：总开关默认开；账号维度窗口内失败达阈值
 * （默认 10 次 / 900 秒）锁定该账号、IP 维度达阈值（默认 30 次）锁定该 IP，
 * 锁定时长默认 900 秒；成功登录只清账号维度。 */
const loginLockout = reactive<LoginLockoutConfig>({
  enabled: true,
  account_max_failures: 10,
  ip_max_failures: 30,
  window_sec: 900,
  lock_sec: 900,
})
const loginLockoutOriginal = reactive<LoginLockoutConfig>({ ...loginLockout })
const loginLockoutLoading = ref(false)

function applyLoginLockout(data: LoginLockoutConfig) {
  loginLockout.enabled = data.enabled === true
  loginLockout.account_max_failures = data.account_max_failures ?? 10
  loginLockout.ip_max_failures = data.ip_max_failures ?? 30
  loginLockout.window_sec = data.window_sec ?? 900
  loginLockout.lock_sec = data.lock_sec ?? 900
  Object.assign(loginLockoutOriginal, loginLockout)
}

async function loadLoginLockout() {
  loginLockoutLoading.value = true
  try {
    applyLoginLockout(await fetchLoginLockoutConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    loginLockoutLoading.value = false
  }
}

const loginLockoutDirty = computed(
  () =>
    loginLockout.enabled !== loginLockoutOriginal.enabled ||
    loginLockout.account_max_failures !== loginLockoutOriginal.account_max_failures ||
    loginLockout.ip_max_failures !== loginLockoutOriginal.ip_max_failures ||
    loginLockout.window_sec !== loginLockoutOriginal.window_sec ||
    loginLockout.lock_sec !== loginLockoutOriginal.lock_sec,
)

/** 登录锁定保存核心：返回是否成功，失败时拦截器已提示。 */
async function saveLoginLockoutCore(): Promise<boolean> {
  try {
    applyLoginLockout(await updateLoginLockoutConfig({ ...loginLockout }))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 官网菜单抽屉卡片（独立 endpoint，非通用分组） ====================
 * 官网落地页移动端菜单抽屉的全部内容（后端 SystemConfig 单键
 * site_drawer_config，整份 JSON）：导航项、社区渠道卡片、分组标题、
 * 底部按钮。顺序即数组顺序（上下移就是改数组）；保存走后端整份
 * 结构校验，非法（空标题 / 未知图标 / 危险链接 / 超量）整份拒绝。 */
const DRAWER_ICON_LABELS: Record<string, string> = {
  home: '首页', tag: '标签', book: '文档', bell: '铃铛', users: '人群',
  bot: '机器人', message: '消息', send: '发送', globe: '地球', login: '登录',
}
const siteDrawer = reactive<SiteDrawerConfig>({
  nav_items: [],
  channels_title: '社区渠道',
  channels: [],
  cta: { label: '进入后台', url: '', enabled: true },
})
const siteDrawerOriginal = ref('')
const siteDrawerIcons = ref<string[]>(Object.keys(DRAWER_ICON_LABELS))
const siteDrawerLoading = ref(false)

function siteDrawerSnapshot(): SiteDrawerConfig {
  return JSON.parse(JSON.stringify({
    nav_items: siteDrawer.nav_items,
    channels_title: siteDrawer.channels_title,
    channels: siteDrawer.channels,
    cta: siteDrawer.cta,
  })) as SiteDrawerConfig
}

function applySiteDrawer(data: SiteDrawerConfigResponse) {
  const cfg = data.config
  siteDrawer.nav_items = (cfg.nav_items || []).map((i) => ({ ...i }))
  siteDrawer.channels_title = cfg.channels_title ?? '社区渠道'
  siteDrawer.channels = (cfg.channels || []).map((c) => ({ ...c }))
  siteDrawer.cta = { ...(cfg.cta || { label: '进入后台', url: '', enabled: true }) }
  if (Array.isArray(data.icon_options) && data.icon_options.length) {
    siteDrawerIcons.value = data.icon_options
  }
  siteDrawerOriginal.value = JSON.stringify(siteDrawerSnapshot())
}

async function loadSiteDrawer() {
  siteDrawerLoading.value = true
  try {
    applySiteDrawer(await fetchSiteDrawerConfig())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    siteDrawerLoading.value = false
  }
}

const siteDrawerDirty = computed(
  () => siteDrawerOriginal.value !== ''
    && JSON.stringify(siteDrawerSnapshot()) !== siteDrawerOriginal.value,
)

/** 官网菜单抽屉保存核心：返回是否成功，失败时拦截器已提示。 */
async function saveSiteDrawerCore(): Promise<boolean> {
  try {
    applySiteDrawer(await updateSiteDrawerConfig(siteDrawerSnapshot()))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

function drawerMove<T>(list: T[], idx: number, dir: -1 | 1) {
  const j = idx + dir
  if (j < 0 || j >= list.length) return
  const [item] = list.splice(idx, 1)
  list.splice(j, 0, item)
}

function addDrawerNav() {
  siteDrawer.nav_items.push({ icon: 'home', label: '', url: '', external: false, enabled: true })
}

function addDrawerChannel() {
  siteDrawer.channels.push({ icon: 'message', title: '', subtitle: '', url: '', enabled: true })
}

/** ==================== 隐私政策卡片（独立 endpoint，非通用分组） ====================
 * 用户端公开页 /privacy 的正文（后端 SystemConfig 单键
 * privacy_policy_md，整份 Markdown）。未存自定义正文时编辑器预填
 * 现行内置正文的 Markdown 转写起稿——起稿不落库，改完保存才生效；
 * 把正文清空再保存 = 删配置行，用户端回落内置正文。 */
const privacyMd = ref('')
const privacyOriginal = ref('')
const privacyHasCustom = ref(false)
const privacyLoaded = ref(false)
const privacyLoading = ref(false)

function applyPrivacyPolicy(data: PrivacyPolicyResponse) {
  privacyHasCustom.value = data.has_custom
  privacyMd.value = data.has_custom ? data.content : data.default_markdown
  privacyOriginal.value = privacyMd.value
  privacyLoaded.value = true
}

async function loadPrivacyPolicy() {
  privacyLoading.value = true
  try {
    applyPrivacyPolicy(await fetchPrivacyPolicy())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    privacyLoading.value = false
  }
}

const privacyPolicyDirty = computed(
  () => privacyLoaded.value && privacyMd.value !== privacyOriginal.value,
)

/** 隐私政策保存核心：只在真改动（dirty）时被 saveAll 调用，直接
 * 整份提交：清空正文提交空串，后端删行、用户端回落内置正文。 */
async function savePrivacyPolicyCore(): Promise<boolean> {
  try {
    applyPrivacyPolicy(await updatePrivacyPolicy(privacyMd.value))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/** ==================== 软件授权卡片（独立 endpoint，非通用分组） ====================
 * 发行版授权校验的运营面：看状态（档位/到期/绑定/宽限/到期提醒）、
 * 填授权码与绑定域名、立即校验。总开关 AETRIX_LICENSE_ENFORCE 走
 * env/发行包，不进后台（功能开关从简）；这里只展示它是否生效。
 * 保存走后端「保存即校验」，返回的就是最新状态。 */
const licenseForm = reactive({ code: '', domain: '' })
const licenseStatus = ref<LicenseStatus | null>(null)
const licenseLoading = ref(false)
const licenseSaving = ref(false)
const licenseVerifying = ref(false)

function applyLicenseStatus(data: LicenseStatus) {
  licenseStatus.value = data
  licenseForm.code = data.code || ''
  licenseForm.domain = data.domain || ''
}

async function loadLicense() {
  licenseLoading.value = true
  try {
    applyLicenseStatus(await fetchLicenseStatus())
  } catch {
    // 错误提示由 HTTP 拦截器统一处理
  } finally {
    licenseLoading.value = false
  }
}

async function saveLicense() {
  licenseSaving.value = true
  try {
    applyLicenseStatus(await saveLicenseConfig({
      // 掩码 ****** 原样回填 = 不改授权码（后端同口径兜底）；清空 = 清除
      ...(licenseForm.code.trim() === '******'
        ? {}
        : { code: licenseForm.code.trim() }),
      domain: licenseForm.domain.trim(),
    }))
    ElMessage.success('已保存，并完成一次校验')
  } catch {
    // 拦截器已提示
  } finally {
    licenseSaving.value = false
  }
}

async function verifyLicense() {
  licenseVerifying.value = true
  try {
    applyLicenseStatus(await verifyLicenseNow())
    ElMessage.success('已完成一次校验')
  } catch {
    // 拦截器已提示
  } finally {
    licenseVerifying.value = false
  }
}

const licenseStateText = computed(() => {
  const s = licenseStatus.value
  if (!s) return '读取中'
  if (!s.enforced) return '未启用校验'
  return { ok: '生效中', grace: '宽限中', blocked: '已拦截播放', disabled: '未启用校验' }[s.state] || s.state
})
const licenseStateBadge = computed(() => {
  const s = licenseStatus.value
  if (!s || !s.enforced) return 'au-badge-muted'
  if (s.state === 'ok') return 'au-badge-green'
  if (s.state === 'grace') return 'au-badge-amber'
  return 'au-badge-rose'
})
const licenseReminderText = computed(() => {
  const s = licenseStatus.value
  if (!s || !s.enforced) return ''
  if (s.state === 'grace') return '授权已到期，当前处于宽限期内：宽限期内播放照常，请尽快续费并重新校验。'
  if (s.reminder === '30') return `授权将在 ${s.days_left ?? 30} 天内到期，请提前续费。`
  if (s.reminder === '7') return `授权将在 ${s.days_left ?? 7} 天内到期，请尽快续费。`
  if (s.reminder === '1') return '授权即将到期（1 天内），请立即续费，过期后有 7 天宽限。'
  return ''
})

/** 运营参数页签顶部的分组跳转：分组多（注册 + 9 块 + 防盗链），先看目录再定位，不用一路滚 */
function scrollToGroup(id: string) {
  document.getElementById(`group-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/**
 * 「重新载入」：当前页签用得上的都重拉一遍。
 * 有未保存的改动时先弹确认——以前这里是静默丢弃：改了东西点一下重载，
 * 别的卡片里刚改的内容就被 load() 的 Object.assign 悄悄冲掉了。
 */
async function reloadAll() {
  if (anyDirty.value) {
    try {
      await ElMessageBox.confirm(
        `有 ${dirtyCount.value} 处改动还没保存，重新载入会把它们丢掉。`,
        '重新载入会丢弃未保存的改动',
        { type: 'warning', confirmButtonText: '继续载入', cancelButtonText: '再想想' },
      )
    } catch {
      return
    }
  }
  loadCapabilities()
  load()
  loadHotlink()
  loadInstallApp()
  loadItemsDedupCache()
  loadMountServing()
  loadDriveApi()
  loadDeliveryPlan()
  loadSoftdecode()
  loadBotCfg()
  loadDanmaku()
  loadDanmuLines()
  loadLoginLockout()
  loadSiteDrawer()
  loadPrivacyPolicy()
}

function isDirty(group: Group): boolean {
  return group.fields.some(
    (f) => isSupported(f.key) && String(settings[f.key] ?? '') !== String(original.value[f.key] ?? ''),
  )
}

/** 高级折叠：每卡默认收起。el-collapse 的 v-model，按卡 id 存展开态。 */
const advancedOpen = reactive<Record<string, string[]>>({})
// 预置空数组：v-model 拿到 undefined 时 el-collapse 展开会异常，默认收起即 []
for (const g of GROUPS) {
  if (g.fields.some((f) => f.advanced)) advancedOpen[g.id] = []
}
const commonFields = (g: Group): Field[] => g.fields.filter((f) => !f.advanced)
const advancedFields = (g: Group): Field[] => g.fields.filter((f) => f.advanced)
const hasAdvanced = (g: Group): boolean => g.fields.some((f) => f.advanced)

/**
 * 全局脏检查：运营参数页签里"改了没存"的地方总数。
 * 经济分组 / 注册策略 / 防盗链三处各自独立 endpoint，这里只计数，
 * 保存时 saveAll 按顺序逐个调。
 */
const dirtyGroups = computed(() => GROUPS.filter((g) => isDirty(g)))
const dirtyCount = computed(() =>
  dirtyGroups.value.length + (regDirty.value ? 1 : 0) + (hotlinkDirty.value ? 1 : 0)
  + (itemsDedupCacheDirty.value ? 1 : 0) + (transferDirty.value ? 1 : 0) + (mountServingDirty.value ? 1 : 0)
  + (driveApiDirty.value ? 1 : 0) + (deliveryPlanDirty.value ? 1 : 0) + (softdecodeDirty.value ? 1 : 0) + (botCfgDirty.value ? 1 : 0) + (danmakuDirty.value ? 1 : 0) + (loginLockoutDirty.value ? 1 : 0) + (installAppDirty.value ? 1 : 0)
  + (siteDrawerDirty.value ? 1 : 0) + (privacyPolicyDirty.value ? 1 : 0),
)
const anyDirty = computed(() => dirtyCount.value > 0)
const savingAll = ref(false)

/** 经济分组保存核心：返回是否成功，失败时拦截器已提示。 */
async function saveGroupCore(group: Group): Promise<boolean> {
  // 载荷构造抽到 @/utils/buildEconomyPayload（纯函数，A-066/A-123 回归锚点）；
  // originals 用于区分「已配值被清空」（发空串删行回默认）与「从未配过」（省略）
  const payload = buildEconomyPayload(group.fields, settings, isSupported, original.value)
  try {
    await updateEconomySettings(payload)
    await refreshEconomy(Object.keys(payload))
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

const paymentReady = computed(() => {
  const url = String(settings.payment_gateway_url || '')
  const pid = String(settings.payment_partner_id || '')
  const keySet = String(settings.payment_partner_key || '') === '******'
  return Boolean(url && pid && keySet)
})

/** 注册策略保存核心：返回是否成功，校验失败/接口失败时已提示。 */
async function saveRegistrationCore(): Promise<boolean> {
  try {
    await regFormInstance()?.validate()
  } catch (e) {
    if (e instanceof Error) {
      // 程序错误（如 ref 形状异常）：不能和校验不通过一起静默吞掉
      console.error('注册策略表单校验异常', e)
      ElMessage.error('表单校验异常，请刷新页面后重试')
    }
    // 校验不通过时 Element Plus 的拒绝值是 invalidFields 普通对象：
    // 字段旁已有红字提示，保持静默返回（汇总横幅由 saveAll 给出）
    return false
  }
  // 注册限流前端校验（C5）——保留作为双保险
  const rlMax = Number.parseInt(String(reg.ratelimitMax), 10)
  const rlWindow = Number.parseInt(String(reg.ratelimitWindow), 10)
  if (!Number.isFinite(rlMax) || rlMax < 1) {
    ElMessage.error('窗口内最大注册次数必须大于等于 1')
    return false
  }
  if (!Number.isFinite(rlWindow) || rlWindow < 60) {
    ElMessage.error('限流窗口必须大于等于 60 秒')
    return false
  }
  try {
    await updateRegistrationSettings({
      mode: reg.mode,
      message: reg.message,
      ratelimit_max: rlMax,
      ratelimit_window: rlWindow,
    })
    snapshotReg()
    return true
  } catch {
    // 拦截器已提示
    return false
  }
}

/**
 * 全局保存：把运营参数页签里所有"改了没存"的地方一次存完。
 * 三处走各自的独立 endpoint（注册策略带表单校验 → 经济分组 → 防盗链带范围校验），
 * 某处失败不影响其它继续存，最后汇总报一次，不再逐个弹成功提示。
 */
async function saveAll() {
  if (!anyDirty.value || savingAll.value) return
  savingAll.value = true
  const failed: string[] = []
  let saved = 0
  try {
    if (regDirty.value) {
      if (await saveRegistrationCore()) saved += 1
      else failed.push('注册策略')
    }
    for (const g of dirtyGroups.value) {
      if (await saveGroupCore(g)) saved += 1
      else failed.push(g.title)
    }
    if (hotlinkDirty.value) {
      if (await saveHotlinkCore()) saved += 1
      else failed.push('防盗链')
    }
    if (installAppDirty.value) {
      if (await saveInstallAppCore()) saved += 1
      else failed.push('安装应用')
    }
    if (itemsDedupCacheDirty.value) {
      if (await saveItemsDedupCacheCore()) saved += 1
      else failed.push('列表去重缓存')
    }
    if (transferDirty.value) {
      if (await saveTransferCore()) saved += 1
      else failed.push('转存')
    }
    if (mountServingDirty.value) {
      if (await saveMountServingCore()) saved += 1
      else failed.push('挂载直读')
    }
    if (driveApiDirty.value) {
      if (await saveDriveApiCore()) saved += 1
      else failed.push('Drive API 限速')
    }
    if (deliveryPlanDirty.value) {
      if (await saveDeliveryPlanCore()) saved += 1
      else failed.push('交付计划')
    }
    if (softdecodeDirty.value) {
      if (await saveSoftdecodeCore()) saved += 1
      else failed.push('网页软解')
    }
    if (botCfgDirty.value) {
      if (await saveBotCfgCore()) saved += 1
      else failed.push('机器人设置')
    }
    if (danmakuDirty.value) {
      if (await saveDanmakuCore()) saved += 1
      else failed.push('弹幕设置')
    }
    if (danmuLinesDirty.value) {
      if (await saveDanmuLinesCore()) saved += 1
      else failed.push('弹幕 API 线路')
    }
    if (loginLockoutDirty.value) {
      if (await saveLoginLockoutCore()) saved += 1
      else failed.push('登录锁定')
    }
    if (siteDrawerDirty.value) {
      if (await saveSiteDrawerCore()) saved += 1
      else failed.push('官网菜单抽屉')
    }
    if (privacyPolicyDirty.value) {
      if (await savePrivacyPolicyCore()) saved += 1
      else failed.push('隐私政策')
    }
  } finally {
    savingAll.value = false
  }
  if (failed.length) {
    ElMessage.error(`「${failed.join('、')}」没存上，其他 ${saved} 处已保存，可再点一次全部保存重试`)
  } else {
    ElMessage.success(`已全部保存（${saved} 处）`)
  }
}

/**
 * 放弃：把运营参数页签里所有未保存的改动丢掉，回到服务端当前值。
 * load() 会同时重置经济分组与注册策略，loadHotlink() 重置防盗链，
 * loadItemsDedupCache() 重置列表去重缓存，loadTransfer() 重置转存，loadMountServing() 重置挂载直读。
 */
async function discardAll() {
  if (!anyDirty.value) return
  try {
    await ElMessageBox.confirm(
      '放弃后，你刚才改的、还没保存的内容都会丢掉，页面回到服务端当前的值。',
      '放弃未保存的改动？',
      { type: 'warning', confirmButtonText: '放弃改动', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  load()
  loadHotlink()
  loadInstallApp()
  loadItemsDedupCache()
  loadTransfer()
  loadMountServing()
  loadDriveApi()
  loadDeliveryPlan()
  loadSoftdecode()
  loadBotCfg()
  loadDanmaku()
  loadDanmuLines()
  loadLoginLockout()
  loadSiteDrawer()
  loadPrivacyPolicy()
}

// ==================== 危险操作（Phase 5） ====================

/** 只读审计角色：服务端会拒，这里先置灰并说清原因（口径同导航上的锁标记） */
const canWrite = computed(() => auth.admin?.can_write !== false)
const WRITE_HINT = '当前账号是只读审计角色：可以看，不能执行写操作'

/** 逻辑与确认文案都在 useDangerOps 里（仪表盘 / 客户端策略 / 登录日志共用同一份） */
const dangerOps = useDangerOps()
const {
  busy: dangerBusy,
  purgeLogs: doPurgeLogs,
} = dangerOps

/** 保留天数：0 = 清空全部登录与安全日志 */
const purgeDays = ref(90)
/** 从别的页面深链过来时高亮对应卡片（?op=logs） */
const TAB_OPS = ['logs']
const target = ref('')

/** 深链定位：?op=xxx → 高亮对应卡片并滚过去（其它页面「去危险操作」的落点） */
function scrollToTarget(key: string) {
  document.getElementById(`danger-${key}`)?.scrollIntoView({ block: 'center' })
}

watch(
  () => route.query.op,
  (op) => {
    const key = Array.isArray(op) ? (op[0] ?? '') : (op ?? '')
    target.value = TAB_OPS.includes(key) ? key : ''
    if (!target.value) return
    nextTick(() => scrollToTarget(target.value))
    // el-tabs 的页签内容是懒渲染的：首次切过去时 DOM 可能还没挂上，滚不到就再试一次
    window.setTimeout(() => scrollToTarget(target.value), 150)
    // 高亮只用来「指给你看这是哪一张」，停几秒就淡掉（否则一直亮着反而像出错了）
    window.setTimeout(() => { target.value = '' }, 4000)
  },
  { immediate: true },
)
</script>

<template>
  <div class="admin-page settings-page">
    <PageHeader
      eyebrow="系统"
      title="系统设置"
      description="外部服务能力、运营参数与危险操作；修改后立即对用户端生效。"
    >
      <template #actions>
        <el-button @click="reloadAll">
          <RefreshCw :size="14" class="btn-ico" />重新载入
        </el-button>
      </template>
    </PageHeader>

    <!-- Phase 5：日常要改的放前两个页签，不可撤销的独立一页 -->
    <el-tabs v-model="tab" class="settings-tabs">
      <el-tab-pane name="capabilities">
        <template #label>
          <span class="tab-label"><Globe :size="13" />能力与服务</span>
        </template>

        <div class="settings-body">
          <!-- ==================== 外部服务能力 ==================== -->
          <NoticePanel
            title="外部服务能力说明"
            summary="凭据由管理员自行填写，填入后即可测试连接"
            :icon="Zap"
            storage-key="settings-capabilities"
          >
            <p class="capability-note">
              能力由面板提供，<strong>凭据全部由你自己填</strong>：各家的 API Key / 站点密钥 / 代理地址 / Bot Token
              都由本项目之外的账号体系签发，填进来即可用，每个能力都能当场「测试连接」。
            </p>
          </NoticePanel>

          <div v-if="capsLoading && !capabilities.length" class="cap-skeleton" aria-busy="true" aria-label="加载中">
            <div v-for="n in 6" :key="n" class="au-skeleton sk-cap" />
          </div>

          <EmptyState
            v-else-if="capsError"
            :icon="PlugZap"
            title="能力列表读取失败"
            description="可能是网络或后端暂时不可用。"
          >
            <template #actions><el-button size="small" @click="loadCapabilities">重试</el-button></template>
          </EmptyState>

          <EmptyState
            v-else-if="!capabilities.length"
            :icon="PlugZap"
            title="暂无可配置的外部能力"
            description="后端没有下发任何能力项。"
          />

          <div v-else class="cap-flow" :class="{ 'is-refreshing': capsLoading }">
            <section v-for="g in capGroups" :key="g.name" class="cap-section">
              <h2 class="au-eyebrow cap-group-title">{{ g.name }}</h2>
              <div class="cap-grid">
                <button
                  v-for="card in g.items"
                  :key="card.slug"
                  type="button"
                  class="cap-card"
                  :class="capStatus(card).tone"
                  @click="openCapability(card.slug)"
                >
                  <div class="cap-card-top">
                    <span class="cap-icon">
                      <component :is="CAP_ICONS[card.slug] || Globe" :size="18" />
                    </span>
                    <span
                      class="au-badge"
                      :class="capStatus(card).tone === 'ok' ? 'au-badge-green' : capStatus(card).tone === 'warn' ? 'au-badge-amber' : 'au-badge-muted'"
                    >{{ capStatus(card).label }}</span>
                  </div>
                  <h3 class="cap-title">{{ card.title }}</h3>
                  <p>{{ card.desc }}</p>
                  <span class="cap-more">配置与测试 →</span>
                </button>
              </div>
            </section>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane name="params">
        <template #label>
          <span class="tab-label"><SlidersHorizontal :size="13" />运营参数</span>
        </template>

        <div v-if="loading && !Object.keys(original).length" class="params-skeleton" aria-busy="true" aria-label="加载中">
          <div class="au-skeleton sk-nav" />
          <div v-for="n in 3" :key="n" class="au-skeleton sk-card" />
        </div>

        <div v-else class="settings-body" :class="{ 'is-refreshing': loading }">
          <el-alert
            v-if="paramsError"
            type="error"
            show-icon
            :closable="false"
            title="运营参数读取失败"
            description="下面的表单可能不是当前生效值，请先点右上角「重新载入」再修改。"
          />

          <!-- 分组目录：按三段归类一眼看全，带未保存标记，点击跳转；滚动时吸顶 -->
          <!-- 吸顶层与横向滚动层分离：sticky 元素自身不再是滚动容器，避免移动端吸顶态滚动原点偏移 -->
          <div class="group-nav-sticky">
          <!-- 全局保存条：运营参数页签里改了任何一处，只在这里点一次就全存（注册策略/经济分组/防盗链各自独立 endpoint，按顺序逐个存） -->
          <div v-if="anyDirty" class="savebar" role="status">
            <span class="savebar-text">有 <strong>{{ dirtyCount }}</strong> 处改动还没保存</span>
            <span class="savebar-actions">
              <el-button size="small" @click="discardAll">放弃</el-button>
              <el-button size="small" type="primary" :loading="savingAll" @click="saveAll">
                <Save :size="14" class="btn-ico" />全部保存
              </el-button>
            </span>
          </div>
          <nav ref="groupNavRef" class="group-nav" aria-label="运营参数分组">
            <div v-for="sec in SECTIONS" :key="sec.id" class="group-nav-sec">
              <span class="group-nav-label">{{ sec.title }}</span>
              <button
                v-if="sec.id === 'growth'"
                type="button"
                class="group-chip"
                @click="scrollToGroup('registration')"
              >
                <KeyRound :size="13" />注册策略
              </button>
              <button
                v-for="g in sec.groups"
                :key="g.id"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': isDirty(g) }"
                @click="scrollToGroup(g.id)"
              >
                <component :is="g.icon" :size="13" />{{ g.title }}
                <span v-if="hasAdvanced(g)" class="chip-adv" title="含高级调优项，默认收起">高级</span>
                <span v-if="isDirty(g)" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'bot'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': botCfgDirty }"
                @click="scrollToGroup('bot')"
              >
                <Bot :size="13" />机器人设置
                <span v-if="botCfgDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': hotlinkDirty }"
                @click="scrollToGroup('hotlink')"
              >
                <ShieldCheck :size="13" />防盗链
                <span v-if="hotlinkDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': installAppDirty }"
                @click="scrollToGroup('install-app')"
              >
                <Download :size="13" />安装应用
                <span v-if="installAppDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': itemsDedupCacheDirty }"
                @click="scrollToGroup('items-dedup-cache')"
              >
                <Zap :size="13" />列表缓存
                <span v-if="itemsDedupCacheDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': transferDirty }"
                @click="scrollToGroup('transfer')"
              >
                <RefreshCw :size="13" />转存
                <span v-if="transferDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': mountServingDirty }"
                @click="scrollToGroup('mount-serving')"
              >
                <HardDrive :size="13" />挂载直读
                <span v-if="mountServingDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': driveApiDirty }"
                @click="scrollToGroup('drive-api')"
              >
                <Gauge :size="13" />Drive 限速
                <span v-if="driveApiDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': deliveryPlanDirty }"
                @click="scrollToGroup('delivery-plan')"
              >
                <Network :size="13" />交付计划
                <span v-if="deliveryPlanDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': softdecodeDirty }"
                @click="scrollToGroup('softdecode')"
              >
                <Cpu :size="13" />网页软解
                <span v-if="softdecodeDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': danmakuDirty }"
                @click="scrollToGroup('danmaku')"
              >
                <MessageSquareText :size="13" />弹幕设置
                <span v-if="danmakuDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': danmuLinesDirty }"
                @click="scrollToGroup('danmu-api-lines')"
              >
                <Network :size="13" />弹幕 API 线路
                <span v-if="danmuLinesDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'playback'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': loginLockoutDirty }"
                @click="scrollToGroup('login-lockout')"
              >
                <Lock :size="13" />登录锁定
                <span v-if="loginLockoutDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'site'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': siteDrawerDirty }"
                @click="scrollToGroup('site-drawer')"
              >
                <PanelBottom :size="13" />官网菜单抽屉
                <span v-if="siteDrawerDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
              <button
                v-if="sec.id === 'site'"
                type="button"
                class="group-chip"
                :class="{ 'is-dirty': privacyPolicyDirty }"
                @click="scrollToGroup('privacy-policy')"
              >
                <ScrollText :size="13" />隐私政策
                <span v-if="privacyPolicyDirty" class="chip-dot" aria-label="有未保存的改动" />
              </button>
            </div>
          </nav>
          </div>

          <template v-for="sec in SECTIONS" :key="sec.id">
          <h2 :id="`section-${sec.id}`" class="au-eyebrow section-title">{{ sec.title }}</h2>

          <!-- 注册策略 -->
          <SectionCard
            v-if="sec.id === 'growth'"
            id="group-registration"
            title="注册策略"
            :icon="KeyRound"
            description="决定新用户如何进入站点"
            class="group-card"
          >
            <template v-if="regDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>
            <el-form ref="regFormRef" :model="reg" :rules="regRules" label-position="top" class="field-form">
              <el-form-item label="注册模式">
                <div class="field-stack">
                  <el-radio-group v-model="reg.mode">
                    <el-radio-button value="open">开放注册</el-radio-button>
                    <el-radio-button value="closed">关闭注册</el-radio-button>
                  </el-radio-group>
                  <span class="field-hint">{{ regModeHint }}</span>
                </div>
              </el-form-item>
              <el-form-item v-if="reg.mode === 'closed'" label="关闭提示" prop="message">
                <el-input v-model="reg.message" type="textarea" :rows="2" placeholder="展示给无法注册的用户…" />
              </el-form-item>
              <el-form-item label="窗口内最大注册次数" prop="ratelimitMax">
                <div class="field-stack">
                  <el-input-number v-model="reg.ratelimitMax" :min="1" :max="10000" :step="1" />
                  <span class="field-hint">同一 IP 在一个时间窗口内最多可注册次数（默认 5）</span>
                </div>
              </el-form-item>
              <el-form-item label="限流时间窗口（秒）" prop="ratelimitWindow">
                <div class="field-stack">
                  <el-input-number v-model="reg.ratelimitWindow" :min="60" :max="86400" :step="60" />
                  <span class="field-hint">滑动窗口长度，单位秒（默认 3600，即 1 小时）</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>

          <!-- 支付状态提示：放在「付费与支付」段首，紧挨着要配的网关字段 -->
          <el-alert
            v-if="sec.id === 'commerce'"
            :type="paymentReady ? 'success' : 'warning'"
            show-icon
            :closable="false"
            :title="paymentReady
              ? '支付网关已配置完成，用户端可直接下单充值。'
              : '支付网关尚未配置齐全（需要网关地址 + 商户 ID + 商户密钥），用户端充值下单会提示未启用。'"
          />

          <!-- 软件授权：独立卡片（状态展示 + 填码 + 立即校验，走独立 endpoint） -->
          <SectionCard
            v-if="sec.id === 'commerce'"
            id="group-license"
            title="软件授权"
            :icon="KeyRound"
            description="本机软件授权的状态与激活。校验只传授权码、机器指纹哈希、绑定域名与版本号，不传任何用户与片库数据；授权服务连不上时有 7 天宽限，不会突然停播。"
            class="group-card"
          >
            <template #actions>
              <span class="au-badge" :class="licenseStateBadge">{{ licenseStateText }}</span>
            </template>

            <div v-loading="licenseLoading" class="field-stack">
              <el-alert
                v-if="licenseStatus && !licenseStatus.enforced"
                type="info"
                show-icon
                :closable="false"
                title="当前部署未启用授权校验（AETRIX_LICENSE_ENFORCE 未开）：以下信息仅供查看与预填，播放不受任何影响。发行版默认启用。"
              />
              <el-alert
                v-if="licenseReminderText"
                :type="licenseStatus?.state === 'grace' ? 'error' : 'warning'"
                show-icon
                :closable="false"
                :title="licenseReminderText"
              />

              <div class="field-grid">
                <div class="field-stack">
                  <span class="field-hint">授权档位</span>
                  <strong>{{ licenseStatus?.tier || '—' }}</strong>
                </div>
                <div class="field-stack">
                  <span class="field-hint">到期时间（UTC）</span>
                  <strong>{{ licenseStatus?.expires_at || '—' }}</strong>
                </div>
                <div class="field-stack">
                  <span class="field-hint">上次校验</span>
                  <strong>
                    {{ licenseStatus?.last_checked_at || '尚未校验' }}
                    <span v-if="licenseStatus?.last_result" class="field-hint">（{{ licenseStatus.last_result }}）</span>
                  </strong>
                </div>
                <div class="field-stack">
                  <span class="field-hint">授权服务</span>
                  <strong>{{ licenseStatus?.server || '' }}</strong>
                </div>
              </div>

              <el-form label-position="top" class="field-form field-grid">
                <el-form-item label="授权码">
                  <el-input
                    v-model="licenseForm.code"
                    type="password"
                    show-password
                    placeholder="已配置时显示为 ******，输入新码才替换"
                    clearable
                  />
                </el-form-item>
                <el-form-item label="绑定域名">
                  <el-input
                    v-model="licenseForm.domain"
                    placeholder="如 media.example.com（只填域名，不要带 https://）"
                    clearable
                  />
                </el-form-item>
              </el-form>

              <div class="field-control">
                <el-button type="primary" :loading="licenseSaving" @click="saveLicense">
                  <Save :size="14" />保存并校验
                </el-button>
                <el-button :loading="licenseVerifying" @click="verifyLicense">
                  <RefreshCw :size="14" />立即校验
                </el-button>
                <span class="field-hint">换服务器后绑定对不上时，先联系授权提供方重置绑定，再在这里重新校验。</span>
              </div>
            </div>
          </SectionCard>

          <!-- 经济配置分组 -->
          <SectionCard
            v-for="g in sec.groups"
            :id="`group-${g.id}`"
            :key="g.id"
            :title="g.title"
            :icon="g.icon"
            :description="g.desc"
            :tone="isDirty(g) ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="isDirty(g)" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item
                v-for="f in commonFields(g)"
                :key="f.key"
                :label="f.label"
                :class="{ 'is-wide': f.type === 'str' || f.type === 'secret', 'is-unsupported': !isSupported(f.key) }"
              >
                <div class="field-stack">
                  <div class="field-control">
                    <el-switch
                      v-if="f.type === 'bool'"
                      v-model="settings[f.key]"
                      active-value="true"
                      inactive-value="false"
                      :disabled="!isSupported(f.key)"
                    />
                    <el-input
                      v-else-if="f.type === 'int'"
                      v-model="settings[f.key]"
                      type="number"
                      min="0"
                      class="num-input"
                      :disabled="!isSupported(f.key)"
                    />
                    <el-select
                      v-else-if="f.choices"
                      v-model="settings[f.key]"
                      class="w-full"
                      :disabled="!isSupported(f.key)"
                    >
                      <el-option
                        v-for="c in f.choices"
                        :key="c.value"
                        :label="c.label"
                        :value="c.value"
                      />
                    </el-select>
                    <el-input
                      v-else
                      v-model="settings[f.key]"
                      :type="f.type === 'secret' ? 'password' : 'text'"
                      :show-password="f.type === 'secret'"
                      :placeholder="f.type === 'secret' ? '留空表示不修改' : ''"
                      :disabled="!isSupported(f.key)"
                    />
                    <span v-if="f.suffix" class="field-suffix">{{ f.suffix }}</span>
                  </div>
                  <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
                  <span v-if="!isSupported(f.key)" class="field-hint warn">
                    当前后端未开放此项的写入（不在 /economy/settings 返回里），保存会被忽略，已置灰。
                  </span>
                </div>
              </el-form-item>
            </el-form>

            <!-- 高级调优：默认收起。收的是"调了也不影响主流程"的参数（去重窗口/超时/分块大小/惩罚幅度等），总开关与日常要改的核心值永远留在外面 -->
            <el-collapse v-if="hasAdvanced(g)" v-model="advancedOpen[g.id]" class="advanced-collapse">
              <el-collapse-item name="adv">
                <template #title>
                  <span class="advanced-title">高级调优<span class="advanced-sub">（一般不用动）</span></span>
                </template>
                <el-form label-position="top" class="field-form field-grid">
                  <el-form-item
                    v-for="f in advancedFields(g)"
                    :key="f.key"
                    :label="f.label"
                    :class="{ 'is-wide': f.type === 'str' || f.type === 'secret', 'is-unsupported': !isSupported(f.key) }"
                  >
                    <div class="field-stack">
                      <div class="field-control">
                        <el-switch
                          v-if="f.type === 'bool'"
                          v-model="settings[f.key]"
                          active-value="true"
                          inactive-value="false"
                          :disabled="!isSupported(f.key)"
                        />
                        <el-input
                          v-else-if="f.type === 'int'"
                          v-model="settings[f.key]"
                          type="number"
                          min="0"
                          class="num-input"
                          :disabled="!isSupported(f.key)"
                        />
                        <el-select
                          v-else-if="f.choices"
                          v-model="settings[f.key]"
                          class="w-full"
                          :disabled="!isSupported(f.key)"
                        >
                          <el-option
                            v-for="c in f.choices"
                            :key="c.value"
                            :label="c.label"
                            :value="c.value"
                          />
                        </el-select>
                        <el-input
                          v-else
                          v-model="settings[f.key]"
                          :type="f.type === 'secret' ? 'password' : 'text'"
                          :show-password="f.type === 'secret'"
                          :placeholder="f.type === 'secret' ? '留空表示不修改' : ''"
                          :disabled="!isSupported(f.key)"
                        />
                        <span v-if="f.suffix" class="field-suffix">{{ f.suffix }}</span>
                      </div>
                      <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
                      <span v-if="!isSupported(f.key)" class="field-hint warn">
                        当前后端未开放此项的写入（不在 /economy/settings 返回里），保存会被忽略，已置灰。
                      </span>
                    </div>
                  </el-form-item>
                </el-form>
              </el-collapse-item>
            </el-collapse>
          </SectionCard>

          <!-- 机器人设置：独立卡片（走 /api/admin/welfare/config 独立 endpoint），自成一段 -->
          <SectionCard
            v-if="sec.id === 'bot'"
            id="group-bot"
            title="机器人设置"
            :icon="Bot"
            description="TG 机器人的播报、排行榜、通知与群治理行为开关。机器人本体与令牌在「能力与服务」页签的 Telegram 卡里，这里只管行为。"
            :tone="botCfgDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="botCfgDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <div v-loading="botCfgLoading">
              <div v-for="bg in BOT_FIELD_GROUPS" :key="bg.id" class="bot-subgroup">
                <div class="bot-subgroup-title">{{ bg.title }}</div>
                <el-form label-position="top" class="field-form field-grid">
                  <el-form-item
                    v-for="f in bg.fields"
                    :key="f.key"
                    :label="f.label"
                    :class="{ 'is-wide': f.type === 'str' }"
                  >
                    <div class="field-stack">
                      <div class="field-control">
                        <el-switch
                          v-if="f.type === 'bool'"
                          v-model="botCfg[f.key]"
                          active-value="true"
                          inactive-value="false"
                        />
                        <el-input
                          v-else-if="f.type === 'int'"
                          v-model="botCfg[f.key]"
                          type="number"
                          min="0"
                          class="num-input"
                        />
                        <el-select
                          v-else-if="f.choices"
                          v-model="botCfg[f.key]"
                          class="w-full"
                        >
                          <el-option
                            v-for="c in f.choices"
                            :key="c.value"
                            :label="c.label"
                            :value="c.value"
                          />
                        </el-select>
                        <el-input
                          v-else
                          v-model="botCfg[f.key]"
                        />
                        <span v-if="f.suffix" class="field-suffix">{{ f.suffix }}</span>
                      </div>
                      <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
                    </div>
                  </el-form-item>
                </el-form>
              </div>
            </div>
          </SectionCard>

          <!-- 防盗链：独立卡片（TTL 需要范围校验，走独立 endpoint），归在「播放与风控」段末 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-hotlink"
            title="防盗链"
            :icon="ShieldCheck"
            description="播放签名有效期与来路检查。播放链接带 15 分钟签名与来路检查，防止盗链（总开关由服务端配置控制）。"
            :tone="hotlinkDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="hotlinkDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="播放签名有效期">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="hotlink.play_sign_ttl"
                      :min="60"
                      :max="86400"
                      :step="60"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">秒</span>
                  </div>
                  <span class="field-hint">uid/exp/sign 签名有效期，默认 900（15 分钟），范围 60~86400</span>
                </div>
              </el-form-item>
              <el-form-item label="Referer 白名单">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input
                      v-model="hotlink.allowed_referers"
                      type="textarea"
                      :rows="2"
                      placeholder="如：cdn.example.com, player.example.com"
                      style="max-width: 420px"
                    />
                  </div>
                  <span class="field-hint">逗号分隔的域名；只有带 Referer 且主机不在名单里才会被 403。留空 = 不检查（放行所有）</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>

          <!-- 安装应用：独立卡片（下载链接走独立 endpoint），归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-install-app"
            title="安装应用"
            :icon="Download"
            description="用户端「安装应用」页里 6 个平台的官方客户端下载链接，改完保存即生效。没有默认链接：留空的平台用户端整行不显示，填上官方链接保存后才出现下载入口。"
            :tone="installAppDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="installAppDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form v-loading="installAppLoading" label-position="top" class="field-form field-grid">
              <el-form-item
                v-for="p in installAppPlatforms"
                :key="p.key"
                :label="`${p.label}（${p.devices}）`"
              >
                <div class="field-stack">
                  <div class="field-control">
                    <el-input
                      v-model="installAppUrls[p.key]"
                      placeholder="https://…"
                      style="max-width: 420px"
                      clearable
                    />
                  </div>
                  <span class="field-hint">
                    <template v-if="p.configured">已配置，用户端显示该行；清空保存 = 取消配置，用户端整行消失</template>
                    <template v-else>未配置，用户端不显示该行；填上官方链接保存后即出现下载入口</template>
                  </span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>

          <!-- 列表去重缓存：独立卡片，归「播放与风控」段（左侧导航入口
               也是这么挂的）。此前漏写 v-if，在每个段落里各渲染了一份
               （全页共 4 份重复卡），2026-10-23 修复 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-items-dedup-cache"
            title="列表去重缓存"
            :icon="Zap"
            description="列表接口去重结果的服务端缓存：命中时跳过约 0.6 秒的去重计算。数据版本号 + 有效期双保险，库数据变化后最多旧一个有效期。"
            :tone="itemsDedupCacheDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="itemsDedupCacheDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="缓存有效期">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="itemsDedupCache.ttl_seconds"
                      :min="10"
                      :max="3600"
                      :step="10"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">秒</span>
                  </div>
                  <span class="field-hint">同一查询多久内直接给缓存结果，默认 60。库数据变化靠版本号即时失效，这里是第二道保险</span>
                </div>
              </el-form-item>
              <el-form-item label="缓存条目上限">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="itemsDedupCache.max_entries"
                      :min="100"
                      :max="20000"
                      :step="100"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">条</span>
                  </div>
                  <span class="field-hint">内存有界：超限时淘汰最久未访问的条目，默认 2000</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>

          <!-- 转存：独立卡片，归「播放与风控」段。跨 provider 总开关
               关掉后错配点播明示播不了（不回落共享），只改写口超管。 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-transfer"
            title="转存"
            :icon="RefreshCw"
            description="用户副本转存的调度：跨 provider 转存开关与并发上限。跨单（绑的盘与片源盘不一致）要经服务器分段搬字节，是分钟级慢单，单独限在途数，不许占满转存道。"
            :tone="transferDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="transferDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="跨 provider 转存">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ transfer.cross_provider_transfer_enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">开：只绑 115 的用户播谷歌盘的片（反之亦然）会先转存到他绑的盘再播；关：错配点播明示播不了，同 provider 转存不受影响。默认开</span>
                </div>
              </el-form-item>
              <el-form-item label="跨 provider 在途上限">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_cross_lane_concurrency"
                      :min="1"
                      :max="8"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">条</span>
                  </div>
                  <span class="field-hint">同时搬字节的跨单最多几条，其余道宽留给同 provider 的点播转存，默认 2</span>
                </div>
              </el-form-item>
              <el-form-item label="剧集预热">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ transfer.transfer_series_preheat_enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">开：播完一集后给同季后续集建预热（先只探针、强信号才搬字节）；关：播完不预热。默认开</span>
                </div>
              </el-form-item>
              <el-form-item label="剧集预热搬字节日帽">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_series_preheat_daily_max"
                      :min="1"
                      :max="16"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">条/人/日</span>
                  </div>
                  <span class="field-hint">强信号时每人每天最多建几张搬字节预热单，与详情页预转存的日帽分开计，默认 2</span>
                </div>
              </el-form-item>
              <el-form-item label="剧集预热候选集数">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_series_preheat_next_count"
                      :min="1"
                      :max="10"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">集</span>
                  </div>
                  <span class="field-hint">播完一集后给同季后续几集建预热，默认 3</span>
                </div>
              </el-form-item>
              <el-form-item label="跨单排队提权阈值">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_cross_aging_sec"
                      :min="60"
                      :max="86400"
                      :step="60"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">秒</span>
                  </div>
                  <span class="field-hint">跨盘搬运单排队超过这个时间还没被领走，就自动按点播单的优先级参与挑选，防无限期饿死，默认 1800 秒（30 分钟）</span>
                </div>
              </el-form-item>
              <el-form-item label="转存道并发">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_fast_lane_concurrency"
                      :min="1"
                      :max="32"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">条</span>
                  </div>
                  <span class="field-hint">转存工人一轮最多同时在飞的任务总数，默认 8</span>
                </div>
              </el-form-item>
              <el-form-item label="单用户在飞上限">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_per_owner_inflight"
                      :min="1"
                      :max="16"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">条</span>
                  </div>
                  <span class="field-hint">同一用户同时在飞的转存任务上限，防一人灌单饿死别人，默认 2</span>
                </div>
              </el-form-item>
              <el-form-item label="类别级熔断（停派）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input
                      v-model="transfer.transfer_paused_categories"
                      type="textarea"
                      :rows="3"
                      placeholder='[{"provider": "drive", "error_kind": "*"}]'
                    />
                  </div>
                  <span class="field-hint">按「源 provider＋失败类别」暂停派发：JSON 数组，每条 {"provider": "drive/pan115/quark", "error_kind": "失败类别，* 表示全部类别"}；命中的任务留在排队中不执行、不计次，改回 [] 即恢复派发。默认 [] 不暂停</span>
                </div>
              </el-form-item>
              <el-form-item label="换绑放行窗口">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_rebind_waiver_window_days"
                      :min="1"
                      :max="365"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">天</span>
                  </div>
                  <span class="field-hint">用户换绑网盘后，旧盘的转存记录不再挡新盘建单，这份放行按这个窗口限频，默认 7 天</span>
                </div>
              </el-form-item>
              <el-form-item label="换绑放行次数">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="transfer.transfer_rebind_waiver_max_uses"
                      :min="1"
                      :max="20"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">次</span>
                  </div>
                  <span class="field-hint">窗口内每人最多用几次换绑放行（按人计、不按片计），用完回落普通 24 小时节流，默认 1 次</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>

          <!-- 挂载直读出流：独立卡片，归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-mount-serving"
            title="挂载直读出流"
            :icon="HardDrive"
            description="白名单内的共享盘原件改走本地挂载文件出流，绕开公开下载链的额度风控与中继交付不稳定。白名单为空 = 不介入，行为与现行一致；清空名单即一步回滚。"
            :tone="mountServingDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="mountServingDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="总开关">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ mountServing.enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">关掉 = 挂载直读一行不走，完整回退现行出法</span>
                </div>
              </el-form-item>
              <el-form-item label="白名单（挂载 id）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input v-model="mountServing.mount_ids" placeholder="如 3" />
                  </div>
                  <span class="field-hint">逗号分隔的 storage_mounts id；留空 = 不介入任何挂载</span>
                </div>
              </el-form-item>
              <el-form-item label="本地根映射">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input v-model="mountServing.local_roots" placeholder="如 3=/mnt/gdrive-direct" />
                  </div>
                  <span class="field-hint">格式 id=/绝对路径，逗号分隔；须与白名单同时命中才生效，挂载文件缺失会明确报错、不静默换源</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- Drive API 限速：独立卡片，归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-drive-api"
            title="Drive API 限速"
            :icon="Gauge"
            description="扫描列举与 Changes 增量轮询共用的 Drive API 全局请求速率上限。调高能加快扫描，也会更贴近谷歌配额；保存后本页所在进程立即生效，其余进程在一分多钟内陆续跟上。同卡管理谷歌 Drive 绑定开关（默认关）。"
            :tone="driveApiDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="driveApiDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="全局速率上限">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="driveApi.qps"
                      :min="1"
                      :max="20"
                      :step="1"
                      controls-position="right"
                      class="num-input"
                    />
                    <span class="field-suffix">次/秒</span>
                  </div>
                  <span class="field-hint">全部 Drive API 请求合计不超过这个速率，默认 8。谷歌 per-user 配额约 10 次/秒，实际靠 SA 池轮换分摊到多个账号</span>
                </div>
              </el-form-item>

            </el-form>
          </SectionCard>
          <!-- 播放交付计划：独立卡片，归「播放与风控」段（O-02 补缺） -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-delivery-plan"
            title="播放交付计划"
            :icon="Network"
            description="播放交付编排器：起播前先算一份交付计划（走哪条出流腿、副本是否就绪），计划在 TTL 内缓存复用，避免每次起播重复探测。关掉 = 不编排，播放回落现行直出路径。保存后会回读一次核对落库值。"
            :tone="deliveryPlanDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="deliveryPlanDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="编排器总开关">
                <div class="field-stack">
                  <div class="field-control">
                    <el-switch v-model="deliveryPlan.enabled" active-text="开" inactive-text="关" />
                  </div>
                  <span class="field-hint">关掉 = 交付计划一行不算，完整回退现行出法</span>
                </div>
              </el-form-item>
              <el-form-item label="计划 TTL（秒）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number v-model="deliveryPlan.ttl_sec" :min="30" :max="3600" :step="30" />
                  </div>
                  <span class="field-hint">30~3600，默认 600。越大探测越省、计划越可能过期；越小越新鲜、起播探测越频</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- 网页软解：独立卡片，归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-softdecode"
            title="网页软解"
            :icon="Cpu"
            description="浏览器硬解失败时的站内软解兜底：只在网页播放路径生效，解码器是站内自编的 LGPL wasm（/wasm/decode/），不走第三方。默认关：关时浏览器解不了就出解码提示卡；开时先试软解，软解失败仍回提示卡，不黑屏。仅支持 HEVC / H.264 画面与 AAC / AC3 / EAC3 / DTS 声音，4K / HDR 不承诺。"
            :tone="softdecodeDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="softdecodeDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="网页软解开关">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ softdecode.enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">开：浏览器解不了的版本改走站内软解；关：保持现状，出解码提示卡。默认关</span>
                </div>
              </el-form-item>
              <el-form-item label="开源许可">
                <div class="field-stack">
                  <a class="license-link" href="/licenses" target="_blank" rel="noopener">在本站查看开源许可页</a>
                  <span class="field-hint">软解用的自编 LGPL 解码器与第三方组件的许可说明。</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- 弹幕设置：独立卡片，归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-danmaku"
            title="弹幕设置"
            :icon="MessageSquareText"
            description="网页播放器的站内弹幕：观众在播放页发弹幕、按时间点飘过画面。默认关：关时播放器不显示弹幕入口，发送与拉取接口一律关闭；开启后下面三项（敏感词、发送间隔、字数上限）立即对新发送生效。"
            :tone="danmakuDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="danmakuDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="弹幕开关">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ danmaku.enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">开：播放器出现弹幕按钮与发送框；关：入口隐藏、接口关闭。默认关</span>
                </div>
              </el-form-item>
              <el-form-item label="敏感词表">
                <div class="field-stack">
                  <el-input
                    v-model="danmaku.blocked_words"
                    type="textarea"
                    :rows="3"
                    placeholder="用逗号分隔，如：词一，词二"
                  />
                  <span class="field-hint">发送时命中任意一词（不分大小写）直接拒绝并提示发送者；留空 = 不过滤</span>
                </div>
              </el-form-item>
              <el-form-item label="最小发送间隔（秒）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="danmaku.min_interval_seconds"
                      :min="0"
                      :max="3600"
                      :step="1"
                      controls-position="right"
                    />
                  </div>
                  <span class="field-hint">同一用户两条弹幕之间的最短间隔；0 = 不限制。默认 5 秒</span>
                </div>
              </el-form-item>
              <el-form-item label="单条字数上限">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="danmaku.max_text_length"
                      :min="1"
                      :max="500"
                      :step="10"
                      controls-position="right"
                    />
                  </div>
                  <span class="field-hint">一条弹幕最多几个字，超出发送时被拒。默认 100 字</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- 弹幕 API 线路：独立卡片，归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-danmu-api-lines"
            title="弹幕 API 线路"
            :icon="Network"
            description="给第三方播放器填的弹幕 API 地址可选的线路：用户在概览页生成弹幕 Token、选一条线路，地址就是这条线路的域名加上固定路径和他的 Token——线路只换域名。改完保存立即对新读取生效；删掉一条线路后，已选该线路的用户会回到未选状态。至少保留一条。每行开关可暂停一条线路：暂停后用户端仍能看到这条线路并标注「暂停服务」，但选不了、也拼不出地址。下方外部弹幕源开启后，播放器来取弹幕时后端会去上游（LogVar 弹幕 API）匹配取回、与站内弹幕合并下发；上游地址只存在这里，用户看不到。"
            :tone="danmuLinesDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="danmuLinesDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form">
              <el-form-item label="线路列表">
                <div class="field-stack sd-stack">
                  <div v-for="(line, idx) in danmuLines" :key="idx" class="sd-row">
                    <el-input v-model="line.id" class="dl-id" placeholder="ID，如 intl" maxlength="32" />
                    <el-input v-model="line.name" class="dl-name" placeholder="名称，如 国际方向" maxlength="40" />
                    <el-input v-model="line.note" class="dl-note" placeholder="说明（可空），如 CDN 加速线路" maxlength="80" />
                    <el-input v-model="line.host" class="dl-host" placeholder="域名，如 cdn.example.com" maxlength="253" />
                    <span class="dl-toggle">
                      <el-switch v-model="line.enabled" />
                      <span class="field-suffix">{{ line.enabled ? '启用' : '暂停服务' }}</span>
                    </span>
                    <span class="sd-ops">
                      <el-button size="small" type="danger" plain @click="danmuLines.splice(idx, 1)">删除</el-button>
                    </span>
                  </div>
                  <div>
                    <el-button size="small" @click="addDanmuLine">＋ 新增线路</el-button>
                    <span class="field-hint">ID 只用小写字母、数字、连字符、下划线（1–32 位）且不能重复；域名只填裸域名，不带 https://、路径和端口。保存时整批校验，有一条不合法整批不存。</span>
                  </div>
                </div>
              </el-form-item>
              <el-form-item label="外部弹幕源">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ danmuExternal.enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">开：取弹幕时去上游匹配、与站内弹幕合并下发；上游超时或没匹配上就只发站内弹幕，不影响播放。默认关</span>
                </div>
              </el-form-item>
              <el-form-item label="外部源地址">
                <div class="field-stack">
                  <el-input
                    v-model="danmuExternal.url"
                    :placeholder="danmuExternalConfigured ? '已配置，留空表示不修改；输入新地址可替换' : '如 https://dm.example.com/令牌路径'"
                    maxlength="512"
                    clearable
                  />
                  <span v-if="danmuExternalConfigured" class="field-hint">已配置（地址不回显）：留空保存表示不修改，输入新地址则替换。</span>
                  <span v-else class="field-hint">上游 LogVar 弹幕 API 的 base 地址（含它的令牌路径），只存在后台、用户不可见；留空 = 未配置，开关打开也不会去取。</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- 登录锁定：独立卡片，归「播放与风控」段 -->
          <SectionCard
            v-if="sec.id === 'playback'"
            id="group-login-lockout"
            title="登录锁定"
            :icon="Lock"
            description="门户、Emby、管理端三处密码登录共用的失败锁定：有人反复试密码时，先锁账号、再锁 IP。关掉总开关后三处登录都不再锁定（只剩每分钟请求数限流兜底），非必要别关；改完下面四项立即对新登录生效。"
            :tone="loginLockoutDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="loginLockoutDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form field-grid">
              <el-form-item label="锁定总开关">
                <div class="field-stack">
                  <div class="field-control">
                    <span class="au-badge">{{ loginLockout.enabled ? '已开启' : '已关闭' }}</span>
                  </div>
                  <span class="field-hint">开：失败到阈值就锁；关：三处登录都不锁定。默认开</span>
                </div>
              </el-form-item>
              <el-form-item label="账号失败阈值（次）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="loginLockout.account_max_failures"
                      :min="1"
                      :max="1000"
                      :step="1"
                      controls-position="right"
                    />
                  </div>
                  <span class="field-hint">窗口内同一账号密码错这么多次就锁该账号。默认 10 次</span>
                </div>
              </el-form-item>
              <el-form-item label="IP 失败阈值（次）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="loginLockout.ip_max_failures"
                      :min="1"
                      :max="1000"
                      :step="1"
                      controls-position="right"
                    />
                  </div>
                  <span class="field-hint">窗口内同一 IP 密码错这么多次就锁该 IP（防换号撞库）。默认 30 次</span>
                </div>
              </el-form-item>
              <el-form-item label="计数窗口（秒）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="loginLockout.window_sec"
                      :min="60"
                      :max="86400"
                      :step="60"
                      controls-position="right"
                    />
                  </div>
                  <span class="field-hint">只数窗口内的失败，超出窗口的旧失败不累计。默认 900 秒（15 分钟）</span>
                </div>
              </el-form-item>
              <el-form-item label="锁定时长（秒）">
                <div class="field-stack">
                  <div class="field-control">
                    <el-input-number
                      v-model="loginLockout.lock_sec"
                      :min="60"
                      :max="86400"
                      :step="60"
                      controls-position="right"
                    />
                  </div>
                  <span class="field-hint">锁上后多久自动解开，期间正确密码也进不去。默认 900 秒（15 分钟）</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- 官网菜单抽屉：独立卡片，归「站点与官网」段 -->
          <SectionCard
            v-if="sec.id === 'site'"
            id="group-site-drawer"
            title="官网菜单抽屉"
            :icon="PanelBottom"
            description="官网落地页手机端点开菜单时滑出的抽屉，内容全部在这里配：导航项、社区渠道卡片、底部按钮。顺序就是下面的排列（用上下箭头调）；关掉某条的开关它就不再下发；渠道一条都不剩时整个分组在抽屉里不出现。链接只接受 http(s) 外链或站内 / 路径，渠道只接受 http(s) 外链。"
            :tone="siteDrawerDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="siteDrawerDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>

            <el-form label-position="top" class="field-form">
              <el-form-item label="导航项">
                <div class="field-stack sd-stack">
                  <div v-for="(item, idx) in siteDrawer.nav_items" :key="idx" class="sd-row">
                    <el-select v-model="item.icon" class="sd-icon" placeholder="图标">
                      <el-option
                        v-for="opt in siteDrawerIcons"
                        :key="opt"
                        :value="opt"
                        :label="DRAWER_ICON_LABELS[opt] || opt"
                      />
                    </el-select>
                    <el-input v-model="item.label" class="sd-label" placeholder="文字，如：首页" maxlength="40" />
                    <el-input v-model="item.url" class="sd-url" placeholder="链接：/ 站内路径或 https:// 外链" maxlength="500" />
                    <label class="sd-check"><el-checkbox v-model="item.external" />外链</label>
                    <el-switch v-model="item.enabled" />
                    <span class="sd-ops">
                      <el-button size="small" :disabled="idx === 0" @click="drawerMove(siteDrawer.nav_items, idx, -1)">↑</el-button>
                      <el-button size="small" :disabled="idx === siteDrawer.nav_items.length - 1" @click="drawerMove(siteDrawer.nav_items, idx, 1)">↓</el-button>
                      <el-button size="small" type="danger" plain @click="siteDrawer.nav_items.splice(idx, 1)">删除</el-button>
                    </span>
                  </div>
                  <div>
                    <el-button size="small" @click="addDrawerNav">＋ 新增导航项</el-button>
                    <span class="field-hint">外链会在新标签打开并在行尾显示外链小图标；站内路径如 /pricing 直接站内跳转。链接留空的行不会下发。</span>
                  </div>
                </div>
              </el-form-item>
              <el-form-item label="渠道分组标题">
                <div class="field-stack">
                  <el-input v-model="siteDrawer.channels_title" placeholder="如：社区渠道" maxlength="20" />
                  <span class="field-hint">渠道卡片上方的小字标题；留空则不显示标题行（有渠道时仍显示卡片）</span>
                </div>
              </el-form-item>
              <el-form-item label="社区渠道卡片">
                <div class="field-stack sd-stack">
                  <div v-for="(ch, idx) in siteDrawer.channels" :key="idx" class="sd-row">
                    <el-select v-model="ch.icon" class="sd-icon" placeholder="图标">
                      <el-option
                        v-for="opt in siteDrawerIcons"
                        :key="opt"
                        :value="opt"
                        :label="DRAWER_ICON_LABELS[opt] || opt"
                      />
                    </el-select>
                    <el-input v-model="ch.title" class="sd-label" placeholder="标题，如：讨论组" maxlength="40" />
                    <el-input v-model="ch.subtitle" class="sd-label" placeholder="副标题，如：@group 名" maxlength="80" />
                    <el-input v-model="ch.url" class="sd-url" placeholder="链接：https://t.me/…" maxlength="500" />
                    <el-switch v-model="ch.enabled" />
                    <span class="sd-ops">
                      <el-button size="small" :disabled="idx === 0" @click="drawerMove(siteDrawer.channels, idx, -1)">↑</el-button>
                      <el-button size="small" :disabled="idx === siteDrawer.channels.length - 1" @click="drawerMove(siteDrawer.channels, idx, 1)">↓</el-button>
                      <el-button size="small" type="danger" plain @click="siteDrawer.channels.splice(idx, 1)">删除</el-button>
                    </span>
                  </div>
                  <div>
                    <el-button size="small" @click="addDrawerChannel">＋ 新增渠道卡片</el-button>
                    <span class="field-hint">卡片 2 列排布；没有真实公开地址的渠道不要编造，先留空不加。</span>
                  </div>
                </div>
              </el-form-item>
              <el-form-item label="底部按钮">
                <div class="field-stack">
                  <div class="sd-row">
                    <el-input v-model="siteDrawer.cta.label" class="sd-label" placeholder="按钮文字，如：进入后台" maxlength="40" />
                    <el-input v-model="siteDrawer.cta.url" class="sd-url" placeholder="链接留空 = 内置入口（已登录进概览、未登录去登录）" maxlength="500" />
                    <el-switch v-model="siteDrawer.cta.enabled" />
                    <span class="field-suffix">{{ siteDrawer.cta.enabled ? '显示' : '不显示' }}</span>
                  </div>
                  <span class="field-hint">抽屉底部整宽的彩虹主按钮；关掉开关整个按钮不渲染。</span>
                </div>
              </el-form-item>
            </el-form>
          </SectionCard>
          <!-- 隐私政策：独立卡片，归「站点与官网」段 -->
          <SectionCard
            v-if="sec.id === 'site'"
            id="group-privacy-policy"
            title="隐私政策"
            :icon="ScrollText"
            description="用户端公开页 /privacy 的正文（Markdown）。保存后立即生效；把正文清空再保存，用户端回落到随版本发布的内置正文。"
            :tone="privacyPolicyDirty ? 'accent' : 'default'"
            class="group-card"
          >
            <template v-if="privacyPolicyDirty" #actions>
              <span class="au-badge au-badge-amber">未保存</span>
            </template>
            <template v-else #actions>
              <span class="au-badge">{{ privacyHasCustom ? '自定义正文生效中' : '当前用内置正文' }}</span>
            </template>

            <div v-loading="privacyLoading" class="field-stack">
              <el-input
                v-model="privacyMd"
                type="textarea"
                :rows="18"
                maxlength="50000"
                placeholder="在此编辑隐私政策正文（Markdown）…"
              />
              <span class="field-hint">
                支持 Markdown：# / ## 标题、1. 有序列表、- 无序列表、**加粗**、[文字](链接)。
                <template v-if="!privacyHasCustom">当前还没存过自定义正文，框里预填的是现行内置正文的转写稿，改完点「全部保存」才生效。</template>
                正文涉及对用户的承诺（尤其是谷歌盘数据口径），改动会记入管理员审计日志。
              </span>
            </div>
          </SectionCard>
          </template>

          <p class="foot-note">
            <Coins :size="13" />
            套餐与兑换码的明细请前往「订阅套餐」「兑换码」「邀请管理」页面管理。
          </p>
        </div>
      </el-tab-pane>

      <!-- ==================== 危险操作（Phase 5） ==================== -->
      <el-tab-pane name="danger">
        <template #label>
          <span class="tab-label"><TriangleAlert :size="13" />危险操作</span>
        </template>

        <div class="settings-body">
          <el-alert
            v-if="!canWrite"
            type="warning"
            show-icon
            :closable="false"
            :title="`${WRITE_HINT}。下面的按钮已置灰，但内容仍然可读。`"
          />

          <!-- 2. 登录与安全日志 -->
          <SectionCard
            id="danger-logs"
            title="清理登录与安全日志"
            :icon="History"
            description="登录成功/失败、设备超限被拒、诱饵码触发封禁等风控事件的流水"
            class="danger-card"
            :class="{ 'is-target': target === 'logs' }"
          >
            <template #actions>
              <span class="au-badge au-badge-rose">删掉就查不到了</span>
            </template>
            <p class="danger-effect">
              保留 <strong>0</strong> 天 = 清空全部。删掉之后，风控与安全审计就查不到那段历史了。
              想让它自然收敛，应该去「安全与准入 → 客户端策略 → 下载与设备」改日志保留天数。
            </p>
            <template #footer>
              <div class="card-footer">
                <label class="field-suffix" for="purge-days">保留天数</label>
                <el-input-number
                  id="purge-days"
                  v-model="purgeDays"
                  :min="0"
                  :max="3650"
                  :controls="false"
                  class="purge-input"
                />
                <el-button
                  type="danger"
                  :disabled="!canWrite"
                  :loading="dangerBusy === 'logs'"
                  :title="canWrite ? '' : WRITE_HINT"
                  @click="doPurgeLogs(purgeDays)"
                >
                  清理日志
                </el-button>
              </div>
            </template>
          </SectionCard>

          <p class="foot-note">
            <TriangleAlert :size="13" />
            删除媒体库、删除服务器、删除挂载这类<strong>针对单个对象</strong>的操作仍然留在它们各自的页面上——
            那里才看得到要删的是什么；本页只收「跨对象、不可逆」的那几项。
          </p>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ==================== 能力配置抽屉 ==================== -->
    <el-drawer v-model="drawerOpen" :title="drawerTitle" :size="capDrawerSize">
      <div class="cap-drawer">
        <p class="cap-drawer-desc">{{ drawerDesc }}</p>
        <p v-if="drawerHint" class="field-hint cap-drawer-hint">{{ drawerHint }}</p>

        <el-form label-position="top" class="field-form">
          <el-form-item v-for="f in drawerFields" :key="f.key" :label="f.label">
            <div class="field-stack">
              <el-switch
                v-if="f.type === 'bool'"
                v-model="drawerValues[f.key]"
                active-value="true"
                inactive-value="false"
              />
              <el-select v-else-if="f.type === 'select'" v-model="drawerValues[f.key]">
                <el-option v-for="opt in f.options" :key="opt" :label="opt" :value="opt" />
              </el-select>
              <el-input
                v-else-if="f.type === 'int'"
                v-model="drawerValues[f.key]"
                type="number"
                class="num-input"
              />
              <el-input
                v-else
                v-model="drawerValues[f.key]"
                :type="f.type === 'secret' ? 'password' : 'text'"
                :show-password="f.type === 'secret'"
                :placeholder="f.type === 'secret' && drawerValues[f.key] === '******'
                  ? '已配置：保持 ****** 不修改，清空则删除' : (f.placeholder || '')"
              />
              <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
            </div>
          </el-form-item>

          <el-form-item v-if="drawerTestInput" :label="drawerTestInput.label">
            <el-input v-model="testInput.value" :placeholder="drawerTestInput.placeholder" />
          </el-form-item>
        </el-form>

        <el-alert
          v-if="testResult"
          :type="testResult.ok ? 'success' : 'error'"
          :title="testResult.ok ? '测试通过' : '测试未通过'"
          :description="testResult.message"
          :closable="false"
          show-icon
        />

        <div class="cap-drawer-footer">
          <el-button v-if="drawerTestable" :loading="capTesting" @click="runCapabilityTest">
            {{ drawerTestLabel }}
          </el-button>
          <el-button type="primary" :loading="capSaving" @click="saveCurrentCapability">
            <Save :size="14" class="btn-ico" />{{ drawerDirty ? '保存修改' : '保存' }}
          </el-button>
        </div>
        <p class="foot-note">
          <ShieldCheck :size="13" />密钥字段只以掩码回显（保持 ****** 不修改，清空则删除）<template v-if="drawerTestable">；测试会真实发起一次请求或投递，便于区分「密钥错」与「网络不通」</template>。
        </p>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.settings-page { gap: 16px; }
.btn-ico { margin-right: 4px; }
.w-full { width: 100%; }
.settings-body { display: flex; flex-direction: column; gap: 16px; }
.is-refreshing { opacity: 0.6; transition: opacity var(--au-fast) var(--au-ease); }

/* ==================== 页签 ==================== */
.tab-label { display: inline-flex; align-items: center; gap: 4px; }

/* ==================== 骨架 ==================== */
.cap-skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.sk-cap { height: 132px; border-radius: var(--au-r-lg); }
.params-skeleton { display: flex; flex-direction: column; gap: 16px; }
.sk-nav { height: 40px; border-radius: var(--au-r-md); }
.sk-card { height: 220px; border-radius: var(--au-r-lg); }

/* ==================== 运营参数：分组目录 ==================== */
/* 吸顶层：只负责吸顶，不再是滚动容器（与横向滚动的 nav 分离，避免移动端吸顶态滚动原点偏移） */
.group-nav-sticky {
  position: sticky;
  top: calc(62px + env(safe-area-inset-top));
  z-index: 5;
  background: var(--au-bg);
  border-bottom: 1px solid var(--au-border);
}
.group-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 10px 0;
}
.group-nav-sec { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
/* 标签永不被压缩：窄屏下 chips 已是 flex: none，标签也要同等待遇，避免被挤到 pill 下面 */
.group-nav-label { flex-shrink: 0; font-size: var(--ax-fs-micro); color: var(--au-text-3); margin-right: 2px; white-space: nowrap; }
.section-title { margin: 8px 0 -4px; scroll-margin-top: 140px; }
.field-hint.warn { color: var(--au-warning); }
.bot-subgroup + .bot-subgroup { margin-top: 20px; }
.bot-subgroup-title { font-size: var(--ax-fs-aux); font-weight: 600; color: var(--au-text-2); margin-bottom: 10px; }
.group-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  /* ax 口径 · 玻璃胶囊（同 SectionCard 玻璃栈）：半透底 + blur/saturate + 玻璃边 + 内高光 */
  border: 1px solid var(--ax-glass-border);
  border-radius: var(--au-r-full);
  background: var(--ax-glass-d);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  color: var(--au-text-2);
  font: inherit;
  font-size: var(--ax-fs-aux-sm);
  cursor: pointer;
  transition: border-color var(--au-fast) var(--au-ease), color var(--au-fast) var(--au-ease);
}
html[data-theme='light'] .group-chip {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
}
.group-chip:hover { border-color: var(--au-border-strong); color: var(--au-text); }
.group-chip:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: 2px; }
.group-chip svg { color: var(--au-primary); }
.group-chip.is-dirty { border-color: var(--au-primary-border); color: var(--au-text); }
.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--au-r-full);
  background: var(--au-primary);
}
/* 分组 chip 上的"高级"角标：这张卡里有默认收起的高级调优项 */
.chip-adv {
  font-size: var(--ax-fs-micro);
  line-height: 1;
  padding: 2px 6px;
  border-radius: var(--au-r-full);
  /* ax 口径 · 玻璃胶囊（同 SectionCard 玻璃栈）：半透底 + blur/saturate + 玻璃边 + 内高光 */
  border: 1px solid var(--ax-glass-border);
  background: var(--ax-glass-d);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  color: var(--au-text-3);
}
html[data-theme='light'] .chip-adv {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
}
/* 全局保存条：吸顶容器内，有未保存改动时才出现；改了任何一处只在这里点一次 */
.savebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  margin-bottom: 6px;
  border: 1px solid var(--au-primary-border);
  border-radius: var(--au-r-md);
  /* ax 口径 · 玻璃栈：底走玻璃、描边保留主色（语义不变） */
  background: var(--ax-glass-d);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--ax-shadow-card-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
}
html[data-theme='light'] .savebar {
  background: var(--ax-glass-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--ax-shadow-card-l);
}
.savebar-text { font-size: var(--ax-fs-aux); color: var(--au-text-2); }
.savebar-text strong { color: var(--au-primary); }
.savebar-actions { display: flex; align-items: center; gap: 8px; flex: none; }
/* 高级调优折叠：默认收起，收的是"调了也不影响主流程"的参数 */
.advanced-collapse { margin-top: 2px; border-top: 1px dashed var(--au-border); }
.advanced-collapse :deep(.el-collapse-item__header) { border-bottom: none; }
.advanced-title { font-size: var(--ax-fs-aux); font-weight: 600; color: var(--au-text-2); }
.advanced-sub { font-weight: 400; font-size: var(--ax-fs-aux-sm); color: var(--au-text-3); margin-left: 4px; }
/* 跳转落点别被顶栏压住 */
.group-card { scroll-margin-top: 140px; }

/* ==================== 表单 ==================== */
.field-form :deep(.el-form-item) { margin-bottom: 18px; }
.field-form :deep(.el-form-item:last-child) { margin-bottom: 0; }
.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0 24px;
}
.field-grid :deep(.el-form-item.is-wide) { grid-column: 1 / -1; max-width: 640px; }
.field-grid :deep(.el-form-item:last-child) { margin-bottom: 18px; }
.field-stack { display: flex; flex-direction: column; gap: 6px; width: 100%; min-width: 0; }
/* 官网菜单抽屉卡片：条目行编辑（图标/文字/链接/开关/排序/删除一行排完，窄屏自动换行） */
.sd-stack { gap: 10px; }
.sd-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; width: 100%; }
.sd-row .sd-icon { width: 110px; flex: none; }
.sd-row .sd-label { width: 170px; flex: none; }
.sd-row .sd-url { flex: 1 1 220px; min-width: 180px; }
.sd-row .sd-check { display: inline-flex; align-items: center; font-size: var(--ax-fs-aux); color: var(--au-text-2); }
.sd-row .dl-id { width: 130px; flex: none; }
.sd-row .dl-name { width: 150px; flex: none; }
.sd-row .dl-note { flex: 1 1 200px; min-width: 160px; }
.sd-row .dl-host { flex: 1 1 220px; min-width: 180px; }
.sd-row .dl-toggle { display: inline-flex; align-items: center; gap: 8px; flex: none; }
.sd-row .sd-ops { display: inline-flex; gap: 4px; margin-left: auto; }
.field-control { display: flex; align-items: center; gap: 8px; min-width: 0; }
.field-suffix { font-size: var(--ax-fs-aux-sm); color: var(--au-text-3); white-space: nowrap; }
.field-hint { font-size: var(--ax-fs-aux-sm); color: var(--au-text-3); line-height: 1.6; }
.license-link { font-size: var(--ax-fs-aux); color: var(--au-primary); text-decoration: underline; text-underline-offset: 3px; }
.num-input { width: 160px; }

.card-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.capability-note { margin: 0; line-height: 1.7; }
.capability-note strong { color: var(--au-text); font-weight: 600; }

.foot-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-3);
  padding: 0 2px 8px;
}
.foot-note strong { color: var(--au-text-2); }

/* ==================== 危险操作 ==================== */
/* 危险操作卡：左边一条警示色，与前两个页签的日常配置卡区分开 */
.danger-card { border-left: 3px solid var(--au-danger-border); scroll-margin-top: 80px; }
.danger-card.is-target {
  border-color: var(--au-danger);
  outline: 3px solid var(--au-danger-soft);
}
.danger-effect {
  margin: 0;
  font-size: var(--ax-fs-aux-sm);
  line-height: 1.7;
  color: var(--au-text-2);
}
.danger-effect strong { color: var(--au-text); font-weight: 600; }
.purge-input { width: 120px; }

/* ==================== 能力卡片 ==================== */
.cap-section { display: flex; flex-direction: column; gap: 10px; }
.cap-group-title { margin: 4px 0 2px; }

.cap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

/*
  能力卡按列打包（v2.42.5）：单卡分组与相邻分组并排；分组本身不被拆开（break-inside: avoid），
  阅读顺序仍是分组的自然顺序。
*/
.cap-flow {
  display: block;
  column-width: 300px;
  column-gap: 12px;
}
.cap-flow .cap-section {
  display: block;
  break-inside: avoid;
  margin-bottom: 16px;
}
.cap-flow .cap-grid { display: block; }
.cap-flow .cap-card { margin-bottom: 12px; break-inside: avoid; }

.cap-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  text-align: left;
  padding: 16px;
  /* ax 口径 · 玻璃栈（同 SectionCard）：半透底 + blur/saturate + 玻璃边 + 内高光 + 柔影 */
  background: var(--ax-glass-d);
  border: 1px solid var(--ax-glass-border);
  border-radius: var(--au-r-lg);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--ax-shadow-card-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  cursor: pointer;
  font: inherit;
  color: inherit;
  transition: border-color var(--au-fast) var(--au-ease), background var(--au-fast) var(--au-ease);
}
html[data-theme='light'] .cap-card {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--ax-shadow-card-l);
}
.cap-card:hover { border-color: var(--au-border-strong); }
.cap-card:active { transform: scale(0.98); }
.cap-card:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: 2px; }
.cap-card.ok { border-color: var(--au-success-border); }
.cap-card.warn { border-color: var(--au-warning-border); }

.cap-card-top { display: flex; align-items: center; justify-content: space-between; }

.cap-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--au-r-md);
  background: var(--au-primary-soft);
  color: var(--au-primary);
}

.cap-title { font-size: var(--ax-fs-body); margin: 2px 0 0; color: var(--au-text); }
.cap-card p { font-size: var(--ax-fs-aux-sm); color: var(--au-text-3); line-height: 1.6; margin: 0; }
.cap-more { font-size: var(--ax-fs-aux-sm); color: var(--au-primary); margin-top: 4px; }

.cap-drawer { display: flex; flex-direction: column; gap: 12px; }
.cap-drawer-desc { font-size: var(--ax-fs-aux); color: var(--au-text-2); margin: 0; line-height: 1.6; }
.cap-drawer-hint { margin: 0; }
.cap-drawer :deep(.el-select) { width: 100%; }

.cap-drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--au-border);
}

/* 手机：数字输入铺满、保存按钮拉满整行 */
@media (max-width: 768px) {
  .num-input { width: 100%; }
  .field-grid { grid-template-columns: 1fr; }
  .card-footer { justify-content: stretch; }
  .card-footer :deep(.el-button) { flex: 1 1 auto; margin-left: 0; }
  .purge-input { flex: 1 1 100px; }
  .settings-body { gap: 12px; }
  .group-nav { flex-wrap: nowrap; overflow-x: auto; padding: 8px 0; scrollbar-width: none; }
  /* scrollbar-width 只 Firefox 认，WebKit 内核要这一行才真藏得住 */
  .group-nav::-webkit-scrollbar { display: none; }
  .group-nav-sec { flex-wrap: nowrap; flex: none; }
  .group-chip { flex: none; }
  .cap-grid { grid-template-columns: 1fr; }
  .cap-flow { column-width: auto; column-count: 1; }
  .cap-flow .cap-section { margin-bottom: 12px; }
  .cap-flow .cap-card { margin-bottom: 10px; }
  .cap-drawer-footer { justify-content: stretch; }
  .cap-drawer-footer :deep(.el-button) { flex: 1; margin-left: 0; }
}

</style>
