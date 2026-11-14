<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳
       （壳内隐藏返回钮、页根内边距由布局承接）；手机原样 -->
  <UhdAccountShellGate>
  <div class="ia-page">
    <header class="ia-head">
      <RouterLink to="/game/account/menu" class="ia-back" aria-label="返回我的">
        <ChevronLeft :size="20" aria-hidden="true" />
      </RouterLink>
      <div class="ia-head__text">
        <h1 class="ia-title">安装应用</h1>
        <p class="ia-sub">把本站装到桌面，像 app 一样打开；也可以往下看，用客户端连服务器看片库。</p>
      </div>
    </header>

    <main class="ia-body">
      <!-- 身份行：应用图标＋应用名＋当前设备/浏览器（UA 真识别，识别不了回通用口径） -->
      <section class="ia-identity" aria-label="当前设备">
        <img class="ia-app-icon" :src="appIcon" :alt="`${appName} 图标`" width="76" height="76" />
        <div class="ia-identity__text">
          <p class="ia-app-name">{{ appName }}</p>
          <p class="ia-current">当前：{{ device.deviceLabel }} · {{ device.browserLabel }}</p>
        </div>
      </section>

      <!-- 在这台设备上安装：按浏览器分段切换步骤 -->
      <section class="ia-sec" aria-label="在这台设备上安装">
        <h2 class="ia-sec__title ia-sec__title--lg">在这台设备上安装</h2>
        <p class="ia-sec__sub">{{ installIntro }}</p>
        <p v-if="standalone" class="ia-sec__sub">你现在就是从装好的应用里打开本站的，不用再装一遍。</p>
        <div class="ia-seg" role="tablist" aria-label="选择浏览器">
          <button
            v-for="b in browserTabs"
            :key="b"
            type="button"
            role="tab"
            class="ia-seg__btn"
            :class="{ 'is-on': activeBrowser === b }"
            :aria-selected="activeBrowser === b"
            @click="activeBrowser = b"
          >
            {{ BROWSER_LABELS[b] }}
          </button>
        </div>
        <ol class="ia-psteps">
          <li v-for="(step, i) in currentSteps" :key="i" class="ia-pstep">
            <span class="ia-pstep__chip">
              <component :is="stepIcon(step.icon)" :size="19" aria-hidden="true" />
            </span>
            <p class="ia-pstep__text">
              <template v-for="(part, j) in step.parts" :key="j">
                <strong v-if="part.strong">{{ part.text }}</strong>
                <template v-else>{{ part.text }}</template>
              </template>
            </p>
          </li>
        </ol>
        <!-- 一键安装动作（组件由 PWA 单提供：支持的浏览器直接调起安装提示，
             iOS 走说明弹层；已在应用内打开时自动隐藏） -->
        <div class="ia-rows ia-pwa">
          <UhdInstallRow />
        </div>
      </section>

      <!-- 其他设备：逐台折叠，展开是该平台的安装步骤 -->
      <section class="ia-sec" aria-label="其他设备">
        <h2 class="ia-sec__title ia-sec__title--lg">其他设备</h2>
        <div class="ia-folds">
          <div v-for="group in otherGroups" :key="group.key" class="ia-fold">
            <button
              type="button"
              class="ia-fold__head"
              :aria-expanded="openGroup === group.key"
              @click="toggleGroup(group.key)"
            >
              <span>{{ group.name }}</span>
              <ChevronDown
                :size="18"
                class="ia-fold__chev"
                :class="{ 'is-open': openGroup === group.key }"
                aria-hidden="true"
              />
            </button>
            <div v-if="openGroup === group.key" class="ia-fold__body">
              <div v-for="b in group.browsers" :key="b" class="ia-fold__browser">
                <p class="ia-fold__browser-name">{{ BROWSER_LABELS[b] }}</p>
                <ol class="ia-psteps ia-psteps--compact">
                  <li v-for="(step, i) in stepsFor(group.key, b)" :key="i" class="ia-pstep">
                    <span class="ia-pstep__chip">
                      <component :is="stepIcon(step.icon)" :size="19" aria-hidden="true" />
                    </span>
                    <p class="ia-pstep__text">
                      <template v-for="(part, j) in step.parts" :key="j">
                        <strong v-if="part.strong">{{ part.text }}</strong>
                        <template v-else>{{ part.text }}</template>
                      </template>
                    </p>
                  </li>
                </ol>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 卸载与重新安装：按平台逐组给确切步骤 -->
      <section class="ia-sec" aria-label="卸载与重新安装">
        <h2 class="ia-sec__title ia-sec__title--lg">卸载与重新安装</h2>
        <div class="ia-folds">
          <div v-for="group in UNINSTALL_GROUPS" :key="group.name" class="ia-ugroup">
            <h3 class="ia-ugroup__name">{{ group.name }}</h3>
            <p class="ia-ugroup__body">{{ group.body }}</p>
          </div>
        </div>
        <p class="ia-reinstall">
          <strong>重新安装：</strong>不用先做任何清理。用浏览器打开本站，回到这一页，照上面的步骤重新添加一次就行；你的账号、观看记录和收藏都还在。
        </p>
      </section>

      <!-- 说明：三条都按本站真实行为核对过 -->
      <section class="ia-sec" aria-label="说明">
        <h2 class="ia-sec__title ia-sec__title--lg">说明</h2>
        <div class="ia-folds">
          <p class="ia-note">装到桌面的 {{ appName }} 和网页版用的是同一个账号，观看记录、收藏和播放进度互通；本站发新版本时会自动更新，不需要重新安装。</p>
          <p class="ia-note">在 iPhone、iPad 和 Mac 上用 Safari 添加的应用，登录状态和浏览器里是分开存的，第一次打开需要重新登录一次。</p>
          <p class="ia-note">订阅付款会跳转到支付页面完成，帮助文档等外部链接也会在浏览器里打开，这都是正常现象。</p>
        </div>
      </section>

      <div v-if="loadError" class="ia-error">
        <p>客户端那部分没加载出来，检查一下网络再重试。</p>
        <button type="button" class="ia-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="ia-skeleton" aria-hidden="true"></div>

      <template v-else>
        <!-- 服务器地址卡：对外地址只有一个口径（后端下发），带复制 -->
        <section class="ia-sec" aria-label="服务器地址">
          <h2 class="ia-sec__title">服务器地址</h2>
          <div class="ia-addr-card">
            <span class="ia-addr">{{ payload?.server_address }}</span>
            <button type="button" class="ia-copy" @click="copyAddress">
              <Check v-if="copied" :size="15" aria-hidden="true" />
              <Copy v-else :size="15" aria-hidden="true" />
              {{ copied ? '已复制' : '复制' }}
            </button>
          </div>
          <div class="ia-notes">
            <p>所有设备共用这一个地址，添加服务器时原样粘贴，不用加端口，也不用加后缀。</p>
            <p>播放走站内线路，客户端里不用再填代理之类的其它地址。</p>
            <p>如果客户端里已经存了别的服务器，先删掉、只留这一个，免得连错。</p>
          </div>
        </section>

        <!-- 下载客户端：链接由管理端配置下发，未配的平台整行不显示 -->
        <section v-if="downloadPlatforms.length" class="ia-sec" aria-label="下载客户端">
          <h2 class="ia-sec__title">下载客户端</h2>
          <p class="ia-sec__sub">认准官方客户端，按你的设备选一个下载，装好打开就能添加服务器。</p>
          <div class="ia-rows">
            <div v-for="p in downloadPlatforms" :key="p.key" class="ia-row">
              <span class="ia-row__main">
                <span class="ia-row__name">{{ p.label }}</span>
                <span class="ia-row__sub">{{ p.devices }} · {{ p.store }}</span>
              </span>
              <a
                class="ia-dl"
                :href="p.url"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Download :size="15" aria-hidden="true" />
                下载
              </a>
            </div>
          </div>
        </section>

        <!-- 客户端分步教程：下载 → 添加服务器 → 填地址 → 登录 -->
        <section class="ia-sec" aria-label="客户端使用步骤">
          <h2 class="ia-sec__title">客户端使用步骤</h2>
          <ol class="ia-steps">
            <li class="ia-step">
              <span class="ia-step__no">1</span>
              <p class="ia-step__text">在上面选你的设备，下载对应的客户端，装好后打开；上面没有你的设备时，用上方网页版也能看。</p>
            </li>
            <li class="ia-step">
              <span class="ia-step__no">2</span>
              <p class="ia-step__text">第一次打开客户端会让你添加服务器；没弹出来，就在客户端的设置里找「添加服务器」。</p>
            </li>
            <li class="ia-step">
              <span class="ia-step__no">3</span>
              <p class="ia-step__text">把服务器地址粘贴进去（就是上面那个地址，可以一键复制），名称随便填一个你认得的。</p>
            </li>
            <li class="ia-step">
              <span class="ia-step__no">4</span>
              <p class="ia-step__text">用你在本站的账号和密码登录，片库就出来了，挑一部直接播。</p>
            </li>
          </ol>
        </section>
      </template>
    </main>
  </div>
  </UhdAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 「安装应用」（/game/install）——用户中心「帮助」组的整页。
 * 2026-11-13 新建：服务器地址卡＋平台客户端下载＋分步教程。
 * 2026-11-14 补全（用户发 UHDNow 安装页截图亲点）：前半按友站结构补齐
 * 网页应用（PWA）安装指南——身份行（图标＋应用名＋UA 真识别的当前
 * 设备/浏览器）、按浏览器分段的安装步骤、其他设备折叠、卸载与重新
 * 安装、说明；文案为本站自有口径，步骤按各系统/浏览器的真实操作写。
 * 「说明」三条按本站真实行为核对：登录态存 localStorage（Safari 添加
 * 的应用与浏览器分开存，首开需重登）、订阅付款跳支付页、版本随站点
 * 发布自动更新。后半原有客户端三段（服务器地址/下载/教程）保持不动。
 * 页面跟随用户外观（浅/深两套本地值）；本页不自创 CSS 变量。
 */
import { computed, onMounted, ref, type Component } from 'vue'
import { RouterLink } from 'vue-router'
import {
  Check, ChevronDown, ChevronLeft, Copy, Download, Ellipsis, EllipsisVertical,
  MonitorDown, Plus, Share, SquarePlus,
} from 'lucide-vue-next'
import UhdInstallRow from '@/components/uhd/UhdInstallRow.vue'
import UhdAccountShellGate from '@/components/uhd/UhdAccountShellGate.vue'
import { getInstallApp, type InstallAppPayload } from '@/api/installApp'
import { writeToClipboard } from '@/composables/useClipboard'
import { isStandalone } from '@/composables/useInstallPrompt'
import { branding } from '@/composables/useBranding'
import {
  BROWSER_LABELS, browsersForPlatform, detectDevice,
  type BrowserKind, type DevicePlatform,
} from '@/utils/deviceDetect'

/* ---------- 身份行与当前设备 ---------- */
const appName = computed(() => branding.site_name || 'Aetrix')
const appIcon = computed(() => branding.logo_url || '/icons/icon-192.png')
const device = detectDevice()
const standalone = ref(false)

/* ---------- 安装步骤（按平台×浏览器；关键动作加粗） ---------- */
type StepIcon = 'share' | 'dots' | 'dotsV' | 'plusSquare' | 'plus' | 'install' | 'check'
interface StepPart { text: string; strong?: boolean }
interface InstallStep { icon: StepIcon; parts: StepPart[] }

const STEP_ICONS: Record<StepIcon, Component> = {
  share: Share,
  dots: Ellipsis,
  dotsV: EllipsisVertical,
  plusSquare: SquarePlus,
  plus: Plus,
  install: MonitorDown,
  check: Check,
}
function stepIcon(kind: StepIcon): Component {
  return STEP_ICONS[kind]
}

const t = (text: string): StepPart => ({ text })
const b = (text: string): StepPart => ({ text, strong: true })

/** 步骤表按应用名生成（站名是品牌配置，不写死在文案里）。 */
function makeSteps(name: string): Record<string, Partial<Record<BrowserKind, InstallStep[]>>> {
  const iosSafari: InstallStep[] = [
    { icon: 'share', parts: [t('点底部的「…」，再点'), b('「分享」'), t('；也可以直接点地址栏旁边的分享按钮。')] },
    { icon: 'plusSquare', parts: [t('在分享列表里向下滑动，点'), b('「添加到主屏幕」'), t('；列表里没有时，滑到最下面点「编辑操作」，先把它加进列表。')] },
    { icon: 'plus', parts: [t('确认名称后点右上角的'), b('「添加」'), t(`，回到主屏幕，点 ${name} 图标打开。`)] },
  ]
  const iosChrome: InstallStep[] = [
    { icon: 'share', parts: [t('点地址栏右边的'), b('分享按钮'), t('。')] },
    { icon: 'plusSquare', parts: [t('在弹出的菜单里向下滑动，点'), b('「添加到主屏幕」'), t('。')] },
    { icon: 'plus', parts: [t('确认名称后点'), b('「添加」'), t(`，回到主屏幕，点 ${name} 图标打开。`)] },
  ]
  const iosEdge: InstallStep[] = [
    { icon: 'dots', parts: [t('点底部的'), b('「…」'), t('打开菜单。')] },
    { icon: 'plusSquare', parts: [t('在菜单里点'), b('「添加到主屏幕」'), t('。')] },
    { icon: 'plus', parts: [t('确认后点'), b('「添加」'), t(`，回到主屏幕，点 ${name} 图标打开。`)] },
  ]
  const androidChrome: InstallStep[] = [
    { icon: 'dotsV', parts: [t('点地址栏右侧的'), b('「⋮」'), t('打开菜单。')] },
    { icon: 'install', parts: [t('点'), b('「安装应用」'), t('（有的版本写「添加到主屏幕」），在弹出的确认框里点'), b('「安装」'), t('。')] },
    { icon: 'check', parts: [t(`装好后回到桌面，点 ${name} 图标打开。`)] },
  ]
  const androidEdge: InstallStep[] = [
    { icon: 'dots', parts: [t('点底部的'), b('「…」'), t('打开菜单。')] },
    { icon: 'plusSquare', parts: [t('点'), b('「添加到手机」'), t('，再点「添加」确认。')] },
    { icon: 'check', parts: [t(`回到桌面，点 ${name} 图标打开。`)] },
  ]
  const desktopChrome = (tail: string): InstallStep[] => [
    { icon: 'install', parts: [t('点地址栏右侧的'), b('安装图标'), t('；没有图标时，点右上角「⋮」打开菜单。')] },
    { icon: 'plusSquare', parts: [t('点图标的，直接在弹窗里点'), b('「安装」'), t(`；走菜单的，选「保存并分享」里的「安装 ${name}」。`)] },
    { icon: 'check', parts: [t(`安装后，${tail}点 ${name} 打开。`)] },
  ]
  const desktopEdge = (tail: string): InstallStep[] => [
    { icon: 'dots', parts: [t('点右上角'), b('「…」'), t('打开菜单；地址栏右侧有应用图标时，也可以直接点它。')] },
    { icon: 'plusSquare', parts: [t('选「应用」里的'), b('「将此站点安装为应用」'), t('，在弹窗里点「安装」。')] },
    { icon: 'check', parts: [t(`安装后，${tail}点 ${name} 打开。`)] },
  ]
  const macSafari: InstallStep[] = [
    { icon: 'share', parts: [t('点工具栏里的'), b('分享按钮'), t('（或菜单栏的「文件」）。')] },
    { icon: 'plusSquare', parts: [t('点'), b('「添加到 Dock」'), t('，确认名称后点「添加」。')] },
    { icon: 'check', parts: [t(`之后从程序坞或启动台点 ${name} 打开。`)] },
  ]
  return {
    ios: { safari: iosSafari, chrome: iosChrome, edge: iosEdge },
    android: { chrome: androidChrome, edge: androidEdge },
    windows: { chrome: desktopChrome('从开始菜单或桌面'), edge: desktopEdge('从开始菜单或桌面') },
    mac: { safari: macSafari, chrome: desktopChrome('从启动台或程序坞'), edge: desktopEdge('从启动台或程序坞') },
  }
}

const stepsTable = computed(() => makeSteps(appName.value))

/** 取某平台×浏览器的步骤；缺表时回退到最接近的一套（桌面 Chrome 口径）。 */
function stepsFor(platform: DevicePlatform, browser: BrowserKind): InstallStep[] {
  const table = stepsTable.value
  return (
    table[platform]?.[browser] ??
    table.windows?.chrome ??
    []
  )
}

/* ---------- 本机安装：分段与当前步骤 ---------- */
const browserTabs = computed(() => browsersForPlatform(device.platform))
const activeBrowser = ref<BrowserKind>(
  browserTabs.value.includes(device.browser) ? device.browser : browserTabs.value[0],
)
const currentSteps = computed(() => stepsFor(device.platform, activeBrowser.value))
const installIntro = computed(() => {
  switch (device.platform) {
    case 'ios':
      return '照你正在用的浏览器看下面的步骤就行。iPhone / iPad 需要 iOS 16.4 或更高版本。'
    case 'android':
      return '照你正在用的浏览器看下面的步骤就行；支持的浏览器也可以用下面的一键安装直接装。'
    case 'windows':
    case 'mac':
      return '照你正在用的浏览器看下面的步骤就行，装好后它会像普通软件一样出现在应用列表里。'
    default:
      return '照你正在用的浏览器看下面的步骤就行。'
  }
})

/* ---------- 其他设备折叠 ---------- */
interface DeviceGroup { key: DevicePlatform; name: string; browsers: BrowserKind[] }
const DEVICE_GROUPS: DeviceGroup[] = [
  { key: 'ios', name: 'iPhone / iPad', browsers: ['safari', 'chrome', 'edge'] },
  { key: 'android', name: 'Android 手机', browsers: ['chrome', 'edge'] },
  { key: 'windows', name: 'Windows 电脑', browsers: ['chrome', 'edge'] },
  { key: 'mac', name: 'Mac', browsers: ['safari', 'chrome', 'edge'] },
]
const otherGroups = computed(() => DEVICE_GROUPS.filter((g) => g.key !== device.platform))
const openGroup = ref<DevicePlatform | null>(null)
function toggleGroup(key: DevicePlatform) {
  openGroup.value = openGroup.value === key ? null : key
}

/* ---------- 卸载分组（应用名随品牌配置） ---------- */
const UNINSTALL_GROUPS = computed(() => {
  const name = appName.value
  return [
    {
      name: 'iPhone / iPad',
      body: `在主屏幕长按 ${name} 图标，点「删除书签」（部分系统版本显示为「删除 App」），再点一次确认删除。`,
    },
    {
      name: 'Android · Chrome / Edge',
      body: `在桌面长按 ${name} 图标，点「卸载」；也可以打开系统「设置 → 应用」，找到 ${name} 后卸载。`,
    },
    {
      name: 'Windows · Chrome / Edge',
      body: `先打开 ${name} 的应用窗口，点右上角「…」菜单里的「卸载 ${name}」；也可以在系统「设置 → 应用 → 已安装的应用」里卸载。`,
    },
    {
      name: 'Mac · Safari',
      body: `打开访达，进入个人文件夹下的「应用程序」文件夹，把 ${name} 拖进废纸篓。`,
    },
    {
      name: 'Mac · Chrome / Edge',
      body: `先打开 ${name} 的应用窗口，点右上角「…」菜单里的「卸载 ${name}」；也可以在浏览器的应用管理页里移除。`,
    },
  ]
})

/* ---------- 客户端三段（原有，后端下发） ---------- */
const payload = ref<InstallAppPayload | null>(null)
// 双保险：后端只下发已配平台，前端再滤一遍空链接，未配整行不显示
const downloadPlatforms = computed(() =>
  (payload.value?.platforms ?? []).filter((p) => p.url),
)
const loading = ref(true)
const loadError = ref(false)
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | null = null

async function load() {
  loading.value = true
  loadError.value = false
  try {
    payload.value = await getInstallApp()
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function copyAddress() {
  const addr = payload.value?.server_address
  if (!addr) return
  const res = await writeToClipboard(addr)
  if (res.ok) {
    copied.value = true
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copied.value = false }, 2000)
  }
}

onMounted(() => {
  standalone.value = isStandalone()
  load()
})
</script>

<style scoped>
/* 跟随用户外观：缺省深色，html[data-theme='light'] 时逐条换浅
   （与「我的」页同一口径；令牌契约只认全局样式表定义的变量，
   本页不自创 CSS 变量，一律直写值＋浅色覆盖）。 */
.ia-page {
  min-height: 100dvh;
  background: #050505;
  color: #ffffff;
  font-family: var(--uhd-font-sans);
  padding: 20px 16px 48px;
  box-sizing: border-box;
}
html[data-theme='light'] .ia-page {
  background: #f6f6f4;
  color: #111214;
}

.ia-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 760px;
  margin: 0 auto 8px;
}
.ia-back {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  margin-top: 2px;
  border-radius: 50%;
  color: #ffffff;
  background: #101013;
  border: 1px solid rgba(255, 255, 255, 0.09);
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
}
html[data-theme='light'] .ia-back {
  color: #111214;
  background: #ffffff;
  border-color: rgba(17, 18, 20, 0.09);
}
.ia-head__text { min-width: 0; }
.ia-title {
  margin: 0;
  font-size: 24px;
  font-weight: 800;
  letter-spacing: 0.01em;
}
.ia-sub {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-sub { color: rgba(17, 18, 20, 0.62); }

.ia-body {
  max-width: 760px;
  margin: 0 auto;
}

/* 身份行 */
.ia-identity {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 20px;
}
.ia-app-icon {
  flex: 0 0 auto;
  width: 76px;
  height: 76px;
  border-radius: 20px;
  object-fit: cover;
}
.ia-app-name {
  margin: 0;
  font-size: 24px;
  font-weight: 800;
  letter-spacing: 0.01em;
}
.ia-current {
  margin: 6px 0 0;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-current { color: rgba(17, 18, 20, 0.62); }

.ia-sec { margin-top: 28px; }
.ia-sec__title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
}
.ia-sec__title--lg {
  font-size: 20px;
  font-weight: 800;
  margin-bottom: 8px;
}
.ia-sec__sub {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-sec__sub { color: rgba(17, 18, 20, 0.62); }

/* 浏览器分段切换 */
.ia-seg {
  display: flex;
  gap: 4px;
  margin-top: 12px;
  padding: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
}
html[data-theme='light'] .ia-seg { background: rgba(17, 18, 20, 0.07); }
.ia-seg__btn {
  flex: 1 1 0;
  min-height: 36px;
  padding: 0 12px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
html[data-theme='light'] .ia-seg__btn { color: rgba(17, 18, 20, 0.55); }
.ia-seg__btn.is-on {
  background: #2b2b30;
  color: #ffffff;
}
html[data-theme='light'] .ia-seg__btn.is-on {
  background: #ffffff;
  color: #111214;
  box-shadow: 0 1px 4px rgba(17, 18, 20, 0.12);
}

/* 编号步骤：圆形图标钮＋文案（关键动作加粗），发丝线分隔 */
.ia-psteps {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
}
.ia-pstep {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 13px 0;
}
.ia-pstep + .ia-pstep { border-top: 1px solid rgba(255, 255, 255, 0.09); }
html[data-theme='light'] .ia-pstep + .ia-pstep { border-top-color: rgba(17, 18, 20, 0.09); }
.ia-pstep__chip {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #17171b;
  border: 1px solid rgba(255, 255, 255, 0.09);
  color: #ffffff;
}
html[data-theme='light'] .ia-pstep__chip {
  background: #ffffff;
  border-color: rgba(17, 18, 20, 0.09);
  color: #111214;
}
.ia-pstep__text {
  margin: 9px 0 0;
  font-size: 15px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.82);
}
html[data-theme='light'] .ia-pstep__text { color: rgba(17, 18, 20, 0.85); }
.ia-pstep__text strong {
  color: #ffffff;
  font-weight: 700;
}
html[data-theme='light'] .ia-pstep__text strong { color: #111214; }
.ia-psteps--compact .ia-pstep { padding: 10px 0; }
.ia-psteps--compact .ia-pstep__text { font-size: 14px; }

/* 折叠组（其他设备）与说明/卸载：平面行组，发丝线分隔 */
.ia-folds { margin-top: 4px; }
.ia-fold + .ia-fold,
.ia-ugroup + .ia-ugroup,
.ia-note + .ia-note {
  border-top: 1px solid rgba(255, 255, 255, 0.09);
}
html[data-theme='light'] .ia-fold + .ia-fold,
html[data-theme='light'] .ia-ugroup + .ia-ugroup,
html[data-theme='light'] .ia-note + .ia-note {
  border-top-color: rgba(17, 18, 20, 0.09);
}
.ia-fold__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 16px 2px;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 16px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.ia-fold__chev {
  flex: 0 0 auto;
  color: rgba(255, 255, 255, 0.45);
  transition: transform 0.25s ease;
}
html[data-theme='light'] .ia-fold__chev { color: rgba(17, 18, 20, 0.45); }
.ia-fold__chev.is-open { transform: rotate(180deg); }
.ia-fold__body { padding: 2px 2px 14px; }
.ia-fold__browser + .ia-fold__browser { margin-top: 14px; }
.ia-fold__browser-name {
  margin: 0 0 2px;
  font-size: 13px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-fold__browser-name { color: rgba(17, 18, 20, 0.62); }

.ia-ugroup { padding: 14px 2px; }
.ia-ugroup__name {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
}
.ia-ugroup__body {
  margin: 0;
  font-size: 14px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-ugroup__body { color: rgba(17, 18, 20, 0.62); }
.ia-reinstall {
  margin: 14px 0 0;
  font-size: 14px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-reinstall { color: rgba(17, 18, 20, 0.62); }
.ia-reinstall strong { color: #ffffff; font-weight: 700; }
html[data-theme='light'] .ia-reinstall strong { color: #111214; }

.ia-note {
  margin: 0;
  padding: 14px 2px;
  font-size: 14px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-note { color: rgba(17, 18, 20, 0.62); }

/* 服务器地址卡 */
.ia-addr-card {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  padding: 16px;
  background: #101013;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 16px;
}
html[data-theme='light'] .ia-addr-card {
  background: #ffffff;
  border-color: rgba(17, 18, 20, 0.09);
}
.ia-addr {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.01em;
  overflow-wrap: anywhere;
}
.ia-copy {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1.5px solid transparent;
  background:
    var(--uhd-glass-btn-d) padding-box,
    var(--uhd-rainbow) border-box;
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
html[data-theme='light'] .ia-copy {
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #171717;
}
.ia-notes {
  margin-top: 10px;
  font-size: 13px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-notes { color: rgba(17, 18, 20, 0.62); }
.ia-notes p { margin: 2px 0; }

/* 平面行组：发丝线分隔，无卡片套卡片 */
.ia-rows {
  margin-top: 8px;
  background: #101013;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 16px;
  overflow: hidden;
}
html[data-theme='light'] .ia-rows {
  background: #ffffff;
  border-color: rgba(17, 18, 20, 0.09);
}
.ia-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
}
.ia-row + .ia-row { border-top: 1px solid rgba(255, 255, 255, 0.09); }
html[data-theme='light'] .ia-row + .ia-row { border-top-color: rgba(17, 18, 20, 0.09); }
.ia-row__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ia-row__name { font-size: 15px; font-weight: 600; }
.ia-row__sub { font-size: 12px; color: rgba(255, 255, 255, 0.38); }
html[data-theme='light'] .ia-row__sub { color: rgba(17, 18, 20, 0.42); }
.ia-dl {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1.5px solid transparent;
  background:
    var(--uhd-glass-btn-d) padding-box,
    var(--uhd-rainbow) border-box;
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
}
html[data-theme='light'] .ia-dl {
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #171717;
}

/* 客户端步骤 */
.ia-steps {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
}
.ia-step {
  display: flex;
  gap: 12px;
  padding: 10px 0;
}
.ia-step + .ia-step { border-top: 1px solid rgba(255, 255, 255, 0.09); }
html[data-theme='light'] .ia-step + .ia-step { border-top-color: rgba(17, 18, 20, 0.09); }
.ia-step__no {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #101013;
  border: 1px solid rgba(255, 255, 255, 0.09);
  font-size: 13px;
  font-weight: 700;
}
html[data-theme='light'] .ia-step__no {
  background: #ffffff;
  border-color: rgba(17, 18, 20, 0.09);
}
.ia-step__text {
  margin: 2px 0 0;
  font-size: 14px;
  line-height: 1.6;
}

/* PWA 行（UhdInstallRow 自带深色行样式）：在本页里跟随页面主题 */
.ia-pwa { margin-top: 14px; }
.ia-pwa :deep(.uir-row) { color: #ffffff; }
html[data-theme='light'] .ia-pwa :deep(.uir-row) { color: #111214; }
.ia-pwa :deep(.uir-row__icon) { color: rgba(255, 255, 255, 0.62); }
html[data-theme='light'] .ia-pwa :deep(.uir-row__icon) { color: rgba(17, 18, 20, 0.62); }
.ia-pwa :deep(.uir-row__value),
.ia-pwa :deep(.uir-row__chev) { color: rgba(255, 255, 255, 0.38); }
html[data-theme='light'] .ia-pwa :deep(.uir-row__value),
html[data-theme='light'] .ia-pwa :deep(.uir-row__chev) { color: rgba(17, 18, 20, 0.42); }
.ia-pwa :deep(.uir-row:active) { background: transparent; }

.ia-error {
  margin-top: 32px;
  text-align: center;
  color: rgba(255, 255, 255, 0.62);
  font-size: 14px;
}
html[data-theme='light'] .ia-error { color: rgba(17, 18, 20, 0.62); }
.ia-retry {
  margin-top: 10px;
  min-height: 36px;
  padding: 0 20px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  background: #101013;
  color: #ffffff;
  font-size: 13px;
  cursor: pointer;
}
html[data-theme='light'] .ia-retry {
  border-color: rgba(17, 18, 20, 0.09);
  background: #ffffff;
  color: #111214;
}
.ia-skeleton {
  margin-top: 28px;
  height: 320px;
  border-radius: 16px;
  background: #101013;
  border: 1px solid rgba(255, 255, 255, 0.09);
  opacity: 0.6;
}
html[data-theme='light'] .ia-skeleton {
  background: #ffffff;
  border-color: rgba(17, 18, 20, 0.09);
}
</style>
