<script setup lang="ts">
/**
 * 公益服·公益配置：SystemConfig 表单
 *
 * v2.55（暗房影院统一）：PageHeader + 分组 SectionCard。
 * 2026-11-13：货币体系/积分转账/获取与兑换/全员赠送能量已随货币下线，
 * 本页只剩游戏榜、播放榜、播报、TG 门禁与公益服规则。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Bot, KeyRound, RefreshCw, Save, Send, Settings2, Trophy } from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard } from '@/components/ui'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { fetchWelfareConfig, saveWelfareConfig } from '@/api/welfare'
import { fetchViewUnlockSettings, updateViewUnlockSettings } from '@/api/admin'

const { isPhone } = useBreakpoint()
/** 窄屏标签放到输入框上方，120px 的左侧标签会把输入框挤出屏幕 */
const labelPosition = computed(() => (isPhone.value ? 'top' : 'right'))

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
/** 首次加载成功前不允许保存：否则会把一张空表单（全是默认值）写回服务端 */
const loaded = ref(false)
const form = ref<Record<string, string>>({})


// 每日播放排行榜：word 风格 true/false，总开关默认关、三榜分开关默认开
const playrankEnabled = computed({
  get: () => (form.value['bot_playrank_enabled'] ?? 'false') === 'true',
  set: (v: boolean) => { form.value['bot_playrank_enabled'] = v ? 'true' : 'false' },
})
const playrankUsersEnabled = computed({
  get: () => (form.value['bot_playrank_users_enabled'] ?? 'true') === 'true',
  set: (v: boolean) => { form.value['bot_playrank_users_enabled'] = v ? 'true' : 'false' },
})
const playrankSeriesEnabled = computed({
  get: () => (form.value['bot_playrank_series_enabled'] ?? 'true') === 'true',
  set: (v: boolean) => { form.value['bot_playrank_series_enabled'] = v ? 'true' : 'false' },
})
const playrankMoviesEnabled = computed({
  get: () => (form.value['bot_playrank_movies_enabled'] ?? 'true') === 'true',
  set: (v: boolean) => { form.value['bot_playrank_movies_enabled'] = v ? 'true' : 'false' },
})
const playrankPosterEnabled = computed({
  get: () => (form.value['bot_playrank_poster_enabled'] ?? 'true') === 'true',
  set: (v: boolean) => { form.value['bot_playrank_poster_enabled'] = v ? 'true' : 'false' },
})

const playrankFields = [
  { key: 'bot_playrank_time', label: '发送时间', hint: '上海时间，如 21:30', suffix: '' },
  { key: 'bot_playrank_top_n', label: '每榜条数', hint: '每个榜最多列几条，默认 5，范围 1-20', suffix: '条' },
]

// 更新入库播报：word 风格 true/false，总开关默认开（读侧 tg_bot.updatedigest）
const digestEnabled = computed({
  get: () => (form.value['bot_update_digest_enabled'] ?? 'true') === 'true',
  set: (v: boolean) => { form.value['bot_update_digest_enabled'] = v ? 'true' : 'false' },
})

const digestFields = [
  { key: 'bot_update_digest_interval_minutes', label: '攒批间隔', hint: '每隔这段时间把这段时间新入库的攒成一批发出，默认 30，范围 1-1440', suffix: '分钟' },
  { key: 'bot_update_digest_chat_id', label: '发送目标', hint: '填频道 @用户名或频道 ID 则发频道。机器人须先被设为该频道管理员才能发帖', suffix: '', placeholder: '留空发群里' },
]

const tgBindEnabled = computed({
  get: () => (form.value['welfare_require_tg_bind'] ?? '1') === '1',
  set: (v: boolean) => { form.value['welfare_require_tg_bind'] = v ? '1' : '0' },
})

const botFields = [
  { key: 'bot_group_ids', label: '启用群 ID', hint: '逗号分隔的 TG 群 id，空=所有群都启用 Bot', suffix: '' },
  { key: 'bot_rate_limit_seconds', label: '命令限流', hint: '每用户每命令最小间隔，默认 3', suffix: '秒' },
  { key: 'bot_group_rate_limit', label: '群限流', hint: '每群每分钟最多处理消息数，超限静默丢弃，默认 20', suffix: '条/分' },
  { key: 'bot_channel_whitelist', label: '频道白名单', hint: '逗号分隔的频道 id，名单内频道可在群里以频道身份发言，空=都不放行', suffix: '' },
  { key: 'tg_notify_merge_window_sec', label: '提醒合并窗口', hint: '同一人这段时间内的到货/转存提醒合并成一条，默认 60，填 0 则到点就发', suffix: '秒' },
]

// 主动私聊提醒：word 风格 true/false，默认关
const storeArrivalNotify = computed({
  get: () => (form.value['tg_notify_store_arrival_enabled'] ?? 'false') === 'true',
  set: (v: boolean) => { form.value['tg_notify_store_arrival_enabled'] = v ? 'true' : 'false' },
})
const transferDoneNotify = computed({
  get: () => (form.value['tg_notify_transfer_done_enabled'] ?? 'false') === 'true',
  set: (v: boolean) => { form.value['tg_notify_transfer_done_enabled'] = v ? 'true' : 'false' },
})

// 频道马甲治理总开关：word 风格 true/false（读侧 tg_bot.policy 只认 'true'）
const channelGuardEnabled = computed({
  get: () => (form.value['bot_channel_guard_enabled'] ?? 'true') === 'true',
  set: (v: boolean) => { form.value['bot_channel_guard_enabled'] = v ? 'true' : 'false' },
})

// 退群处置：开=cancel_welfare（只取消公益资格），关=none（不处置）
const leaveCancelEnabled = computed({
  get: () => (form.value['bot_leave_action'] ?? 'cancel_welfare') === 'cancel_welfare',
  set: (v: boolean) => { form.value['bot_leave_action'] = v ? 'cancel_welfare' : 'none' },
})

const welfareFields = [
  { key: 'welfare_grace_days', label: '到期保留天数', hint: '到期后可登录但不可播放，默认 7', suffix: '天' },
  { key: 'welfare_inactive_days', label: '未活跃禁用天数', hint: '默认 30', suffix: '天' },
  { key: 'welfare_vitality_renew_min', label: '活力保号线', hint: '资格到期时，当前活力达到该值自动续期、不扣分（保号只认活力，连签不再参与）；查看凭证不自动续，默认 3', suffix: '点' },
  { key: 'welfare_streak_renew_days', label: '保号续期天数', hint: '活力保号自动续期一次续多少天，默认 30，范围 1-365', suffix: '天' },
  { key: 'welfare_code_monthly_quota', label: '资格码月配额', hint: '每位管理员每月可生成的公益资格码张数，默认 30，填 0 不限', suffix: '张/月' },
  { key: 'register_gift_welfare_days', label: '新注册送公益天数', hint: '新用户注册默认赠送的公益资格天数，默认 30，填 0 为永不过期', suffix: '天' },
  { key: 'register_gift_view_days', label: '新注册送查看天数', hint: '新用户注册默认赠送的查看凭证天数，默认 30，最小 1', suffix: '天' },
  // 公益求片额度（welfare_request_monthly）已于 2026-10-23 去重 #5 移到
  // 运营参数「兑换码 · 求片」卡，与每日上限、付费月限放一起
]

// 群发言积分两套（chat_points_* / points_chat_*）均已退役：前者 2026-10-23
// 去重删除，后者随货币于 2026-11-13 与 points.py 一并下线。

// 默认关闭的功能开关保留在面板（2026-10-21 服主口径：默认开的开关摘面板走 env，默认关的留面板）
// r40：查看行有效期（emby_view_unlock_days；积分价格已随货币下线）；
// 后端 GET/PUT /api/admin/emby/view-unlock；此前有接口无后台入口）
const viewUnlock = ref({ days: 0 })
const viewUnlockLoaded = ref(false)
const viewUnlockSaving = ref(false)

async function saveViewUnlock() {
  if (!viewUnlockLoaded.value || viewUnlockSaving.value) return
  const days = Math.max(0, Math.floor(Number(viewUnlock.value.days) || 0))
  viewUnlockSaving.value = true
  try {
    const res = await updateViewUnlockSettings({ days })
    viewUnlock.value = { days: res.days ?? days }
    ElMessage.success('查看权限有效期已保存')
  } catch {
    /* 写操作失败由请求拦截器统一弹错 */
  } finally {
    viewUnlockSaving.value = false
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    form.value = await fetchWelfareConfig()
    loaded.value = true
  } catch (e) {
    // GET 走 silent：拦截器不提示，这里给页面级错误态（带重试）
    loadError.value = e instanceof Error ? e.message : '加载配置失败'
  } finally {
    loading.value = false
  }
  // r40：查看权限解锁价格走独立 endpoint（view_unlock 家族），与福利表单分开加载，
  // 一边失败不影响另一边展示
  try {
    const vu = await fetchViewUnlockSettings()
    viewUnlock.value = { days: vu.days ?? 0 }
    viewUnlockLoaded.value = true
  } catch {
    /* 查看权限加载失败时该卡片显示错误态，不影响福利表单 */
  }
}

async function save() {
  if (!loaded.value || saving.value) return
  saving.value = true
  try {
    await saveWelfareConfig(form.value)
    ElMessage.success('已保存')
  } catch {
    /* 写操作失败由请求拦截器统一弹错 */
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader
      eyebrow="内容"
      title="公益配置"
      description="游戏榜、播放榜、播报与公益服到期策略"
    >
      <template #actions>
        <el-button :icon="RefreshCw" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Save" :loading="saving" :disabled="!loaded" @click="save">保存</el-button>
      </template>
    </PageHeader>

    <SectionCard v-if="loadError && !loaded">
      <EmptyState :icon="Settings2" title="配置加载失败" :description="loadError">
        <template #actions>
          <el-button :loading="loading" @click="load">重试</el-button>
        </template>
      </EmptyState>
    </SectionCard>

    <div v-else v-loading="loading" class="form-grid">

      <SectionCard
        title="每日播放排行榜"
        :icon="Trophy"
        description="每天到点把今日电影榜、剧集榜、观看时长榜发到主群（海报图＋文字榜一条发，发出后置顶）"
      >
        <el-form :label-position="labelPosition" label-width="120px" class="config-form">
          <el-form-item label="总开关">
            <el-switch v-model="playrankEnabled" />
            <span class="field-hint">关闭后定时与手动都不发。默认关。主群用「启用群 ID」里的第一个群；管理员也可在机器人私聊发 /playrank 手动推送验收。当天没人播放时不发空榜。不发能量</span>
          </el-form-item>
          <el-form-item v-for="f in playrankFields" :key="f.key" :label="f.label">
            <el-input v-model="form[f.key]" class="field-input" />
            <span class="field-suffix">{{ f.suffix }}</span>
            <span class="field-hint">{{ f.hint }}</span>
          </el-form-item>
          <el-form-item label="观看时长榜">
            <el-switch v-model="playrankUsersEnabled" />
            <span class="field-hint">按今日总观看时长排用户，默认开</span>
          </el-form-item>
          <el-form-item label="剧集榜">
            <el-switch v-model="playrankSeriesEnabled" />
            <span class="field-hint">集汇总到剧，按总时长排，默认开</span>
          </el-form-item>
          <el-form-item label="电影榜">
            <el-switch v-model="playrankMoviesEnabled" />
            <span class="field-hint">按总时长排电影，默认开</span>
          </el-form-item>
          <el-form-item label="海报图">
            <el-switch v-model="playrankPosterEnabled" />
            <span class="field-hint">播报时配一张海报图（电影/剧集各前 5）与文字榜一起发，默认开；关掉只发文字</span>
          </el-form-item>
        </el-form>
      </SectionCard>

      <SectionCard
        title="更新入库播报"
        :icon="RefreshCw"
        description="新片入库后攒批播报到群里"
      >
        <el-form :label-position="labelPosition" label-width="120px" class="config-form">
          <el-form-item label="总开关">
            <el-switch v-model="digestEnabled" />
            <span class="field-hint">关闭后不再往群里发入库播报。默认开。主群用「启用群 ID」里的第一个群；没有新入库时不发空播报。只报已经能看的片，没认出身份的等认出后再报</span>
          </el-form-item>
          <el-form-item v-for="f in digestFields" :key="f.key" :label="f.label">
            <el-input v-model="form[f.key]" class="field-input" :placeholder="f.placeholder || ''" />
            <span class="field-suffix">{{ f.suffix }}</span>
            <span class="field-hint">{{ f.hint }}</span>
          </el-form-item>
        </el-form>
      </SectionCard>

      <SectionCard
        title="Telegram 门禁"
        :icon="Send"
        description="公益服能力（抽奖）是否要求绑定 Telegram"
      >
        <el-form :label-position="labelPosition" label-width="120px" class="config-form">
          <el-form-item label="强制绑定">
            <el-switch v-model="tgBindEnabled" />
            <span class="field-hint">开启后未绑定用户无法使用公益服写操作；老用户有 7 天宽限期；Bot 故障时可关闭降级</span>
          </el-form-item>
        </el-form>
      </SectionCard>

      <SectionCard
        title="TG Bot 总控"
        :icon="Bot"
        description="Bot 启用群白名单与限流（各功能开关由服务端配置控制）"
      >
        <el-form :label-position="labelPosition" label-width="120px" class="config-form">
          <el-form-item v-for="f in botFields" :key="f.key" :label="f.label">
            <el-input v-model="form[f.key]" class="field-input" />
            <span class="field-suffix">{{ f.suffix }}</span>
            <span class="field-hint">{{ f.hint }}</span>
          </el-form-item>
          <el-form-item label="频道马甲治理">
            <el-switch v-model="channelGuardEnabled" />
            <span class="field-hint">开启后群里以外部频道身份发言的消息会被删除并回说明；匿名管理员不受影响，默认开</span>
          </el-form-item>
          <el-form-item label="退群取消资格">
            <el-switch v-model="leaveCancelEnabled" />
            <span class="field-hint">开启后已绑定用户退群／被踢自动只取消公益资格（账号、查看凭证不动）；关闭则退群不做处理，默认开</span>
          </el-form-item>
          <el-form-item label="求片到货提醒">
            <el-switch v-model="storeArrivalNotify" />
            <span class="field-hint">开启后，你求的片入库时私聊求片人和附议人一句「到了」，不带播放按钮。默认关</span>
          </el-form-item>
          <el-form-item label="转存完成提醒">
            <el-switch v-model="transferDoneNotify" />
            <span class="field-hint">开启后，你自己发起的转存完成或最终失败时私聊你。成功只发文字；失败说明原因，并给「为什么播不了」。默认关</span>
          </el-form-item>
        </el-form>
      </SectionCard>

      <SectionCard
        title="公益服规则"
        :icon="Settings2"
        description="到期保留、未活跃禁用与新注册赠送（求片额度在运营参数里统一设置）"
      >
        <el-form :label-position="labelPosition" label-width="120px" class="config-form">
          <el-form-item v-for="f in welfareFields" :key="f.key" :label="f.label">
            <el-input v-model="form[f.key]" class="field-input" />
            <span class="field-suffix">{{ f.suffix }}</span>
            <span class="field-hint">{{ f.hint }}</span>
          </el-form-item>
        </el-form>
      </SectionCard>

      <!-- r40：查看行有效期（emby_view_unlock_days）。后端接口一直存在，
           但管理后台没有入口，只能手写 DB；本轮补上独立卡片（独立 endpoint 独立保存） -->
      <SectionCard
        title="查看权限解锁"
        :icon="KeyRound"
        description="公益服查看行（Emby 账号/线路）的有效期"
      >
        <el-form v-if="viewUnlockLoaded" :label-position="labelPosition" label-width="120px" class="config-form">
          <el-form-item label="有效期">
            <el-input-number v-model="viewUnlock.days" :min="0" :step="1" class="field-input" />
            <span class="field-suffix">天</span>
            <span class="field-hint">解锁后有效天数，0 = 永久有效</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Save" :loading="viewUnlockSaving" @click="saveViewUnlock">保存有效期</el-button>
          </el-form-item>
        </el-form>
        <el-alert v-else title="查看权限配置加载失败，请刷新重试" type="warning" :closable="false" />
      </SectionCard>

    </div>

  </div>
</template>

<style scoped>
.form-grid {
  display: grid;
  gap: 16px;
  /* min(100%, …)：360px 手机上单列铺满，不会因为 320px 的轨道下限把页面撑出横向滚动 */
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr));
}
.config-form :deep(.el-form-item__content) {
  flex-wrap: wrap;
  row-gap: 4px;
}
.field-input {
  width: 160px;
  max-width: 100%;
}
.config-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.config-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}
.field-suffix {
  margin-left: 8px;
  color: var(--au-text-2);
  white-space: nowrap;
}
/* 说明另起一行：长句子跟在输入框后面会被挤成一列竖排 */
.field-hint {
  flex-basis: 100%;
  color: var(--au-text-3);
  font-size: 12px;
  line-height: 1.5;
}
</style>
