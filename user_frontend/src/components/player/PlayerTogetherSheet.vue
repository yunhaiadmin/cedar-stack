<template>
  <PlayerSheet :open="open" :title="inRoom ? '' : '邀请朋友一起看'" @update:open="emit('update:open', $event)">
    <!-- 未进房 -->
    <template v-if="!inRoom">
      <template v-if="joinCode">
        <p class="wt-note">
          你打开了一个一起看邀请。加入后，你的播放会和房主同步；视频还是走你自己账号的线路播放，不经过别人。
        </p>
        <button
          type="button"
          class="wt-primary"
          :disabled="busy"
          @click="emit('join')"
        >
          {{ busy ? '正在加入…' : '加入一起看' }}
        </button>
      </template>
      <template v-else>
        <!-- 副行与主钮照友站邀请卡（G-188/G-190）；「房主控制播放」
             已定版为房主独控（G-189）：服务端只采纳房主的控制信号 -->
        <p class="wt-sub">最多 10 人，房主控制播放</p>
        <button
          type="button"
          class="wt-primary wt-create"
          :class="{ 'wt-primary--busy': busy }"
          :disabled="busy"
          @click="emit('create')"
        >
          {{ busy ? '正在开启...' : '开启同看' }}
        </button>
      </template>
      <p v-if="error" class="wt-error">{{ error }}</p>
      <!-- 同步设置入口（G-192）：开启前一处，展开形态照友站 -->
      <WtSyncSettings
        :params="syncParams"
        @apply="emit('apply-sync-params', $event)"
      />
    </template>

    <!-- 已进房：独立叠层卡（G-200，友站形态为叠在邀请面板之上的新一层） -->
    <template v-else>
      <div class="wt-room-card">
        <!-- 复制钮（G-202）：全宽深灰胶囊，点后变对勾＋「已复制链接」；
             链接明文与房间编码行不再展示（G-209，友站面板内无此两行） -->
        <button type="button" class="wt-pill-btn" @click="copyLink">
          <Check v-if="copied" :size="16" aria-hidden="true" />
          <Copy v-else :size="16" aria-hidden="true" />
          {{ copied ? '已复制链接' : '复制邀请链接' }}
        </button>

        <p class="wp-sec">成员</p>
        <div class="wp-list">
          <div v-for="m in members" :key="m.user_id" class="wp-row wp-row-static">
            <span class="wp-row-main">
              <Crown v-if="m.is_host" :size="16" class="wt-crown" aria-label="房主" />
              <span>{{ m.username || '用户' }}{{ m.user_id === selfId ? '（我）' : '' }}</span>
            </span>
          </div>
        </div>

        <!-- 房主独控（G-189）：成员视角明示控制权归房主 -->
        <p v-if="!isHost" class="wt-note wt-guest-note">
          只有房主能控制播放，你的进度会自动和房主同步。
        </p>

        <!-- 结束钮仅房主视角照友站改（G-206）；非房主「退出一起看」
             友站报告未覆盖，保持原样 -->
        <button
          v-if="isHost"
          type="button"
          class="wt-pill-btn wt-end"
          @click="emit('dissolve')"
        >
          <Square :size="15" aria-hidden="true" />
          结束同看
        </button>
        <button v-else type="button" class="wt-danger" @click="emit('leave')">
          退出一起看
        </button>
        <p v-if="error" class="wt-error">{{ error }}</p>
      </div>
      <!-- 同步设置入口（G-192）：已开启态同样一处 -->
      <WtSyncSettings
        :params="syncParams"
        @apply="emit('apply-sync-params', $event)"
      />
    </template>
  </PlayerSheet>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Check, Copy, Crown, Square } from 'lucide-vue-next'
import PlayerSheet from '@/components/player/PlayerSheet.vue'
import WtSyncSettings from '@/components/player/WtSyncSettings.vue'
import type { WtMember } from '@/composables/useWatchTogether'
import type { WtSyncParams } from '@/utils/wtSyncParams'

const props = defineProps<{
  open: boolean
  inRoom: boolean
  isHost: boolean
  code: string
  inviteLink: string
  members: WtMember[]
  selfId: number
  busy: boolean
  error: string
  joinCode: string
  syncParams: WtSyncParams
}>()

const emit = defineEmits<{
  'update:open': [boolean]
  create: []
  join: []
  leave: []
  dissolve: []
  'apply-sync-params': [params: WtSyncParams]
}>()

const copied = ref(false)
let copyTimer: number | undefined

watch(
  () => props.open,
  (o) => {
    if (o) copied.value = false
  },
)

async function copyLink(): Promise<void> {
  const link = props.inviteLink
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
  } catch {
    // 剪贴板不可用时退回临时输入框拷贝
    const ta = document.createElement('textarea')
    ta.value = link
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
    } catch {
      /* 复制不了就让用户长按链接手动复制 */
    }
    ta.remove()
  }
  copied.value = true
  window.clearTimeout(copyTimer)
  // 友站实录反馈态持续至少 3 帧（1fps 抽帧≈3 秒），按 3000ms 落定
  copyTimer = window.setTimeout(() => (copied.value = false), 3000)
}
</script>

<style scoped>
.wp-sec {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin: 18px 0 6px;
}
.wp-sec:first-child {
  margin-top: 6px;
}
.wp-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wp-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  border: none;
  background: transparent;
  color: #fff;
  font-size: 15px;
  text-align: left;
  padding: 12px 10px;
  border-radius: 12px;
  cursor: pointer;
}
.wp-row-static {
  cursor: default;
}
.wp-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.wt-note {
  font-size: 14px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.72);
  margin: 6px 0 16px;
}
.wt-guest-note {
  margin: 14px 0 0;
}
.wt-sub {
  font-size: 13.5px;
  color: rgba(255, 255, 255, 0.55);
  margin: 2px 0 16px;
}
/* 邀请卡主钮（G-190）：彩虹描边长胶囊＋文字左对齐，配方与深色主钮同口径
   （深玻璃面＋ ::before mask 彩虹环切，texture-tokens-3） */
.wt-create {
  position: relative;
  overflow: hidden;
  border: 1.5px solid transparent;
  border-radius: 999px;
  background: linear-gradient(var(--ax-glass-btn-d), var(--ax-glass-btn-d));
  color: #fff;
  text-align: left;
  padding: 0 22px;
}
/* 彩虹环切（texture-tokens-3）：彩虹只走 1.5px 细环（::before mask 环切），
   钮面只留玻璃层。 */
.wt-create::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--ax-rainbow-border-w);
  background: var(--ax-rainbow);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
}
/* 开启中态（G-191）：不置灰，钮面覆一层扇形旋转光效 */
.wt-primary.wt-primary--busy:disabled {
  opacity: 1;
}
.wt-primary--busy::after {
  content: '';
  position: absolute;
  inset: -60%;
  background: conic-gradient(
    from 0deg,
    transparent 0deg,
    rgba(255, 255, 255, 0.22) 40deg,
    transparent 100deg
  );
  animation: wt-fan 1.1s linear infinite;
  pointer-events: none;
}
@keyframes wt-fan {
  to {
    transform: rotate(360deg);
  }
}
/* 已进房叠层卡（G-200）：自带底色/描边/投影，与邀请面板拉开层级 */
.wt-room-card {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 20px;
  box-shadow: var(--ax-shadow-float);
  padding: 16px 14px 14px;
}
/* 全宽深灰胶囊钮（G-202 复制钮／G-206 房主结束钮共用） */
.wt-pill-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 50px;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}
.wt-end {
  margin-top: 20px;
}
.wt-crown {
  flex: none;
  color: #ffd479;
}
.wt-primary {
  width: 100%;
  height: 50px;
  border: none;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.94);
  color: #111;
  font-size: 15.5px;
  font-weight: 700;
  cursor: pointer;
}
.wt-primary:disabled {
  opacity: 0.55;
  cursor: default;
}
.wt-danger {
  width: 100%;
  height: 48px;
  margin-top: 20px;
  border: 1px solid rgba(255, 120, 120, 0.35);
  border-radius: 16px;
  background: rgba(255, 80, 80, 0.08);
  color: #ff9c9c;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}
.wt-error {
  margin: 12px 0 0;
  font-size: 13.5px;
  line-height: 1.6;
  color: #ff9c9c;
}
</style>
