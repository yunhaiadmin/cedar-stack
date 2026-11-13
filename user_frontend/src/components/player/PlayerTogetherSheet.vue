<template>
  <PlayerSheet :open="open" title="一起看" @update:open="emit('update:open', $event)">
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
        <p class="wt-note">
          开一个一起看房间，把链接发给朋友。大家各自用自己的账号播放同一部片，播放、暂停和进度自动同步。
        </p>
        <button
          type="button"
          class="wt-primary"
          :disabled="busy"
          @click="emit('create')"
        >
          {{ busy ? '正在创建…' : '创建一起看' }}
        </button>
      </template>
      <p v-if="error" class="wt-error">{{ error }}</p>
    </template>

    <!-- 已进房 -->
    <template v-else>
      <p class="wp-sec">邀请链接</p>
      <div class="wt-link-box">
        <span class="wt-link">{{ inviteLink }}</span>
        <button type="button" class="wt-copy" @click="copyLink">
          {{ copied ? '已复制' : '复制' }}
        </button>
      </div>
      <p class="wt-note wt-note-sm">房间码 {{ code }} · 把链接发给朋友，对方登录后点开就能加入。</p>

      <p class="wp-sec">成员（{{ members.length }}）</p>
      <div class="wp-list">
        <div v-for="m in members" :key="m.user_id" class="wp-row wp-row-static">
          <span class="wp-row-main">
            <span class="wt-avatar">{{ (m.username || '?').slice(0, 1).toUpperCase() }}</span>
            <span>{{ m.username || '用户' }}{{ m.user_id === selfId ? '（我）' : '' }}</span>
          </span>
          <span v-if="m.is_host" class="wt-host-badge">房主</span>
        </div>
      </div>

      <button
        v-if="isHost"
        type="button"
        class="wt-danger"
        @click="emit('dissolve')"
      >
        结束一起看
      </button>
      <button v-else type="button" class="wt-danger" @click="emit('leave')">
        退出一起看
      </button>
      <p v-if="error" class="wt-error">{{ error }}</p>
    </template>
  </PlayerSheet>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import PlayerSheet from '@/components/player/PlayerSheet.vue'
import type { WtMember } from '@/composables/useWatchTogether'

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
}>()

const emit = defineEmits<{
  'update:open': [boolean]
  create: []
  join: []
  leave: []
  dissolve: []
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
  copyTimer = window.setTimeout(() => (copied.value = false), 2000)
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
.wt-note-sm {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin: 8px 0 0;
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
.wt-link-box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.05);
}
.wt-link {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wt-copy {
  flex: none;
  border: none;
  border-radius: 10px;
  padding: 7px 14px;
  background: rgba(255, 255, 255, 0.92);
  color: #111;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.wt-avatar {
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.wt-host-badge {
  flex: none;
  font-size: 12px;
  color: #ffd479;
  border: 1px solid rgba(255, 212, 121, 0.4);
  padding: 2px 8px;
  border-radius: 999px;
}
</style>
