<template>
  <UhdAccountLayout>
  <div class="op-page">
    <!-- 页头 -->
    <header class="op-head">
      <h1 class="op-title">播放账号</h1>
      <p class="op-sub">开通会员后在这里拿到播放器登录用的账号密码。</p>
    </header>

    <!-- 加载中 -->
    <div v-if="loading && !account" class="op-sec">
      <div class="op-loading" role="status" aria-label="正在加载播放账号信息">
        <span class="op-spinner" aria-hidden="true"></span>正在加载…
      </div>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="accountError && !account" class="op-sec">
      <div class="op-err">
        播放账号信息加载失败：{{ accountError }}<br>
        <button class="op-retry" @click="refreshAccount()">重试</button>
      </div>
    </div>

    <template v-else>
      <!-- 有查看权限：账号密码区（已设置用户首屏第一块就是它，公益说明移到区后） -->
      <template v-if="viewGranted">
        <section class="op-sec">
          <h2 class="op-sec-title">你的播放账号</h2>
          <p class="op-sec-sub">{{ hasEmbyUsername ? '拿着这三样，去播放器里登录' : '自己设用户名和播放密码。用户名设好后不能改。' }}</p>

          <div class="op-rows" :class="{ loading: loading && !accountReloading }">
            <template v-if="accountReloading">
              <div class="op-loading" role="status" aria-label="正在刷新账号信息">
                <span class="op-spinner" aria-hidden="true"></span>正在刷新账号信息…
              </div>
            </template>
            <template v-else>
            <div class="op-row">
              <span class="op-row__main">
                <span class="op-row__title">服务器</span>
                <span class="op-row__desc mono">{{ serverUrl }}</span>
              </span>
              <button type="button" class="copy-btn" title="复制服务器地址" aria-label="复制服务器地址" @click="copyText(serverUrl, 'server')">
                <Check v-if="copiedField === 'server'" :size="14" class="ok" />
                <Copy v-else :size="14" />
              </button>
            </div>
            <form v-if="!hasEmbyUsername" class="cred-form" aria-label="设置播放账号" @submit.prevent="confirmSetCredentials">
              <div class="cred-field">
                <label class="op-label" for="emby-cred-username">用户名</label>
                <input
                  id="emby-cred-username"
                  ref="credUsernameInput"
                  v-model="newUsername"
                  class="cred-input"
                  name="emby_username"
                  autocomplete="off"
                  autocapitalize="off"
                  autocorrect="off"
                  spellcheck="false"
                  maxlength="32"
                  placeholder="字母、数字或下划线"
                  aria-required="true"
                  :aria-invalid="credUsernameInvalid ? 'true' : 'false'"
                  aria-describedby="emby-cred-hint"
                />
              </div>
              <div class="cred-field">
                <label class="op-label" for="emby-cred-password">播放密码</label>
                <input
                  id="emby-cred-password"
                  ref="credPasswordInput"
                  v-model="newPassword"
                  class="cred-input"
                  type="password"
                  name="emby_play_password"
                  autocomplete="new-password"
                  maxlength="64"
                  placeholder="3 到 64 位"
                  aria-required="true"
                  :aria-invalid="credPasswordInvalid ? 'true' : 'false'"
                  aria-describedby="emby-cred-hint"
                />
              </div>
              <div class="cred-actions">
                <p id="emby-cred-hint" class="op-tip cred-hint">{{ credHint }}</p>
                <p v-if="credError" class="modal-error" role="alert">{{ credError }}</p>
                <button type="submit" class="op-btn primary" :disabled="settingPwd">
                  {{ settingPwd ? '保存中' : '保存播放账号' }}
                </button>
              </div>
            </form>
            <template v-else>
            <div class="op-row">
              <span class="op-row__main">
                <span class="op-row__title">用户名</span>
                <span class="op-row__desc mono">{{ embyUsername }}</span>
              </span>
              <button type="button" class="copy-btn" title="复制用户名" aria-label="复制用户名" @click="copyText(embyUsername, 'user')">
                <Check v-if="copiedField === 'user'" :size="14" class="ok" />
                <Copy v-else :size="14" />
              </button>
            </div>
            <div class="op-row op-row-password">
              <span class="op-row__main">
                <span class="op-row__title">播放密码</span>
                <span class="op-row__desc mono">
                  <template v-if="playPwdLoaded">{{ showPlayPassword ? playPasswordText : '••••••••' }}</template>
                  <template v-else-if="hasPlayPassword">••••••••</template>
                  <template v-else>未设置</template>
                </span>
              </span>
              <div class="op-row-actions">
                <button
                  v-if="playPwdLoaded"
                  type="button"
                  class="copy-btn"
                  :title="showPlayPassword ? '隐藏播放密码' : '查看播放密码'"
                  :aria-label="showPlayPassword ? '隐藏播放密码' : '查看播放密码'"
                  @click="showPlayPassword = !showPlayPassword"
                >
                  <Eye v-if="showPlayPassword" :size="14" />
                  <EyeOff v-else :size="14" />
                </button>
                <button
                  v-if="playPwdLoaded"
                  type="button"
                  class="text-btn"
                  aria-label="清除本次记住的播放密码"
                  @click="clearRememberedPassword"
                >
                  清除
                </button>
              </div>
            </div>
            </template>
            </template>
          </div>

          <!-- 改密码入口移到账号区下方：首屏先亮账号，改密是次级动作（弹窗与行为不变） -->
          <div v-if="hasEmbyUsername" class="op-card-actions">
            <button type="button" class="op-mini-btn" @click="openSetPlayPwd">
              {{ hasPlayPassword ? '修改播放密码' : '设置播放密码' }}
            </button>
          </div>

          <p class="op-tip">
            播放密码用于 Emby 客户端登录，和这个网站的登录密码是两回事，互不影响。
          </p>
        </section>

        <!-- 一键导入。没有用户名时方案列表是空的，仍留说明，避免只看 has_password 就当成已经能导入。 -->
        <section v-if="showImportSection" class="op-sec">
          <h2 class="op-sec-title">一键导入到客户端</h2>
          <p class="op-sec-sub">{{ hasEmbyUsername ? '点一下，直接跳到播放器里填好' : '设好播放账号之后才能导入' }}</p>
          <div v-if="hasSchemes" class="scheme-btns">
            <button
              v-for="(url, name) in importSchemes"
              :key="name"
              class="scheme-btn"
              @click="openScheme(url, name)"
            >
              {{ name }}
            </button>
          </div>
          <p class="op-tip">{{ importTip }}</p>
        </section>

        <!-- 多服 -->
        <section v-if="realmCards.length > 1" class="op-sec">
          <h2 class="op-sec-title">我的服</h2>
          <p class="op-sec-sub">每个服有自己的地址和会员</p>
          <div class="realm-list">
            <div v-for="r in realmCards" :key="r.id" class="op-row">
              <span class="op-row__main">
                <span class="op-row__title">{{ r.name }}</span>
                <span v-if="r.view_granted" class="op-row__desc mono">{{ r.base_url || '管理员还没填这个服的地址' }}</span>
                <span v-else class="op-row__desc">{{ r.is_free ? '公益资格在期或存量凭证在效可见' : '开通会员后可见' }}</span>
              </span>
              <span v-if="r.is_free" class="badge free">公益服</span>
              <span v-else-if="r.subscribed" class="badge ok">{{ daysLeftText(r) }}</span>
              <span v-else class="badge off">未开通</span>
              <button
                v-if="r.view_granted && r.base_url"
                class="copy-btn"
                :title="'复制服务器地址：' + r.name"
                :aria-label="'复制服务器地址：' + r.name"
                @click="copyText(r.base_url, `realm-${r.id}`)"
              >
                <Check v-if="copiedField === `realm-${r.id}`" :size="13" class="ok" />
                <Copy v-else :size="13" />
              </button>
            </div>
          </div>
        </section>

        <!-- 正在播放：旧 PlaybackSessions 一比一恢复（#783 删页面时丢了入口） -->
        <section class="op-sec" aria-label="正在播放">
          <div class="op-sec-headrow">
            <h2 class="op-sec-title">正在播放</h2>
            <button
              class="refresh-btn"
              :class="{ 'is-loading': sessionsLoading }"
              :disabled="sessionsLoading"
              :aria-label="sessionsLoading ? '正在刷新播放列表' : '刷新正在播放'"
              @click="loadSessions"
            >
              {{ sessionsLoading ? '刷新中' : '刷新' }}
            </button>
          </div>
          <p class="op-sec-sub">这个账号正在哪台设备上播放，一键远程停掉</p>

          <div v-if="sessionsLoading" class="op-loading" role="status" aria-label="正在加载播放列表">
            <span class="op-spinner" aria-hidden="true"></span>正在加载…
          </div>

          <div v-else-if="sessionsError" class="op-err">
            正在播放没加载出来<br>
            <button class="op-retry" @click="loadSessions">重试</button>
          </div>

          <div v-else-if="sessions.length === 0" class="op-empty">
            <span class="op-empty__icon"><MonitorSmartphone :size="26" aria-hidden="true" /></span>
            <p class="op-empty__title">现在没有正在播放的</p>
            <p class="op-empty__desc">在任意设备上开始播放后，会在这里显示并可远程结束</p>
          </div>

          <div v-else class="op-session-list">
            <div v-for="s in sessions" :key="s.session_key" class="op-session-row">
              <div class="op-session-icon">
                <MonitorSmartphone :size="17" aria-hidden="true" />
              </div>

              <div class="op-session-main">
                <div class="op-session-title">
                  <span class="op-session-name" :title="s.item">{{ s.item }}</span>
                  <span class="badge type">{{ typeLabel(s.item_type) }}</span>
                  <span v-if="s.is_paused" class="badge paused">已暂停</span>
                </div>
                <div class="op-session-meta">
                  <span>{{ s.device || '未知设备' }}</span>
                  <span v-if="s.client" class="meta-sep">·</span>
                  <span v-if="s.client">{{ s.client }}</span>
                  <span v-if="s.play_method" class="meta-sep">·</span>
                  <span v-if="s.play_method" class="method">{{ s.play_method === 'Transcode' ? '转码' : '直连' }}</span>
                  <span v-if="s.remote_addr" class="meta-sep">·</span>
                  <span v-if="s.remote_addr" class="addr"><Wifi :size="11" aria-hidden="true" />{{ s.remote_addr }}</span>
                </div>
                <div class="op-session-progress">
                  <div class="bar" role="progressbar" :aria-valuenow="Math.round(s.progress)" aria-valuemin="0" aria-valuemax="100" :aria-label="'播放进度 ' + Math.round(s.progress) + '%'"><div class="bar-fill" :style="{ transform: 'scaleX(' + Math.min(100, Math.max(0, s.progress)) / 100 + ')' }" /></div>
                  <span class="bar-text">{{ Math.round(s.progress) }}%<template v-if="s.updated_at"> · {{ fmtTime(s.updated_at) }}</template></span>
                </div>
              </div>

              <button
                class="op-stop-btn"
                :disabled="stopping === s.session_key"
                :aria-label="'结束播放：' + (s.item || s.session_key)"
                @click="confirmStopSession(s)"
              >
                <CircleStop :size="13" aria-hidden="true" />
                {{ stopping === s.session_key ? '结束中' : '结束' }}
              </button>
            </div>
          </div>
        </section>

        <!-- 怎么连：小白指引（步骤行模具） -->
        <section class="op-sec">
          <h2 class="op-sec-title">怎么连上？</h2>
          <div class="guide-steps">
            <div class="guide-step">
              <span class="step-num">1</span>
              <div class="guide-step__main">
                <b>{{ hasEmbyUsername ? '复制上面三样' : '先设好播放账号' }}</b>
                <p v-if="hasEmbyUsername">服务器地址、用户名、播放密码，点右边按钮复制。</p>
                <p v-else>填一个用户名和播放密码。用户名设好就不能改，密码以后可以再改。</p>
              </div>
            </div>
            <div class="guide-step">
              <span class="step-num">2</span>
              <div class="guide-step__main">
                <b>打开播放器</b>
                <p>比如 Infuse、Forward，选"添加 Emby 服务器"。</p>
              </div>
            </div>
            <div class="guide-step">
              <span class="step-num">3</span>
              <div class="guide-step__main">
                <b>粘贴登录</b>
                <p>把三样粘贴进去，登录就能看了。嫌麻烦就用上面的一键导入。</p>
              </div>
            </div>
          </div>
        </section>
      </template>

      <!-- 公益服说明（原在账号区之前，会把已设置用户的账号区挤到首屏下方，现移到账号区之后） -->
      <div v-if="isFreeRealm" class="op-sec">
        <h2 class="op-sec-title">接入方式</h2>
        <div class="free-note">
          <span class="badge free">公益服 · 免费开放</span>
          <p>{{ realmNote }}</p>
        </div>
      </div>

      <!-- 没查看权限：解锁入口（view_permission 后端恒下发，显隐必须以 granted 为准，
           只判对象存在会让已授权用户也看到本区块） -->
      <div v-else-if="viewPermission && !viewGranted" class="op-sec">
        <h2 class="op-sec-title">还没有查看权限</h2>
        <template v-if="viewPermission.realm_free">
          <p class="unlock-desc">
            公益服查看已随公益资格自动开通，不用再单独买凭证。
            续上公益资格，这里的服务器地址和账号就会出现。
          </p>
        </template>
        <template v-else>
          <p class="unlock-desc">服务器地址和账号只向会员开放，开通后回来这里查看。</p>
          <button class="op-btn primary" @click="router.push('/game/account/subscribe')">去开通会员</button>
        </template>
      </div>

      <div v-else-if="!viewGranted" class="op-sec">
        <div class="op-err">
          播放账号信息加载失败，再试一次<br>
          <button class="op-retry" @click="refreshAccount()">重试</button>
        </div>
      </div>
    </template>
  </div>

  <!-- 修改播放密码（用户名已设好，只改密码） -->
  <Teleport to="body">
    <div v-if="showSetPlayPwd" class="modal-mask" @click.self="closeSetPlayPwd()" @keydown="onModalKeydown">
      <div
        class="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="set-play-pwd-title"
        aria-describedby="set-play-pwd-desc"
      >
        <h3 id="set-play-pwd-title" class="modal-title">{{ hasPlayPassword ? '修改播放密码' : '设置播放密码' }}</h3>
        <p id="set-play-pwd-desc" class="modal-desc">这个密码用在 Emby 客户端里登录，和网站登录密码无关。密码长度 3-64 位。</p>
        <form @submit.prevent="confirmSetPassword">
          <label class="modal-label" for="set-play-pwd-input">新的播放密码</label>
          <input
            id="set-play-pwd-input"
            ref="setPlayPwdInput"
            v-model="playPwd"
            type="password"
            class="modal-input"
            autocomplete="new-password"
            maxlength="64"
            aria-required="true"
            :aria-invalid="pwdError ? 'true' : 'false'"
          />
          <p v-if="pwdError" class="modal-error" role="alert">{{ pwdError }}</p>
          <div class="modal-actions">
            <button type="button" class="op-btn ghost" @click="closeSetPlayPwd()">取消</button>
            <button type="submit" class="op-btn primary" :disabled="settingPwd">
              {{ settingPwd ? '保存中' : '确认' }}
            </button>
          </div>
        </form>
        <button type="button" class="modal-close" aria-label="关闭" @click="closeSetPlayPwd()">×</button>
      </div>
    </div>
  </Teleport>

  <!-- 一键导入前补明文密码（接口不回传密码） -->
  <Teleport to="body">
    <div v-if="showImportPwd" class="modal-mask" @click.self="closeImportPwd()" @keydown="onModalKeydown">
      <div
        class="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-pwd-title"
        aria-describedby="import-pwd-desc"
      >
        <h3 id="import-pwd-title" class="modal-title">填写播放密码</h3>
        <p id="import-pwd-desc" class="modal-desc">{{ importPwdDesc }}</p>
        <form @submit.prevent="confirmImportPassword">
          <label class="modal-label" for="import-pwd-input">播放密码</label>
          <input
            id="import-pwd-input"
            ref="importPwdInput"
            v-model="importPwd"
            type="password"
            class="modal-input"
            autocomplete="current-password"
            maxlength="64"
            aria-required="true"
            :aria-invalid="importPwdError ? 'true' : 'false'"
          />
          <p v-if="importPwdError" class="modal-error" role="alert">{{ importPwdError }}</p>
          <div class="modal-actions">
            <button type="button" class="op-btn ghost" @click="closeImportPwd()">取消</button>
            <button type="submit" class="op-btn primary" :disabled="settingPwd">
              {{ settingPwd ? '处理中' : (hasPlayPassword ? '带上密码导入' : '保存并导入') }}
            </button>
          </div>
        </form>
        <button type="button" class="modal-close" aria-label="关闭" @click="closeImportPwd()">×</button>
      </div>
    </div>
  </Teleport>


  <!-- 结束播放确认弹窗（二次确认，防手滑） -->
  <Teleport to="body">
    <div v-if="sessionToStop" class="modal-mask" @click.self="closeStopSession()" @keydown="onModalKeydown">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="确认结束播放">
        <h3 class="modal-title">结束这次播放？</h3>
        <p class="modal-desc">
          将在「<strong>{{ sessionToStop.device || '未知设备' }}</strong>」上停掉
          「<strong>{{ sessionToStop.item }}</strong>」的播放。
        </p>
        <p v-if="stopError" class="modal-error" role="alert">{{ stopError }}</p>
        <div class="modal-actions">
          <button class="op-btn ghost" :disabled="!!stopping" @click="closeStopSession()">取消</button>
          <button class="op-btn primary" :disabled="!!stopping" @click="doStopSession">
            {{ stopping ? '结束中' : (stopError ? '重试' : '确认结束') }}
          </button>
        </div>
        <button type="button" class="modal-close" aria-label="关闭" @click="closeStopSession()">×</button>
      </div>
    </div>
  </Teleport>

  </UhdAccountLayout>
</template>

<script setup lang="ts">
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Check, CircleStop, Copy, Eye, EyeOff, MonitorSmartphone, Wifi } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import { assignImportScheme, embyImportTip, withImportPassword } from '@/composables/usePlayerSchemes'
import { useClipboard } from '@/composables/useClipboard'
import { humanizeError } from '@/composables/usePanelApi'
import { toUserMessage } from '@/utils/toUserMessage'
import {
  embyApi,
  type AccountCard, type AccountRealmCard, type MyPlaybackSession, type ViewPermission,
} from '@/api'
const router = useRouter()
const toast = useToast()
// R41：复制走全站唯一切口（照抄 结伴页 同一模式）
const { copy } = useClipboard()

const loading = ref(true)
const account = ref<AccountCard | null>(null)
const accountError = ref('')
/** R50：空占位符（与 copyText 的空值判定共用一处，改一处不漏另一处） */
const EMPTY_DASH = '—'

/** R50：后端账号卡实际下发、但 AccountCard 类型尚未收录的字段（渐进补齐，避免满屏 as any） */
interface AccountCardExtra {
  /** 兼容字段：部分部署 server_url 与 base_url 并存 */
  server_url?: string
  /** 公益服标记（顶层兼容字段） */
  realm_free?: boolean
  /** 公益服说明（顶层兼容字段） */
  realm_note?: string
  /** 接入的是外部 Emby 时不下发导入方案 */
  external?: boolean
}
/** R50：view_permission 里后端可能多带的字段 */
interface ViewPermissionExtra {
  realm_id?: number | null
  realm_note?: string
}
/** R52-CI：AccountCardExtra 是弱类型（全可选字段），直接把 AccountCard 往它上面靠
    会触发"无公共属性"报错；与 AccountCard 取交集才是卡的完整形态 */
type AccountCardFull = AccountCard & AccountCardExtra
const accountExtra = computed((): AccountCardFull => (account.value ?? {}) as AccountCardFull)
/** R50：账号已就绪时的刷新（重试按钮场景）——op-rows 显示骨架而不是整块变暗 */
const accountReloading = computed(() => loading.value && !!account.value)
// 查看权限：后端账号卡自带 view_permission（没权限时 200 返回卡，granted=false，只给解锁信息不下发地址）
// 和 ProfileView 同一口径：从卡里派生，不单独调接口（后端没有 /view-permission 这类端点）
const viewPermission = computed(() => (account.value?.view_permission || null) as ((ViewPermission & ViewPermissionExtra) | null))

const copiedField = ref('')
const showPlayPassword = ref(false)
const playPasswordText = ref('')
/** 本页是否已有明文播放密码。接口不回传密码，只有刚设好或导入前填过才为 true。 */
const playPwdLoaded = ref(false)
const showSetPlayPwd = ref(false)
const playPwd = ref('')
const pwdError = ref('')
const settingPwd = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const credError = ref('')
const credUsernameInvalid = ref(false)
const credPasswordInvalid = ref(false)
const credUsernameInput = ref<HTMLInputElement | null>(null)
const credPasswordInput = ref<HTMLInputElement | null>(null)
const setPlayPwdInput = ref<HTMLInputElement | null>(null)
const showImportPwd = ref(false)
const importPwd = ref('')
const importPwdError = ref('')
const importPwdInput = ref<HTMLInputElement | null>(null)
const pendingScheme = ref<{ url: string; name: string } | null>(null)

const USERNAME_RE = /^[a-zA-Z0-9_]{3,32}$/

watch(newUsername, () => { credUsernameInvalid.value = false })
watch(newPassword, () => { credPasswordInvalid.value = false })

/** 是否有查看权限：以卡里 view_permission.granted 为准（和 ProfileView 同一口径） */
const viewGranted = computed(() => !!viewPermission.value?.granted)
const hasPlayPassword = computed(() => !!account.value?.has_password)

const serverUrl = computed(() => accountExtra.value.server_url || account.value?.base_url || EMPTY_DASH)
/** 空串不当成账号，也不回退到网站登录名，更不展示 emby_ 占位。 */
const embyUsername = computed(() => (account.value?.emby_username || '').trim())
const hasEmbyUsername = computed(() => embyUsername.value.length > 0)
/** 三态：还没用户名 / 有账号但本页没有明文密码 / 可以带密码导入。只看用户名，不看 has_password。 */
const importTip = computed(() => embyImportTip(hasEmbyUsername.value, playPwdLoaded.value))
const credHint = computed(() => (
  hasPlayPassword.value
    ? '播放用户名还没有。填一个用户名，并设播放密码。用户名设好后不能改。密码长度 3-64 位。'
    : '用户名只能包含字母、数字、下划线，长度 3-32 位，设好后不能改。密码长度 3-64 位，和网站登录密码无关。'
))
const importPwdDesc = computed(() => (
  hasPlayPassword.value
    ? '把播放密码填上，这次导入会带过去。填错可以点清除再填，不会改掉已经设好的密码。'
    : '还没设过播放密码。确认后会记成你的播放密码，并带进这次导入。'
))
const isFreeRealm = computed(() => !!accountExtra.value.realm_free || !!viewPermission.value?.realm_free)
const realmNote = computed(() => accountExtra.value.realm_note || viewPermission.value?.realm_note || '')
const realmCards = computed((): AccountRealmCard[] => {
  const r = account.value?.realms
  return Array.isArray(r) ? r : []
})
const importSchemes = computed((): Record<string, string> => {
  const s = account.value?.import_schemes
  return s && typeof s === 'object' ? s : {}
})
const hasSchemes = computed(() => Object.keys(importSchemes.value).length > 0)
const isExternalEmby = computed(() => !!accountExtra.value.external)
/** 没有用户名时方案是空对象，导入区仍要出现，用来说明先去设账号。外部 Emby 本来就没有方案。 */
const showImportSection = computed(() => hasSchemes.value || (!hasEmbyUsername.value && !isExternalEmby.value))

async function refreshAccount(quiet = false): Promise<boolean> {
  if (!quiet) loading.value = true
  accountError.value = ''
  try {
    const res = await embyApi.getAccountCard()
    account.value = res as AccountCard
    return true
  } catch (e: any) {
    // 没权限时后端直接 200 返回带 view_permission 的卡（granted=false），不会 403；
    // 这里只处理真正的加载失败（网络/鉴权），和 ProfileView 同一口径
    accountError.value = humanizeError(e)
    return false
  } finally {
    loading.value = false
  }
}

/** R41：裸调 navigator.clipboard?.writeText 在 HTTP 自建部署下是 undefined，
 *  点复制毫无反应还不告诉用户 —— 改用 useClipboard（Clipboard API + execCommand 兜底，
 *  成功失败都给人话反馈；照抄 结伴页 同一切口） */
let copyTimer: number | undefined
async function copyText(text: string, field: string) {
  if (!text || text === EMPTY_DASH) {
    toast.info('没有可复制的内容')
    return
  }
  const label = field === 'server' ? '服务器地址' : field === 'user' ? '用户名' : '地址'
  const ok = await copy(text, label)
  if (!ok) return
  copiedField.value = field
  window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => { if (copiedField.value === field) copiedField.value = '' }, 1800)
}

function rememberPlayPassword(password: string) {
  playPasswordText.value = password
  playPwdLoaded.value = true
  showPlayPassword.value = false
}

function clearRememberedPassword() {
  playPasswordText.value = ''
  playPwdLoaded.value = false
  showPlayPassword.value = false
}

function passwordError(password: string): string {
  if (password.length < 3 || password.length > 64) return '密码长度需为 3-64 位'
  return ''
}

function focusCredUsername() {
  nextTick(() => {
    credUsernameInput.value?.scrollIntoView({ block: 'center' })
    credUsernameInput.value?.focus()
  })
}

async function confirmSetCredentials() {
  if (settingPwd.value) return
  credError.value = ''
  const username = newUsername.value.trim()
  const password = newPassword.value
  credUsernameInvalid.value = !USERNAME_RE.test(username)
  credPasswordInvalid.value = passwordError(password) !== ''
  if (credUsernameInvalid.value) {
    credError.value = '用户名只能包含字母、数字、下划线，长度 3-32 位'
    focusCredUsername()
    return
  }
  if (credPasswordInvalid.value) {
    credError.value = passwordError(password)
    nextTick(() => credPasswordInput.value?.focus())
    return
  }
  settingPwd.value = true
  try {
    // 用户名还是空的才带 username。已有用户名只改密码，不传 username（空串也会被当成改名）。
    const res = await embyApi.setPassword(password, username)
    rememberPlayPassword(password)
    newUsername.value = ''
    newPassword.value = ''
    const refreshed = await refreshAccount(true)
    const saved = (account.value?.emby_username || res?.emby_username || '').trim()
    if (saved && saved !== username) {
      toast.info('服务器保存的用户名和你填的不一样，设好后不能再改')
    } else if (refreshed) {
      toast.success('播放账号已设好')
    } else {
      toast.info('账号已保存，页面没刷新成功，重新进入本页就能看到')
    }
  } catch (e) {
    const msg = humanizeError(e)
    credError.value = msg
    // 用户名其实已经有了（重复提交）时，拉一次卡切到只读
    const refreshed = await refreshAccount(true)
    if (refreshed && hasEmbyUsername.value) toast.info(msg)
  } finally {
    settingPwd.value = false
  }
}

async function confirmSetPassword() {
  if (settingPwd.value) return
  pwdError.value = ''
  const password = playPwd.value
  const err = passwordError(password)
  if (err) {
    pwdError.value = err
    nextTick(() => setPlayPwdInput.value?.focus())
    return
  }
  settingPwd.value = true
  try {
    // 用户名已存在，只传密码。相同用户名可以再传，这里不传，避免发成空串或另一个名字。
    await embyApi.setPassword(password)
    rememberPlayPassword(password)
    toast.success('播放密码已更新')
    closeSetPlayPwd()
    await refreshAccount(true)
  } catch (e) {
    pwdError.value = humanizeError(e)
  } finally {
    settingPwd.value = false
  }
}

async function jumpScheme(url: string, appName: string, password: string) {
  // R50：空地址直接提示，不跳转（空串 location.href 会刷新当前页）
  if (!url) {
    toast.info('播放地址无效')
    return
  }
  const finalUrl = withImportPassword(url, password)
  // R41：没装 App 时 iOS 跳转是死路 —— 跳转前先说一声，提醒没装的去应用商店
  toast.info(`正在打开${appName}，没装的话请先去应用商店安装`)
  // R50：给 toast 一点展示时间再跳转，否则提示来不及被看到
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 600))
  // 与发现页「去看」同一跳转：只接受 App scheme，拒绝 http(s)
  if (!assignImportScheme(finalUrl)) toast.info('播放地址无效')
}

async function openScheme(url: string, appName = '播放器') {
  if (!url) {
    toast.info('播放地址无效')
    return
  }
  if (!hasEmbyUsername.value) {
    toast.info('先设好用户名和播放密码，再导入')
    focusCredUsername()
    return
  }
  // 本页还没有明文密码：先问一次，再拼进 scheme。问过之后复用 playPwdLoaded。
  if (!playPwdLoaded.value) {
    pendingScheme.value = { url, name: appName }
    importPwdError.value = ''
    importPwd.value = ''
    recordTrigger()
    showImportPwd.value = true
    nextTick(() => importPwdInput.value?.focus())
    return
  }
  await jumpScheme(url, appName, playPasswordText.value)
}

async function confirmImportPassword() {
  if (settingPwd.value) return
  importPwdError.value = ''
  const password = importPwd.value
  const pending = pendingScheme.value
  if (!pending) {
    closeImportPwd()
    return
  }
  const err = passwordError(password)
  if (err) {
    importPwdError.value = err
    nextTick(() => importPwdInput.value?.focus())
    return
  }
  // 已经设过密码：只拿来拼链接，不把用户填错的值写回服务器。
  // 还没设过、但用户名已经有了：只提交密码。用户名还是空的时走上面的建账号表，不进这个弹窗。
  settingPwd.value = true
  try {
    if (!hasPlayPassword.value) {
      await embyApi.setPassword(password)
      await refreshAccount(true)
    }
    rememberPlayPassword(password)
    closeImportPwd()
    await jumpScheme(pending.url, pending.name, password)
  } catch (e) {
    importPwdError.value = humanizeError(e)
  } finally {
    settingPwd.value = false
  }
}

function daysLeft(r: AccountRealmCard): number | null {
  // 剩余天数以后端下发的 days_left 为准（与「我的」页同口径）；后端没给才回退自算
  // （自算曾把 UTC 裸时间戳按本地时间再解释，与后端差 1 天，2026-10-23 统一口径）
  if (typeof r.days_left === 'number') return r.days_left
  if (!r.end_date) return 0
  const t = new Date(r.end_date).getTime()
  // R50：非法日期返回 null（模板显示"—"），不再渲染 NaN
  if (!isFinite(t)) return null
  return Math.max(0, Math.ceil((t - Date.now()) / 86400000))
}

/** R50：会员剩余天数文案（daysLeft 只调一次；null → "—" 不显示"会员已到期"） */
function daysLeftText(r: AccountRealmCard): string {
  const d = daysLeft(r)
  if (d === null) return EMPTY_DASH
  return d > 0 ? `会员剩 ${d} 天` : '会员已到期'
}

// ===== 正在播放（旧 PlaybackSessions.vue 一比一恢复，放接线员页） =====
// 数据源：GET /api/user/emby/sessions；结束：DELETE /api/user/emby/sessions/{key}
// 后端两个端点 #783 后都还在（backend/emby_server/portal.py），前端 wrapper 在 api/index.ts
const sessions = ref<MyPlaybackSession[]>([])
const sessionsLoading = ref(true)
const sessionsError = ref(false)
const stopping = ref<string | null>(null)
const sessionToStop = ref<MyPlaybackSession | null>(null)
/** R50：结束播放失败时的弹窗内错误（失败不自动关弹窗，展示错误+重试） */
const stopError = ref('')

function typeLabel(t?: string | null) {
  // R41：Episode 标"单集" —— 之前错标成"剧集"，跟 Series 重了；与 CalendarView 同名函数对齐
  return ({ Movie: '电影', Series: '剧集', Episode: '单集' } as Record<string, string>)[t || ''] || '影片'
}

function fmtTime(iso?: string | null) {
  if (!iso) return EMPTY_DASH
  const date = new Date(iso)
  // R50：非法日期直接返回占位，不渲染 NaN
  if (!isFinite(date.getTime())) return EMPTY_DASH
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

async function loadSessions() {
  sessionsLoading.value = true
  sessionsError.value = false
  try {
    const res = await embyApi.getSessions()
    sessions.value = res.sessions || []
  } catch {
    sessions.value = []
    sessionsError.value = true
  } finally {
    sessionsLoading.value = false
  }
}

function confirmStopSession(session: MyPlaybackSession) {
  recordTrigger()
  stopError.value = ''
  sessionToStop.value = session
  focusOpenModalCard()
}

/** Esc 关闭各确认弹窗（照 WishlistView 模式） */
function onEsc(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  if (showImportPwd.value) closeImportPwd()
  else if (showSetPlayPwd.value) closeSetPlayPwd()
  else if (sessionToStop.value) closeStopSession()
}

/** R36：简易 focus trap —— Tab 在对话框内循环，不跑出对话框（照抄 WishlistView R27 标杆） */
function onModalKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab') return
  const card = (e.currentTarget as HTMLElement).querySelector('.modal-card')
  if (!card) return
  const els = Array.from(
    card.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => el.offsetParent !== null)
  if (els.length === 0) return
  const first = els[0]
  const last = els[els.length - 1]
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}

/** R36：弹窗打开初始聚焦 —— 焦点放进对话框首个可聚焦元素（Teleport 到 body 后用 query 定位） */
function focusOpenModalCard() {
  nextTick(() => {
    const el = document.querySelector<HTMLElement>(
      '.modal-mask .modal-card button:not([disabled]), .modal-mask .modal-card input:not([disabled])',
    )
    el?.focus()
  })
}
/** R38 P2-2：弹窗关闭后焦点回到触发按钮（setPlayPwd / stopSession 两处共用） */
const lastTrigger = ref<HTMLElement | null>(null)
function recordTrigger() { lastTrigger.value = document.activeElement as HTMLElement | null }
function restoreTriggerFocus() {
  const t = lastTrigger.value
  // R50：触发按钮仍在 DOM 才还回去（会话行被移除后 focus 会静默掉回 body）
  if (t && document.contains(t)) {
    t.focus?.()
    return
  }
  // 触发按钮不在了：焦点落到仍打开的弹窗内关闭按钮/标题（标题补 tabindex="-1" 使其可聚焦）
  const card = document.querySelector<HTMLElement>('.modal-mask .modal-card')
  const target = card?.querySelector<HTMLElement>('.op-btn.ghost') || card?.querySelector<HTMLElement>('.modal-title')
  if (target) {
    if (target.tagName === 'H3') target.setAttribute('tabindex', '-1')
    target.focus?.()
  }
}
function closeSetPlayPwd() {
  showSetPlayPwd.value = false
  playPwd.value = ''
  pwdError.value = ''
  restoreTriggerFocus()
}
function closeImportPwd() {
  showImportPwd.value = false
  importPwd.value = ''
  importPwdError.value = ''
  pendingScheme.value = null
  restoreTriggerFocus()
}
function closeStopSession() {
  // R50：先落焦点再关弹窗——此时弹窗仍在 DOM，触发按钮若已被移除可落到弹窗关闭按钮/标题
  restoreTriggerFocus()
  sessionToStop.value = null
}

/** R41：两个 Teleport 弹窗（设置密码 / 结束播放确认）—— 任一打开都锁住背后页面滚动。
 *  代码路径上两者互斥（弹窗打开时页面被遮罩盖住点不到别的），
 *  但用 anyModalOpen 统一 watch 而不是各写一遍，万一叠加也锁不死：三个全关才释放。 */
const anyModalOpen = computed(
  () => showSetPlayPwd.value || showImportPwd.value || sessionToStop.value !== null,
)
/** R50：body 滚动锁——保存打开前的值，关闭时恢复（避免与其他锁滚动逻辑互相覆盖） */
let savedBodyOverflow = ''
watch(anyModalOpen, (open) => {
  if (open) {
    savedBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = savedBodyOverflow
  }
})
function openSetPlayPwd() {
  // R37 P3-9：重开弹窗清掉旧错误文案
  pwdError.value = ''
  playPwd.value = ''
  recordTrigger()
  showSetPlayPwd.value = true
  nextTick(() => setPlayPwdInput.value?.focus())
}

async function doStopSession() {
  const session = sessionToStop.value
  if (!session) return
  stopping.value = session.session_key
  stopError.value = ''
  try {
    await embyApi.stopSession(session.session_key)
    toast.success(`已结束「${session.device || '未知设备'}」上的播放`)
    sessions.value = sessions.value.filter((s) => s.session_key !== session.session_key)
    // R50：成功才关弹窗
    closeStopSession()
  } catch (err: unknown) {
    // Batch G：中文判断逻辑已收敛进 toUserMessage（中文 detail 透传），不再内联
    // R41：结束播放失败走 stopFailed —— "暂时没能保存"跟"结束播放"动作对不上
    // R50：失败不自动关弹窗，错误留在弹窗内展示并可重试（不再 toast）
    stopError.value = toUserMessage(err, 'stopFailed')
  } finally {
    stopping.value = null
  }
}

onMounted(() => {
  void refreshAccount()
  window.addEventListener('keydown', onEsc)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onEsc)
  // R41：清掉复制提示的定时器（照抄 结伴页 模式）
  window.clearTimeout(copyTimer)
  // R41：卸载时释放 body 滚动锁，避免锁死
  document.body.style.overflow = savedBodyOverflow
})

// 有查看权限才拉正在播放（解锁后翻成 granted 也会触发一次）
watch(viewGranted, (granted) => {
  if (granted) void loadSessions()
}, { immediate: true })
</script>
<style scoped>
/* 播放账号（UHD 办事面）：平面分组＋发丝线＋状态行模具；只换布局骨架，
   业务脚本一字未动。数值出自说明书 2.2/2.6/2.7/2.8 与登录页实测（输入框）。 */
.op-page {
  max-width: var(--uhd-container-app);
  margin: 0 auto;
  font-family: var(--uhd-font-sans);
  color: var(--uhd-l-text);
}

/* 页头（与 UhdAccountLayout 头同口径：H1 34/800＋副文 14 灰） */
.op-head { margin-bottom: 4px; }
.op-title {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-h1-app);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: var(--uhd-lh-tight);
}
.op-sub { margin: 8px 0 0; font-size: 14px; color: var(--uhd-l-text-2); }

/* 分区（说明书 2.6 分组：H2 24/800 领起） */
.op-sec { margin-top: 34px; }
.op-sec-title {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 24px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
}
.op-sec-sub { margin: 6px 0 0; font-size: 14px; color: var(--uhd-l-text-2); }
.op-sec-headrow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

/* 状态行组：行高 64–72、标题 16 粗、副文 14 灰、行间 1px 发丝线 */
.op-rows { margin-top: 14px; border-top: 1px solid var(--uhd-l-divider); }
.op-rows.loading { opacity: 0.6; pointer-events: none; }
.op-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 68px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--uhd-l-divider);
  box-sizing: border-box;
}
.op-row__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.op-row__title { font-size: 16px; font-weight: var(--uhd-fw-semibold); }
.op-row__desc {
  font-size: 14px;
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.op-row__desc.mono {
  font-family: var(--uhd-font-mono);
  font-size: 13.5px;
  color: var(--uhd-l-text);
}
.op-row-actions { display: flex; align-items: center; gap: 4px; flex: none; }

/* 行内图标钮（复制/显隐）：小胶囊白底灰边，视觉 38px */
.copy-btn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 38px;
  height: 38px;
  padding: 0 10px;
  border: 1px solid var(--uhd-l-border);
  border-radius: 999px;
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text-2);
  cursor: pointer;
}
.copy-btn .ok { color: var(--uhd-success); }
.copy-btn:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }
/* 危险文字钮（说明书 2.2：红色文字、不填充） */
.text-btn {
  flex: none;
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-danger);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 10px 8px;
  min-height: 44px;
}
.text-btn:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }

/* 次按钮小胶囊（改密码入口/重试/刷新同款） */
.op-mini-btn,
.op-retry,
.refresh-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 38px;
  padding: 0 18px;
  border: 1px solid var(--uhd-l-border);
  border-radius: 999px;
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-family: var(--uhd-font-sans);
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
  white-space: nowrap;
}
.op-mini-btn:focus-visible,
.op-retry:focus-visible,
.refresh-btn:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }
.refresh-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.op-card-actions { margin-top: 14px; }
.op-err .op-retry { margin-top: 12px; }

/* 设账号表单（行内表单：标签 14＋登录页同款输入框 46 高/圆角 23/浅灰底） */
.cred-form { border-bottom: 1px solid var(--uhd-l-divider); padding: 18px 2px 20px; }
.cred-field { margin-bottom: 14px; }
.op-label {
  display: block;
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  margin: 0 0 8px;
}
.cred-input {
  width: 100%;
  box-sizing: border-box;
  height: 46px;
  padding: 0 16px;
  font-size: 16px;
  font-family: var(--uhd-font-mono);
  border: 1px solid #e4e4e7;
  border-radius: 23px;
  background: #f4f4f5;
  color: var(--uhd-l-text);
}
.cred-input::placeholder { color: var(--uhd-l-text-3); }
.cred-input:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 1px; }
.cred-actions .op-btn { width: 100%; }
.cred-hint { margin: 0 0 12px; }
.cred-actions .modal-error { margin: 0 0 10px; }

.op-tip {
  font-size: 13px;
  color: var(--uhd-l-text-2);
  line-height: 1.7;
  margin: 12px 2px 0;
}
.unlock-desc { font-size: 14px; line-height: 1.7; margin: 10px 0 16px; }

/* 徽标 chip */
.badge {
  display: inline-flex;
  align-items: center;
  flex: none;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
}
.badge.free { background: rgba(37, 99, 235, 0.08); color: var(--uhd-l-link); border: 1px solid rgba(37, 99, 235, 0.28); }
.badge.ok { background: rgba(22, 163, 74, 0.1); color: var(--uhd-success); border: 1px solid rgba(22, 163, 74, 0.3); }
.badge.off { background: var(--uhd-l-input-bg); color: var(--uhd-l-text-2); border: 1px solid var(--uhd-l-border); }
.badge.type { background: rgba(37, 99, 235, 0.08); color: var(--uhd-l-link); border: 1px solid rgba(37, 99, 235, 0.28); }
.badge.paused { background: var(--uhd-warn-bg); color: var(--uhd-warn-text); border: 1px solid rgba(217, 119, 6, 0.35); }

/* 多服：与凭据行同组形态 */
.realm-list { margin-top: 14px; border-top: 1px solid var(--uhd-l-divider); }

/* 一键导入方案钮（次按钮胶囊） */
.scheme-btns { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
.scheme-btn {
  height: 48px;
  padding: 0 22px;
  border: 1px solid var(--uhd-l-border);
  border-radius: 999px;
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-family: var(--uhd-font-sans);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.scheme-btn:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }

/* 步骤行（说明书 2.6 步骤行变体：序号圆＋标题＋说明） */
.guide-steps { margin-top: 14px; border-top: 1px solid var(--uhd-l-divider); }
.guide-step {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 14px 2px;
  border-bottom: 1px solid var(--uhd-l-divider);
}
.step-num {
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--uhd-l-text);
  color: var(--uhd-l-surface);
  font-size: 14px;
  font-weight: var(--uhd-fw-bold);
  display: flex;
  align-items: center;
  justify-content: center;
}
.guide-step__main { min-width: 0; }
.guide-step b { font-size: 15px; font-weight: var(--uhd-fw-semibold); }
.guide-step p { font-size: 14px; color: var(--uhd-l-text-2); margin: 3px 0 0; line-height: 1.6; }

/* 正在播放（状态行会话变体：图标块＋两行信息＋进度＋结束钮） */
.op-session-list { margin-top: 8px; border-top: 1px solid var(--uhd-l-divider); }
.op-session-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 16px 2px;
  border-bottom: 1px solid var(--uhd-l-divider);
}
.op-session-icon {
  flex: none;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text-2);
}
.op-session-main { flex: 1; min-width: 0; }
.op-session-title { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.op-session-name {
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.op-session-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 3px;
  font-size: 13px;
  color: var(--uhd-l-text-2);
  flex-wrap: wrap;
}
.op-session-meta .addr { display: inline-flex; align-items: center; gap: 3px; }
.op-session-meta .method { color: var(--uhd-l-link); }
.meta-sep { opacity: 0.5; }
.op-session-progress { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.op-session-progress .bar {
  flex: 1;
  max-width: 220px;
  height: 6px;
  border-radius: 999px;
  background: var(--uhd-l-seg-bg);
  overflow: hidden;
}
.op-session-progress .bar-fill {
  height: 100%;
  background: var(--uhd-l-text);
  transform-origin: left center;
  transition: transform 0.3s ease;
}
.op-session-progress .bar-text { font-size: 12px; color: var(--uhd-l-text-2); white-space: nowrap; }
.op-stop-btn {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 40px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid var(--uhd-danger);
  background: var(--uhd-l-surface);
  color: var(--uhd-danger);
  font-family: var(--uhd-font-sans);
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
  cursor: pointer;
}
.op-stop-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.op-stop-btn:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }

/* 空态（说明书 2.7 图标变体：48px 圆 icon＋粗标题＋说明） */
.op-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
  padding: 28px 24px;
}
.op-empty__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text-2);
}
.op-empty__title { margin: 0; font-size: 16px; font-weight: var(--uhd-fw-bold); color: var(--uhd-l-text); }
.op-empty__desc { margin: 0; font-size: 14px; color: var(--uhd-l-text-2); }

/* 错误卡 */
.op-err {
  padding: 20px;
  border: 1px solid var(--uhd-l-border);
  border-radius: 16px;
  background: var(--uhd-l-surface);
  font-size: 14px;
  line-height: 1.7;
}

/* 加载态（说明书 1.6：旋转圈＋灰字） */
.op-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  font-size: 14px;
  color: var(--uhd-l-text-2);
}
.op-rows .op-loading { margin-top: 0; padding: 18px 2px; }
.op-spinner {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-border);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 0.8s linear infinite;
}

/* 按钮（说明书 2.2 胶囊）：primary 彩虹边框＋淡彩底；ghost 白底灰边 */
.op-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 48px;
  padding: 0 26px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-family: var(--uhd-font-sans);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
  cursor: pointer;
}
.op-btn.primary {
  color: var(--uhd-l-text);
  border: var(--uhd-rainbow-border-w) solid transparent;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  background-color: var(--uhd-l-surface);
}
.op-btn.ghost {
  color: var(--uhd-l-text);
  border-color: var(--uhd-l-border);
  background: var(--uhd-l-surface);
}
.op-btn:disabled {
  color: var(--uhd-l-text-3);
  border-color: var(--uhd-l-border);
  background: var(--uhd-l-input-bg);
  opacity: 0.55;
  cursor: not-allowed;
}
.op-btn:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }

/* 弹窗（说明书 2.8 模具，与 UhdModal 同数值：遮罩黑 40%＋模糊 6px、
   白卡 max 400、圆角 24、内边距 32、标题 24、右上 × 关闭） */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 100;
  font-family: var(--uhd-font-sans);
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
}
.modal-card {
  position: relative;
  width: 100%;
  max-width: 400px;
  box-sizing: border-box;
  background: var(--uhd-l-surface);
  border-radius: var(--uhd-radius-modal);
  box-shadow: var(--uhd-shadow-modal);
  padding: 32px;
  max-height: calc(100dvh - 48px);
  overflow-y: auto;
  color: var(--uhd-l-text);
  animation: uhd-blur-in var(--uhd-dur-blur-in) var(--uhd-ease-pop);
}
.modal-title {
  margin: 0 0 8px;
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
}
.modal-desc { font-size: 14px; color: var(--uhd-l-text-2); line-height: 1.7; margin: 0 0 16px; }
.modal-desc strong { color: var(--uhd-danger); }
.modal-label {
  display: block;
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  margin: 0 0 8px;
}
.modal-input {
  width: 100%;
  box-sizing: border-box;
  height: 46px;
  padding: 0 16px;
  font-size: 16px;
  border: 1px solid #e4e4e7;
  border-radius: 23px;
  background: #f4f4f5;
  color: var(--uhd-l-text);
  margin-bottom: 6px;
}
.modal-input:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 1px; }
.modal-error { font-size: 13px; color: var(--uhd-danger); margin: 6px 0 0; }
.modal-actions { display: flex; gap: 10px; margin-top: 20px; }
.modal-actions .op-btn { flex: 1; }
.modal-close {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-l-text-2);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}
.modal-close:hover { background: var(--uhd-l-input-bg); }

.free-note { margin-top: 14px; }
.free-note p { font-size: 14px; color: var(--uhd-l-text-2); line-height: 1.7; margin: 12px 0 0; }

@media (prefers-reduced-motion: reduce) {
  .op-session-progress .bar-fill { transition: none; }
  .modal-mask, .modal-card { animation: none; }
  .op-spinner { animation-duration: 1.6s; }
}
</style>
