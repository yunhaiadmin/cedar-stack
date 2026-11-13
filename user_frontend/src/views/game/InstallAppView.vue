<template>
  <div class="ia-page">
    <header class="ia-head">
      <RouterLink to="/game/account/menu" class="ia-back" aria-label="返回我的">
        <ChevronLeft :size="20" aria-hidden="true" />
      </RouterLink>
      <div class="ia-head__text">
        <h1 class="ia-title">安装应用</h1>
        <p class="ia-sub">在手机、电视和电脑上看片库：先装客户端，再填服务器地址。</p>
      </div>
    </header>

    <main class="ia-body">
      <div v-if="loadError" class="ia-error">
        <p>页面没加载出来，检查一下网络再重试。</p>
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

        <!-- 分步教程：下载 → 添加服务器 → 填地址 → 登录 -->
        <section class="ia-sec" aria-label="使用步骤">
          <h2 class="ia-sec__title">使用步骤</h2>
          <ol class="ia-steps">
            <li class="ia-step">
              <span class="ia-step__no">1</span>
              <p class="ia-step__text">在上面选你的设备，下载对应的客户端，装好后打开；上面没有你的设备时，用下方网页版也能看。</p>
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

        <!-- 网页版：PWA 安装动作保留在本页内（组件由 PWA 单提供） -->
        <section class="ia-sec" aria-label="网页版">
          <h2 class="ia-sec__title">网页版</h2>
          <p class="ia-sec__sub">不想装客户端，浏览器打开本站也能看；还能把网页版添加到桌面，当 app 一样用。</p>
          <div class="ia-rows ia-pwa">
            <UhdInstallRow />
          </div>
        </section>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 「安装应用」（/game/install）——用户中心「帮助」组的整页。
 * 2026-11-13 新建（用户亲点）：友站有整页（服务器地址卡＋线路配置＋
 * 6 平台客户端下载＋分步教程），我方原先只有一行 PWA 入口。
 * 版式按二级页口径：平面分组＋发丝线，跟随用户外观（浅/深两套本地变量）。
 * 下载链接与服务器地址全部走后端下发（链接是系统配置项），本页不写死；
 * 未配链接的平台后端不下发、本页也不渲染该行。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, ChevronLeft, Copy, Download } from 'lucide-vue-next'
import UhdInstallRow from '@/components/uhd/UhdInstallRow.vue'
import { getInstallApp, type InstallAppPayload } from '@/api/installApp'
import { writeToClipboard } from '@/composables/useClipboard'

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

onMounted(load)
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

.ia-sec { margin-top: 28px; }
.ia-sec__title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
}
.ia-sec__sub {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.62);
}
html[data-theme='light'] .ia-sec__sub { color: rgba(17, 18, 20, 0.62); }

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
/* 步骤 */
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
