<template>
  <UhdAccountLayout title="账号与设置" eyebrow="账号">
  <div class="v4-scope">
  <div class="settings-page">
    <!-- 安全 -->
    <section class="set-group">
      <h2 class="set-gtitle">安全</h2>
      <p class="set-gsub">发现不是自己登的，先改密码再清设备</p>
      <div class="set-rows">
        <PcEntryRow :icon="Lock" name="修改登录密码" @click="pwdOpen = true" />
        <DevicesEntry />
        <LoginHistoryEntry />
        <SecurityTrioPanel />
      </div>
    </section>

    <!-- 绑定：providers 门控结论由面板回报，未确认前整组不显示（v-show 保挂载，面板要先加载才能回报） -->
    <section v-show="driveGroupVisible" id="my-drive" class="set-group">
      <h2 class="set-gtitle">绑定</h2>
      <p class="set-gsub">绑了自己的盘，播放走你自己的额度，不走共享池</p>
      <div class="set-rows">
        <DriveBindingsPanel ref="drivePanel" @loaded="onDriveLoaded" @active-binding="onActiveBinding" />
      </div>
    </section>

    <!-- 追剧订阅：管理面板原家在此；概览「我的片子」组摘除后归位回来。
         门控同绑定组：面板常驻挂载先加载，summary 回报不可见（开关关 404）时整组不显示 -->
    <section v-show="followGroupVisible" class="set-group">
      <h2 class="set-gtitle">追剧订阅</h2>
      <p class="set-gsub">订阅的剧有新集到货，自动转存到你绑定的盘里</p>
      <div class="set-rows">
        <SeriesFollowsPanel @summary="onFollowSummary" />
      </div>
    </section>

    <!-- 通知：TG 私聊偏好（站内信不受影响） -->
    <section class="set-group">
      <h2 class="set-gtitle">通知</h2>
      <p class="set-gsub">只管机器人私聊这一份，站内消息照常发</p>
      <div class="set-rows">
        <div class="set-row">
          <span class="set-row-ic" aria-hidden="true"><Bell :size="22" /></span>
          <span class="set-row-name">求片到货提醒</span>
          <button
            type="button"
            class="set-switch"
            :class="{ 'is-on': notifyPrefs.seek }"
            :disabled="notifyLoading"
            role="switch"
            :aria-checked="notifyPrefs.seek"
            aria-label="求片到货提醒"
            @click="toggleNotifyPref('seek')"
          ></button>
        </div>
        <div class="set-row">
          <span class="set-row-ic" aria-hidden="true"><Bell :size="22" /></span>
          <span class="set-row-name">到期提醒</span>
          <button
            type="button"
            class="set-switch"
            :class="{ 'is-on': notifyPrefs.expiry }"
            :disabled="notifyLoading"
            role="switch"
            :aria-checked="notifyPrefs.expiry"
            aria-label="到期提醒"
            @click="toggleNotifyPref('expiry')"
          ></button>
        </div>
        <div class="set-row">
          <span class="set-row-ic" aria-hidden="true"><Bell :size="22" /></span>
          <span class="set-row-name">活动中奖通知</span>
          <button
            type="button"
            class="set-switch"
            :class="{ 'is-on': notifyPrefs.lottery }"
            :disabled="notifyLoading"
            role="switch"
            :aria-checked="notifyPrefs.lottery"
            aria-label="活动中奖通知"
            @click="toggleNotifyPref('lottery')"
          ></button>
        </div>
        <div class="set-row">
          <span class="set-row-ic" aria-hidden="true"><Bell :size="22" /></span>
          <span class="set-row-name">贡献回响</span>
          <button
            type="button"
            class="set-switch"
            :class="{ 'is-on': notifyPrefs.pool_digest }"
            :disabled="notifyLoading"
            role="switch"
            :aria-checked="notifyPrefs.pool_digest"
            aria-label="贡献回响"
            @click="toggleNotifyPref('pool_digest')"
          ></button>
        </div>
      </div>
      <p class="set-note">求片到货、会员/公益资格到期、抽奖中奖、公共池每日小结这四类机器人私聊，可以分别关掉；关掉后站内消息仍会发。</p>
      <p v-if="notifyError" class="set-errline">
        <span>{{ notifyError }}</span>
        <button type="button" class="set-retry" @click="loadNotifyPrefs">重试</button>
      </p>
    </section>

    <!-- 账号 -->
    <section class="set-group">
      <h2 class="set-gtitle">账号</h2>
      <div class="set-rows">
        <PcEntryRow :icon="LogOut" name="退出登录" danger @click="handleLogout" />
      </div>
    </section>

    <ChangePasswordModal v-model:open="pwdOpen" />
  </div>
  </div>
  </UhdAccountLayout>
</template>

<script setup lang="ts">
/**
 * 账号设置页（/game/account/settings）：个人中心重做后设置类功能的家。
 * 五组——安全（改密码 / 我的设备 / 登录记录）、绑定（谷歌盘管理 + 已
 * 下线绑定解绑）、追剧订阅（订阅列表 / 搜片名订阅 / 分集转存状态，
 * 面板原家；概览归组摘除后归位）、通
 * 知（TG 私聊三类偏好开关）、账号（退出登录）。各块逻辑都在 components/profile/ 的对应组件里（自旧
 * 账号页原样抽出，行为口径不变）；本页只留退出登录。原「展示与隐私」组
 * （发现页卡片展示 / 观影碑上榜开关）已随发现页删除与榜单下架定版整组
 * 摘除（2026-11-13 残留入口清理单）。
 * 带 #my-drive 锚点进来（旧深链 / 播放器方案弹窗）时，等绑定组显示后
 * 自动展开谷歌盘并滚到位。点播引导带 bind_return 参数进来时，绑好
 * （列表出现可用绑定）自动回跳来源页并让其重开播放器选择。
 */
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, Lock, LogOut } from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import PcEntryRow from '@/components/profile/PcEntryRow.vue'
import ChangePasswordModal from '@/components/profile/ChangePasswordModal.vue'
import DevicesEntry from '@/components/profile/DevicesEntry.vue'
import LoginHistoryEntry from '@/components/profile/LoginHistoryEntry.vue'
import SecurityTrioPanel from '@/components/profile/SecurityTrioPanel.vue'
import DriveBindingsPanel from '@/components/profile/DriveBindingsPanel.vue'
import SeriesFollowsPanel from '@/components/profile/SeriesFollowsPanel.vue'
import { panelApi, humanizeError } from '@/composables/usePanelApi'
import {
  BIND_RETURN_QUERY,
  BIND_TITLE_QUERY,
  RESUME_TITLE_QUERY,
  sanitizeBindReturnPath,
} from '@/composables/useBindReturn'
import { useUserStore } from '@/stores/user'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const toast = useToast()

const pwdOpen = ref(false)

// ── 绑定组门控（providers 结论由 DriveBindingsPanel 回报） ──
const drivePanel = ref<InstanceType<typeof DriveBindingsPanel> | null>(null)
const driveGroupVisible = ref(false)
function onDriveLoaded(payload: { visible: boolean }) {
  driveGroupVisible.value = payload.visible
  if (payload.visible && route.hash === '#my-drive') {
    void nextTick(() => {
      drivePanel.value?.expandDrive()
      document.getElementById('my-drive')?.scrollIntoView({ block: 'start' })
    })
  }
}

// ── 追剧订阅组门控（面板 summary 回报：接口 404＝未启用，整组隐藏） ──
const followGroupVisible = ref(false)
function onFollowSummary(payload: { visible: boolean }) {
  followGroupVisible.value = payload.visible
}

// ── 绑盘回跳：点播被门禁拦下经引导进来（query 带 bind_return）时，
// 名下出现可用绑定就自动带回来源页，并让来源页重开播放器选择 ──
const bindReturnDone = ref(false)
function onActiveBinding() {
  if (bindReturnDone.value) return
  const target = sanitizeBindReturnPath(route.query[BIND_RETURN_QUERY])
  if (!target) return
  bindReturnDone.value = true
  const rawTitle = route.query[BIND_TITLE_QUERY]
  const title = typeof rawTitle === 'string' ? rawTitle.trim() : ''
  toast.success('绑好了，带你回到刚才的片')
  void router.replace({
    path: target,
    query: title ? { [RESUME_TITLE_QUERY]: title } : {},
  })
}

// ── TG 私聊通知偏好（只管机器人私聊，站内信不受影响） ──
type NotifyPrefKey = 'seek' | 'expiry' | 'lottery' | 'pool_digest'
const notifyPrefs = ref<Record<NotifyPrefKey, boolean>>({
  seek: true, expiry: true, lottery: true, pool_digest: true,
})
const notifyLoading = ref(false)
const notifyError = ref('')

async function loadNotifyPrefs() {
  notifyLoading.value = true
  notifyError.value = ''
  try {
    const d = await panelApi.notifyPrefsGet()
    notifyPrefs.value = {
      seek: d.seek !== false,
      expiry: d.expiry !== false,
      lottery: d.lottery !== false,
      pool_digest: d.pool_digest !== false,
    }
  } catch (e) {
    notifyError.value = humanizeError(e)
  } finally {
    notifyLoading.value = false
  }
}
async function toggleNotifyPref(key: NotifyPrefKey) {
  if (notifyLoading.value) return
  const next = !notifyPrefs.value[key]
  notifyLoading.value = true
  try {
    const d = await panelApi.notifyPrefsSet({ [key]: next })
    notifyPrefs.value = {
      seek: d.seek !== false,
      expiry: d.expiry !== false,
      lottery: d.lottery !== false,
      pool_digest: d.pool_digest !== false,
    }
    toast.success(next ? '已打开这类私聊通知' : '已关掉这类私聊通知')
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    notifyLoading.value = false
  }
}

/** 退出登录：与顶栏头像菜单同款动作——清登录态后回登录页，不另写一套 */
async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}

onMounted(() => {
  void loadNotifyPrefs()
})
</script>

<style scoped>
/* 状态行模具（说明书 2.6 / §3 行 8 友站 /user/security）：
   分组 H2 24px/800 领起 + 平面状态行（图标 22 + 标题 16/600 + 右侧状态/开关），
   行间 1px --uhd-l-divider，不再套卡片容器。单列堆叠（与友站同构），
   分组与行序与旧版一致（旧桌面两栏在单列下即此顺序）。 */
.settings-page {
  max-width: var(--uhd-container-app);
  margin: 0 auto;
  padding-bottom: 34px;
  font-family: var(--uhd-font-sans);
}
.set-group { margin-top: 36px; }
.set-group:first-child { margin-top: 4px; }
.set-gtitle {
  margin: 0 0 6px;
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  color: var(--uhd-l-text);
}
.set-gsub {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.55;
  color: var(--uhd-l-text-2);
}
.set-rows > :not(:last-child) { border-bottom: var(--uhd-border-w) solid var(--uhd-l-divider); }
.set-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 68px;
  padding: 10px 2px;
}
.set-row-ic {
  width: 22px;
  height: 22px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-l-text);
}
.set-row-name {
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  white-space: nowrap;
}
/* 开关模具：44×24 黑体轨道 + 20px 白钮（友站开关形态），
   按钮本体 52×44 命中区（类内写死高度，压过 mobile.css 的全局 button 规则） */
.set-switch {
  position: relative;
  flex: none;
  width: 52px;
  height: 44px;
  min-height: 44px;
  padding: 0;
  margin-left: auto;
  border: 0;
  background: none;
  cursor: pointer;
}
.set-switch::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 10px;
  width: 44px;
  height: 24px;
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-border);
  transition: background var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.set-switch::after {
  content: '';
  position: absolute;
  top: 12px;
  left: 6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--uhd-l-surface);
  box-shadow: var(--uhd-shadow-seg);
  transition: left var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.set-switch.is-on::before { background: var(--uhd-l-text); }
.set-switch.is-on::after { left: 26px; }
.set-switch:disabled { opacity: 0.5; cursor: not-allowed; }
.set-note {
  margin: 0;
  padding: 12px 2px 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.set-errline {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 12px 2px 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: var(--uhd-danger);
}
.set-retry {
  flex: none;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  border-radius: var(--uhd-radius-pill);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
</style>
