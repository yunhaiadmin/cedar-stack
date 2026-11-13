<template>
  <div class="pc-entry">
    <!-- providers 拉取失败：不装成"没开"，整段错误态 + 重试 -->
    <div v-if="providersError" class="sx-err">
      <span>{{ providersError }}</span>
      <button type="button" class="sx-mini-btn" @click="loadProviders">重试</button>
    </div>

    <!-- 谷歌盘（后端 providers 名单驱动；名单里没有整行不渲染） -->
    <template v-else-if="providersLoaded && hasProvider('drive')">
      <PcEntryRow
        :icon="HardDrive"
        name="谷歌盘"
        :meta="providerMeta('drive')"
        expandable
        :expanded="driveExpanded"
        @click="driveExpanded = !driveExpanded"
      />
      <div v-if="driveExpanded" class="pc-expand">
        <div class="prov-tools">
          <p class="prov-desc">绑了自己的谷歌盘，播放就走你自己的额度，不走共享池；没绑定只能浏览、不能点播</p>
          <button type="button" class="sx-mini-btn" title="刷新" :disabled="driveLoading" :aria-busy="driveLoading" @click="loadDriveBindings">
            <RefreshCw :size="14" /> {{ driveLoading ? '刷新中' : '刷新列表' }}
          </button>
        </div>
        <div v-if="driveLoading && !driveBindings.length" class="sx-loading"><span class="sx-spin" aria-hidden="true"></span>正在加载…</div>
        <div v-else-if="driveError" class="sx-err"><span>{{ driveError }}</span><button type="button" class="sx-mini-btn" @click="loadDriveBindings">重试</button></div>
        <div v-else-if="!driveBindings.length" class="sx-empty sx-empty--left">
          <p>① 这是什么：绑了自己的谷歌盘，播放就走你自己的额度，不走共享池；没绑定只能浏览、不能点播。</p>
          <p>② 怎么做：起个名字点“绑定谷歌盘”→新窗口去 Google 点同意→回来后列表会自动刷新，没出现再点“刷新列表”。</p>
          <p>③ 绑完怎么验：去播一集，看是不是走你自己的盘。</p>
        </div>
        <template v-else>
          <template v-for="b in driveBindings" :key="b.id">
          <div class="dev-row">
            <div class="dev-main">
              <span class="dev-name">{{ b.account_label }}</span>
              <span class="dev-meta">
                谷歌盘 · {{ driveStatusText(b.status) }}
                <template v-if="b.error_message"> · {{ b.error_message }}</template>
                <template v-if="b.needs_rebind"> · 转存需要写权限，请重新授权</template>
              </span>
            </div>
            <span v-if="b.status === 'active'" class="sx-status sx-status--ok"><span class="sx-dot" aria-hidden="true"></span>可用</span>
            <span v-else class="sx-status sx-status--bad"><span class="sx-dot" aria-hidden="true"></span>不可用</span>
            <button
              v-if="b.status !== 'active'"
              type="button"
              class="dev-remove"
              :disabled="verifyingBinding === b.id"
              @click="handleVerifyBinding(b)"
            >
              {{ verifyingBinding === b.id ? '验证中' : '重新验证' }}
            </button>
            <button
              v-if="b.needs_rebind"
              type="button"
              class="dev-remove"
              :disabled="rebindingId === b.id"
              @click="handleRebindDrive(b)"
            >
              {{ rebindingId === b.id ? '正在打开授权…' : '重新授权' }}
            </button>
            <button
              type="button"
              class="dev-remove"
              :class="{ confirming: pendingUnbind === b.id }"
              :disabled="unbindingId === b.id"
              @click="handleUnbindDrive(b)"
            >
              {{ unbindingId === b.id ? '解绑中' : (pendingUnbind === b.id ? '确认解绑' : '解绑') }}
            </button>
          </div>
          <!-- 公共贡献池（goal-33 PR-4）：池总开关关且本人无成员/候选行时整块不渲染 -->
          <div v-if="poolEntryFor(b.id)" class="pool-block">
            <template v-if="!poolEntryFor(b.id)!.member">
              <div class="pool-line">
                <span class="pool-text">把这个盘贡献到公共池：别人能看你盘里的公共副本，站里只按上限用盘，你随时能退出。</span>
                <button
                  v-if="poolEntryFor(b.id)!.can_apply"
                  type="button"
                  class="sx-mini-btn"
                  :disabled="poolActing === b.id"
                  @click="handlePoolApply(b.id)"
                >
                  {{ poolActing === b.id ? '提交中' : (contribution?.join_mode === 'open' ? '开启贡献' : '申请加入公共池') }}
                </button>
                <span v-else class="pool-note">{{ poolEntryFor(b.id)!.apply_block_reason }}</span>
              </div>
              <button type="button" class="pool-faq-toggle" @click="poolFaqOpen = !poolFaqOpen">
                {{ poolFaqOpen ? '收起入池须知' : '入池须知' }}
              </button>
              <div v-if="poolFaqOpen" class="pool-faq">
                <p>盘里会出现一个公共副本文件夹，文件名就是片名，别的观众能看到片名、看不到你是谁；站里不碰你盘里别的文件；退出后放在你盘里的公共副本会被删除，正在靠这些副本看片的人会回到“准备中”等其它盘接上。你的绑定本身不会被解绑。</p>
              </div>
            </template>
            <div v-else-if="poolEntryFor(b.id)!.member!.status === 'pending'" class="pool-line">
              <span class="sx-status sx-status--warn"><span class="sx-dot" aria-hidden="true"></span>审核中</span>
              <span class="pool-text">入池申请已提交，等管理员通过。</span>
            </div>
            <div v-else-if="poolEntryFor(b.id)!.member!.status === 'active'" class="pool-line">
              <span class="sx-status sx-status--ok"><span class="sx-dot" aria-hidden="true"></span>已加入公共池</span>
              <span class="pool-text">
                池占用 {{ fmtPoolBytes(poolEntryFor(b.id)!.member!.copies_bytes) }} · 副本 {{ poolEntryFor(b.id)!.member!.copies_count }} 份 · 今日被起播 {{ poolEntryFor(b.id)!.member!.today_play_starts }} 次
              </span>
              <button
                type="button"
                class="dev-remove"
                :class="{ confirming: pendingPoolWithdraw === poolEntryFor(b.id)!.member!.id }"
                :disabled="poolActing === b.id"
                @click="handlePoolWithdraw(b.id)"
              >
                {{ pendingPoolWithdraw === poolEntryFor(b.id)!.member!.id ? '确认退出（副本会被删除）' : '退出贡献' }}
              </button>
            </div>
            <div v-else-if="poolEntryFor(b.id)!.member!.status === 'suspended'" class="pool-line">
              <span class="sx-status sx-status--bad"><span class="sx-dot" aria-hidden="true"></span>已暂停</span>
              <span class="pool-text">{{ poolEntryFor(b.id)!.member!.suspended_reason === 'binding_invalid' ? '绑定失效连带暂停，恢复绑定后联系管理员恢复。' : '这个盘已被暂停，副本暂停下发。' }}</span>
              <button
                type="button"
                class="dev-remove"
                :class="{ confirming: pendingPoolWithdraw === poolEntryFor(b.id)!.member!.id }"
                :disabled="poolActing === b.id"
                @click="handlePoolWithdraw(b.id)"
              >
                {{ pendingPoolWithdraw === poolEntryFor(b.id)!.member!.id ? '确认退出（副本会被删除）' : '退出贡献' }}
              </button>
            </div>
            <div v-else class="pool-line">
              <span class="pool-text">{{ poolTerminalText(poolEntryFor(b.id)!.member!.status) }}</span>
              <button
                v-if="poolEntryFor(b.id)!.can_apply"
                type="button"
                class="sx-mini-btn"
                :disabled="poolActing === b.id"
                @click="handlePoolApply(b.id)"
              >
                {{ poolActing === b.id ? '提交中' : '重新申请加入' }}
              </button>
              <span v-else-if="poolEntryFor(b.id)!.apply_block_reason" class="pool-note">{{ poolEntryFor(b.id)!.apply_block_reason }}</span>
            </div>
          </div>
          </template>
          <p class="dev-tip">绑好了，下一步：去播一集试试，走的就是你自己的盘；播不动先点“重新验证”，还不行就解绑重绑。</p>
          <p v-if="driveBindings.some((b) => b.needs_rebind)" class="dev-tip">有谷歌盘还是旧的只读授权，转存到你盘里会失败：点那一行旁边的“重新授权”，去 Google 同意新增的写权限（只覆盖本站创建的文件），同意后转存失败的任务会自动重跑。</p>
        </template>
        <div class="acct-field">
          <label class="acct-label" for="drive-label">给这个盘起个名字</label>
          <input id="drive-label" v-model="driveLabel" type="text" placeholder="比如：我的主力盘" autocomplete="off" />
        </div>
        <p v-if="driveFormError" class="acct-error" role="alert">{{ driveFormError }}</p>
        <p v-if="driveAuthUrl" class="dev-tip">点这里打开 Google 授权页：<a :href="driveAuthUrl" target="_blank" rel="noopener">打开 Google 授权页</a>，同意后关掉那个窗口、回到本页，列表会自动刷新。</p>
        <template v-if="driveOauth2 && driveOauth2.available">
          <div class="acct-field">
            <span class="acct-label">{{ driveOauth2.label }}</span>
            <p class="dev-tip">{{ driveOauth2.description }}</p>
          </div>
          <p v-if="!driveOauth2.eligible" class="acct-error" role="alert">{{ driveOauth2.reason }}</p>
          <UhdButton variant="primary" :disabled="!driveOauth2.eligible || drive2Starting" @click="handleBindDrive2">
            {{ drive2Starting ? '正在打开授权…' : '绑定谷歌盘' }}
          </UhdButton>
          <p class="dev-tip">用上面填的名字绑定：点绑定后会在新窗口打开 Google 授权页，点同意后关掉那个窗口、回到本页，列表会自动刷新，没出现再点“刷新列表”。授权页本身不是本站页面，账号密码只交给 Google。</p>
        </template>
      </div>
    </template>

    <!-- 115 / 夸克：绑定线已下线，存量只给看 + 解绑 -->
    <template v-if="removedBindings.length">
      <PcEntryRow
        class="bind-sep"
        :icon="Layers"
        name="已下线绑定"
        :meta="`${removedBindings.length} 条`"

        expandable
        :expanded="removedExpanded"
        @click="removedExpanded = !removedExpanded"
      />
      <div v-if="removedExpanded" class="pc-expand">
        <div v-for="b in removedBindings" :key="b.id" class="dev-row">
          <div class="dev-main">
            <span class="dev-name">{{ b.account_label }}</span>
            <span class="dev-meta">
              {{ b.provider === 'pan115' ? '115' : '夸克' }} · 已下线
              <template v-if="b.error_message"> · {{ b.error_message }}</template>
            </span>
          </div>
          <span class="sx-status sx-status--idle"><span class="sx-dot" aria-hidden="true"></span>已下线</span>
          <button
            type="button"
            class="dev-remove"
            :class="{ confirming: pendingUnbind === b.id }"
            :disabled="unbindingId === b.id"
            @click="handleUnbindDrive(b)"
          >
            {{ unbindingId === b.id ? '解绑中' : (pendingUnbind === b.id ? '确认解绑' : '解绑') }}
          </button>
        </div>
        <p class="dev-tip">115 / 夸克绑定线已下线，旧绑定不能再用来点播；想播本地库的片，请在上面改绑谷歌盘。</p>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 网盘绑定管理（自旧 AccountSettingsView 原样抽出，行为口径不变）：
 * 谷歌盘（行式展开：名单 / 重新验证 / 重新授权 / 两步确认解绑 / 起名
 * 绑定（单一入口：老入口已隐藏、新绑只走新应用；老绑定的重新授权
 * 仍按建绑时的入口走，是存量维护、不受隐藏影响））＋ 115/夸克
 * 已下线存量解绑。providers 名单
 * 门控与原页一致（名单里没有 drive 整行不渲染；名单拉取失败显示错误
 * 态不猜）。OAuth 流程：手势里同步开空窗保住手势、拿到授权地址再指
 * 过去（绝不带 noopener，iOS 实测带了后续改地址不生效）；弹窗被拦时
 * 留手动链接；授权回来靠 pendingOAuthReturn 标记在回焦/回前台时重拉
 * 一次列表。父级靠 'loaded' 事件决定绑定组是否显示（v-show 保挂载）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { HardDrive, Layers, RefreshCw } from 'lucide-vue-next'
import PcEntryRow from './PcEntryRow.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import {
  poolContributionApi,
  storageBindingApi,
  type DriveOAuth2EntryStatus,
  type PoolContributionBinding,
  type PoolContributionResponse,
  type StorageBinding,
} from '@/api/economy'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { toUserMessage } from '@/utils/toUserMessage'

const emit = defineEmits<{
  (e: 'loaded', payload: { visible: boolean }): void
  /** 列表里出现可用（active）谷歌盘绑定时发：设置页据此执行绑盘回跳 */
  (e: 'activeBinding'): void
}>()

const toast = useToast()

const availableProviders = ref<string[]>([])
const providersLoaded = ref(false)
const providersError = ref('')
const driveOauth2 = ref<DriveOAuth2EntryStatus | null>(null)
const driveBindings = ref<StorageBinding[]>([])
const removedBindings = ref<StorageBinding[]>([])
const driveLoading = ref(false)
const driveError = ref('')
const driveLabel = ref('')
const driveFormError = ref('')
const drive2Starting = ref(false)
const rebindingId = ref<number | null>(null)
const driveAuthUrl = ref('')
const verifyingBinding = ref<number | null>(null)
const unbindingId = ref<number | null>(null)
const pendingUnbind = ref<number | null>(null)
const pendingOAuthReturn = ref(false)
const driveExpanded = ref(false)
const removedExpanded = ref(false)

// 公共贡献池（PR-4）：状态随绑定列表一起拉；拿不到就整块不渲染，
// 不装成“没加入”。
const contribution = ref<PoolContributionResponse | null>(null)
const poolActing = ref<number | null>(null)
const pendingPoolWithdraw = ref<number | null>(null)
const poolFaqOpen = ref(false)

async function loadContribution() {
  try {
    contribution.value = await poolContributionApi.status()
  } catch {
    contribution.value = null
  }
}

function poolEntryFor(bindingId: number): PoolContributionBinding | null {
  const c = contribution.value
  if (!c) return null
  const entry = c.bindings.find((e) => e.binding_id === bindingId) || null
  if (!entry) return null
  // 存在感口径：池开着、或本人已有成员行/候选资格时才渲染
  if (!c.pool_enabled && !entry.member && !c.candidate) return null
  return entry
}

function fmtPoolBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

function poolTerminalText(status: string): string {
  if (status === 'exited') return '已退出公共池。'
  if (status === 'rejected') return '上次申请被拒绝，可以重新申请。'
  if (status === 'ejected') return '这个盘已被管理员清退。'
  return ''
}

async function handlePoolApply(bindingId: number) {
  if (poolActing.value !== null) return
  poolActing.value = bindingId
  try {
    const res = await poolContributionApi.apply(bindingId)
    toast.success(
      res.member.status === 'active' ? '已加入公共池' : '申请已提交，等管理员通过')
    await loadContribution()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'saveFailed'))
  } finally {
    poolActing.value = null
  }
}

async function handlePoolWithdraw(bindingId: number) {
  // 两步确认（照解绑口径：4 秒内未确认自动收回）；后果在按钮文案里
  // 写清：退出后放在盘里的公共副本会被删除，绑定本身不解绑。
  const entry = poolEntryFor(bindingId)
  const member = entry?.member
  if (!member || poolActing.value !== null) return
  if (pendingPoolWithdraw.value !== member.id) {
    pendingPoolWithdraw.value = member.id
    window.setTimeout(() => {
      if (pendingPoolWithdraw.value === member.id) {
        pendingPoolWithdraw.value = null
      }
    }, 4000)
    return
  }
  pendingPoolWithdraw.value = null
  poolActing.value = bindingId
  try {
    await poolContributionApi.withdraw(member.id)
    toast.success('已退出公共池')
    await loadContribution()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'saveFailed'))
  } finally {
    poolActing.value = null
  }
}

function hasProvider(provider: string) {
  return availableProviders.value.includes(provider)
}
function bindingsOf(provider: string): StorageBinding[] {
  if (provider === 'drive') return driveBindings.value
  return []
}
function providerNeedsAction(provider: string) {
  return bindingsOf(provider).some((b) => b.status !== 'active' || b.needs_rebind)
}
const PROVIDER_EMPTY_META: Record<string, string> = {
  drive: '还没绑 · 绑了走你自己的额度',
}
function providerMeta(provider: string) {
  const list = bindingsOf(provider)
  if (!list.length) return PROVIDER_EMPTY_META[provider] || '还没绑'
  // 行小字照效果图口径：状态 · 第一个盘的名字；需处理时后缀点明
  let meta = list.some((b) => b.status === 'active')
    ? `已绑定 · ${list[0].account_label}`
    : `不可用 · ${list[0].account_label}`
  if (list.some((b) => b.needs_rebind)) meta += ' · 有盘需重新授权'
  else if (providerNeedsAction(provider) && list.some((b) => b.status === 'active')) meta += ' · 有不可用'
  return meta
}
function driveStatusText(status: string) {
  if (status === 'active') return '可用'
  if (status === 'expired') return '已过期'
  if (status === 'invalid') return '不可用'
  return status
}

function reportVisibility() {
  // 与原页区段 v-if 同口径：providers 出错时也显示（错误态要给用户看）
  emit('loaded', {
    visible: !!providersError.value || (providersLoaded.value && hasProvider('drive')),
  })
}

async function loadProviders() {
  providersError.value = ''
  try {
    const res = await storageBindingApi.providers()
    availableProviders.value = res.providers || []
    // 第二绑定入口状态随名单同响应下发：没这份数据就当入口不存在
    driveOauth2.value = res.drive_oauth2 || null
    providersLoaded.value = true
  } catch (e) {
    // 名单拿不到就没法按规则渲染，留错误态给用户重试，不猜、不 fallback 到全显
    providersError.value = humanizeError(e)
  }
  reportVisibility()
}

function reportActiveBinding() {
  if (driveBindings.value.some((b) => b.status === 'active')) emit('activeBinding')
}

async function loadDriveBindings() {
  driveLoading.value = true
  driveError.value = ''
  try {
    const res = await storageBindingApi.list()
    driveBindings.value = (res.bindings || []).filter((b) => b.provider === 'drive')
    removedBindings.value = (res.bindings || []).filter(
      (b) => b.provider === 'pan115' || b.provider === 'quark',
    )
    reportActiveBinding()
  } catch (e) {
    // 加载失败别装成"没绑盘"，留错误态给用户重试
    driveError.value = humanizeError(e)
  } finally {
    driveLoading.value = false
  }
}

async function handleBindDrive2() {
  // 绑定（唯一入口）：共用上面的名字输入框，走新应用 oauth2
  // start 口；无凭证时按钮已置灰，这里再兜一层后端 403 原样展示
  const label = driveLabel.value.trim()
  if (!label) {
    driveFormError.value = '先给这个盘起个名字'
    return
  }
  driveFormError.value = ''
  driveAuthUrl.value = ''
  const win = window.open('about:blank', '_blank')
  drive2Starting.value = true
  try {
    const res = await storageBindingApi.startDriveOAuth2(label)
    if (!res.authorization_url) {
      if (win && !win.closed) win.close()
      driveFormError.value = '服务器没有返回授权地址，请稍后重试'
      return
    }
    driveAuthUrl.value = res.authorization_url
    if (!win || win.closed) {
      driveFormError.value = '弹窗被浏览器拦了，点上面“打开 Google 授权页”手动打开，同意后回来，列表会自动刷新'
      return
    }
    win.location.href = res.authorization_url
    pendingOAuthReturn.value = true
    toast.success('已打开 Google 授权页，同意后回到本页，列表会自动刷新')
  } catch (e) {
    if (win && !win.closed) win.close()
    driveFormError.value = humanizeError(e)
  } finally {
    drive2Starting.value = false
  }
}

async function handleRebindDrive(b: StorageBinding) {
  // 重绑：同名同行原地换凭据；入口必须与建绑时一致（oauth2 行走
  // oauth2 start 口），开窗保手势口径与新绑一字不差
  if (rebindingId.value !== null) return
  rebindingId.value = b.id
  driveFormError.value = ''
  driveAuthUrl.value = ''
  const win = window.open('about:blank', '_blank')
  try {
    const startFn = b.oauth_client === 'oauth2'
      ? storageBindingApi.startDriveOAuth2
      : storageBindingApi.startDriveOAuth
    const res = await startFn(b.account_label, b.id)
    if (!res.authorization_url) {
      if (win && !win.closed) win.close()
      driveFormError.value = '服务器没有返回授权地址，请稍后重试'
      return
    }
    driveAuthUrl.value = res.authorization_url
    if (!win || win.closed) {
      driveFormError.value = '弹窗被浏览器拦了，点上面“打开 Google 授权页”手动打开，同意后回来，列表会自动刷新'
      return
    }
    win.location.href = res.authorization_url
    pendingOAuthReturn.value = true
    toast.success('已打开 Google 授权页，同意新增的写权限后回到本页，列表会自动刷新，转存失败的任务会自动重跑')
  } catch (e) {
    if (win && !win.closed) win.close()
    driveFormError.value = humanizeError(e)
  } finally {
    rebindingId.value = null
  }
}

async function handleVerifyBinding(b: StorageBinding) {
  if (verifyingBinding.value !== null) return
  verifyingBinding.value = b.id
  try {
    const updated = await storageBindingApi.verify(b.id)
    driveBindings.value = driveBindings.value.map((x) => (x.id === b.id ? updated : x))
    reportActiveBinding()
    toast.success(updated.status === 'active' ? '验证通过，这个盘可以用了' : '验证后仍不可用，请重新绑定')
  } catch (err: any) {
    toast.error(toUserMessage(err, 'removeFailed'))
  } finally {
    verifyingBinding.value = null
  }
}

async function handleUnbindDrive(b: StorageBinding) {
  // 两步确认，照抄设备移除的口径（4 秒内未确认自动收回）
  if (unbindingId.value !== null) return
  if (pendingUnbind.value !== b.id) {
    pendingUnbind.value = b.id
    window.setTimeout(() => {
      if (pendingUnbind.value === b.id) pendingUnbind.value = null
    }, 4000)
    return
  }
  pendingUnbind.value = null
  unbindingId.value = b.id
  try {
    await storageBindingApi.remove(b.id)
    toast.success('已解绑')
    await loadDriveBindings()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'removeFailed'))
  } finally {
    unbindingId.value = null
  }
}

function onOAuthReturn() {
  if (!pendingOAuthReturn.value || driveLoading.value) return
  pendingOAuthReturn.value = false
  void loadDriveBindings()
}
function onVisibilityReturn() {
  if (document.visibilityState === 'visible') onOAuthReturn()
}

const summaryMeta = computed(() => {
  if (!providersLoaded.value || !hasProvider('drive')) return ''
  return providerMeta('drive')
})

onMounted(() => {
  void loadProviders()
  void loadDriveBindings()
  void loadContribution()
  window.addEventListener('focus', onOAuthReturn)
  document.addEventListener('visibilitychange', onVisibilityReturn)
})
onUnmounted(() => {
  window.removeEventListener('focus', onOAuthReturn)
  document.removeEventListener('visibilitychange', onVisibilityReturn)
})

defineExpose({
  expandDrive: () => {
    driveExpanded.value = true
  },
  summaryMeta,
})
</script>

<style scoped>
/* 展开面板与行：状态行模具（说明书 2.6）——1px --uhd-l-divider 分隔、
   状态=圆点+文字、操作=白底灰边小胶囊、主按钮=UhdButton 彩虹边框胶囊；
   加载态=旋转圈+灰字（1.6）、空态=虚线卡（2.7） */
.pc-expand {
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  padding: 14px 2px 18px;
  font-family: var(--uhd-font-sans);
}
.bind-sep { border-top: var(--uhd-border-w) solid var(--uhd-l-divider); }
.prov-tools {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}
.prov-desc {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.sx-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
.sx-mini-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.sx-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.sx-spin {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-divider);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 0.8s linear infinite;
}
.sx-err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0;
  padding: 14px 16px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
  font-family: var(--uhd-font-sans);
}
.sx-empty {
  padding: 26px 20px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  text-align: center;
  font-size: var(--uhd-fs-body);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.sx-empty--left { text-align: left; }
.sx-empty p { margin: 0 0 8px; }
.sx-empty p:last-child { margin-bottom: 0; }
.dev-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 64px;
  padding: 10px 0;
}
.dev-row + .dev-row { border-top: var(--uhd-border-w) solid var(--uhd-l-divider); }
.dev-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.dev-name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dev-meta {
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.sx-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  font-size: var(--uhd-fs-caption);
  white-space: nowrap;
}
.sx-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.sx-status--ok { color: var(--uhd-success); }
.sx-status--ok .sx-dot { background: var(--uhd-dot-ok); }
.sx-status--idle { color: var(--uhd-l-text-2); }
.sx-status--idle .sx-dot { background: var(--uhd-dot-idle); }
.sx-status--warn { color: var(--uhd-warn-text-2); }
.sx-status--warn .sx-dot { background: var(--uhd-dot-doing); }
.sx-status--bad { color: var(--uhd-danger); }
.sx-status--bad .sx-dot { background: var(--uhd-dot-bad); }
.dev-remove {
  flex: none;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
  cursor: pointer;
}
.dev-remove:disabled { opacity: 0.5; cursor: not-allowed; }
.dev-remove.confirming { color: var(--uhd-danger); border-color: var(--uhd-danger); font-weight: var(--uhd-fw-semibold); }
.dev-tip {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.dev-tip a { color: var(--uhd-l-link); }
.acct-field { margin-top: 16px; }
.acct-label {
  display: block;
  margin-bottom: 8px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.acct-field input {
  width: 100%;
  height: 46px;
  padding: 0 14px;
  background: var(--uhd-l-input-bg);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-input);
  color: var(--uhd-l-text);
  font-size: 16px;
  outline: none;
  box-sizing: border-box;
}
.acct-field input:focus { border-color: var(--uhd-l-text-3); }
.acct-error {
  margin: 12px 0 0;
  padding: 10px 14px;
  background: rgba(220, 38, 38, 0.07);
  border-radius: 12px;
  color: var(--uhd-danger);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
}
.pc-expand :deep(.uhd-btn) { margin-top: 14px; }
/* 公共贡献池块（PR-4）：挂在每个绑定行下方 */
.pool-block {
  margin: 4px 0 12px;
  padding: 14px 16px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
}
.pool-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.pool-text { flex: 1; min-width: 200px; font-size: var(--uhd-fs-aux); color: var(--uhd-l-text-2); line-height: 1.6; }
.pool-note { font-size: var(--uhd-fs-aux); color: var(--uhd-l-text-3); }
.pool-faq-toggle {
  margin-top: 8px;
  padding: 0;
  background: none;
  border: none;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-link);
  cursor: pointer;
}
.pool-faq { margin-top: 6px; font-size: var(--uhd-fs-aux); color: var(--uhd-l-text-3); line-height: 1.6; }
.pool-faq p { margin: 0; }
</style>
