<template>
  <!-- 工单详情线程：对话全链＋继续回复＋关闭（带二次确认）；管理员回复原样呈现。
       逻辑与旧求救信号页同口径，关闭后只读。 -->
  <div class="rf-thread">
    <div v-if="messagesLoading">
      <div v-for="i in 3" :key="i" class="rf-skeleton" style="height: 56px; margin-bottom: 8px"></div>
    </div>
    <div v-else-if="messagesError" class="rf-err">
      对话加载失败：{{ friendlyError(messagesError) }}<br />
      <button class="rf-retry" @click="fetchMessages">重试</button>
    </div>
    <div v-else ref="messagesEl" class="rf-messages" tabindex="0" role="log" aria-label="对话记录">
      <p v-if="!messages.length" class="rf-messages__empty">还没有对话内容，我们会尽快回复</p>
      <div v-for="msg in messages" :key="msg.id" class="rf-msg" :class="{ 'rf-msg--admin': msg.is_admin }">
        <div class="rf-msg__body">
          <span v-if="msg.is_admin" class="rf-msg__author">管理员回复{{ msg.admin_name ? ` · ${msg.admin_name}` : '' }}</span>
          <span v-else class="rf-msg__author rf-msg__author--me">我的消息</span>
          <span class="rf-msg__text">{{ msg.message }}</span>
          <span class="rf-msg__time">{{ formatDate(msg.created_at) }}</span>
        </div>
      </div>
    </div>

    <div v-if="ticket.status !== 'closed'" class="rf-thread__actions">
      <div class="rf-reply">
        <input
          v-model="replyMessage"
          type="text"
          maxlength="2000"
          placeholder="输入回复…"
          aria-label="输入回复"
          @keyup.enter="(e) => !e.isComposing && replyTicket()"
        />
        <button
          class="rf-reply__send"
          :disabled="replying || !replyMessage.trim()"
          :title="replying ? '发送中' : '发送回复'"
          :aria-label="replying ? '发送中' : '发送回复'"
          @click="replyTicket"
        >➤</button>
      </div>
      <button class="rf-close" @click="showCloseConfirm = true">关闭记录</button>
    </div>
    <p v-else class="rf-closed-hint">这条记录已关闭，不能再回复</p>

    <Modal v-model="showCloseConfirm" size="sm" content-class="rf-confirm">
      <template #header>
        <h3 class="rf-confirm__title">关闭记录</h3>
      </template>
      <p class="rf-confirm__text">确定要关闭这条反馈记录吗？关闭后不能再回复。</p>
      <template #footer>
        <div class="rf-confirm__row">
          <button class="rf-confirm__btn rf-confirm__btn--ghost" @click="showCloseConfirm = false">再想想</button>
          <button class="rf-confirm__btn rf-confirm__btn--go" :disabled="closing" @click="doCloseTicket">{{ closing ? '关闭中' : '确定关闭' }}</button>
        </div>
      </template>
    </Modal>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import { ticketApi, type Ticket, type TicketMessage } from '@/api'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'

const props = defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ changed: [] }>()

const toast = useToast()
const messages = ref<TicketMessage[]>([])
const messagesLoading = ref(false)
const messagesError = ref('')
const messagesEl = ref<HTMLElement | null>(null)
const replyMessage = ref('')
const replying = ref(false)
const showCloseConfirm = ref(false)
const closing = ref(false)
let messagesSeq = 0
let alive = true

function friendlyError(raw: string): string {
  if (/network|failed to fetch|fetch failed|offline|断网/i.test(raw)) return '网络好像断了，检查一下再重试'
  if (/timeout|timed out|超时/i.test(raw)) return '暂时没有回应，稍后再试'
  return '服务暂时异常，稍后再试'
}
async function fetchMessages(): Promise<void> {
  const seq = ++messagesSeq
  messagesLoading.value = true
  messagesError.value = ''
  try {
    const res = await ticketApi.getMessages(props.ticket.id)
    if (seq !== messagesSeq || !alive) return
    messages.value = (res as TicketMessage[]) || []
  } catch (e) {
    if (seq !== messagesSeq || !alive) return
    messages.value = []
    messagesError.value = humanizeError(e)
  } finally {
    if (seq === messagesSeq) messagesLoading.value = false
  }
  if (seq === messagesSeq && alive) {
    await nextTick()
    const el = messagesEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
}
async function replyTicket(): Promise<void> {
  if (!replyMessage.value.trim() || replying.value) return
  replying.value = true
  try {
    await ticketApi.reply(props.ticket.id, replyMessage.value.trim())
    replyMessage.value = ''
    await fetchMessages()
    emit('changed')
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    replying.value = false
  }
}
async function doCloseTicket(): Promise<void> {
  if (closing.value) return
  closing.value = true
  try {
    await ticketApi.close(props.ticket.id)
    toast.success('记录已关闭')
    showCloseConfirm.value = false
    emit('changed')
  } catch (e) {
    toast.error(humanizeError(e))
    closing.value = false
  }
}
function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  if (Number.isNaN(date.getTime())) return '时间未知'
  const now = new Date()
  const diff = Math.max(0, now.getTime() - date.getTime())
  const days = Math.floor(diff / (1000 * 60 * 60 * 24))
  if (days === 0) {
    const hours = Math.floor(diff / (1000 * 60 * 60))
    if (hours === 0) {
      const minutes = Math.floor(diff / (1000 * 60))
      return minutes === 0 ? '刚刚' : `${minutes}分钟前`
    }
    return `${hours}小时前`
  }
  if (days === 1) return '昨天'
  if (days < 7) return `${days}天前`
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
watch(() => props.ticket.id, () => {
  replyMessage.value = ''
  void fetchMessages()
})
onMounted(() => { void fetchMessages() })
onUnmounted(() => { alive = false })
</script>

<style scoped>
/* 工单线程：缺省深色，html[data-theme='light'] 时换浅。 */
.rf-thread { margin-top: 10px; border-top: 1px dotted #333336; padding-top: 10px; }
.rf-skeleton { background: #1e1f21; border-radius: 10px; animation: rf-pulse 1.4s ease-in-out infinite; }
@keyframes rf-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
.rf-err { font-size: 13px; color: #fca5a5; line-height: 1.6; }
.rf-retry { margin-top: 6px; padding: 3px 12px; font-size: 12px; color: #fbfcfd; background: transparent; border: 1px solid currentColor; border-radius: 999px; cursor: pointer; opacity: 0.8; }
.rf-messages { display: flex; flex-direction: column; gap: 8px; max-height: 320px; overflow-y: auto; }
.rf-messages__empty { margin: 0; padding: 1.5rem 0; text-align: center; font-size: 12px; color: #9ca3af; }
.rf-msg { padding: 10px 12px; border-radius: 10px; background: #1e1f21; }
.rf-msg--admin { background: rgba(47, 143, 224, 0.14); }
.rf-msg__body { display: flex; flex-direction: column; gap: 4px; }
.rf-msg__author { font-size: 12px; font-weight: 700; color: #6ea8fe; }
.rf-msg__author--me { color: #9ca3af; }
.rf-msg__text { color: #fbfcfd; font-size: 13px; line-height: 1.7; word-break: break-word; white-space: pre-wrap; }
.rf-msg__time { font-size: 11px; color: #9ca3af; }
.rf-thread__actions { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.rf-reply { display: flex; gap: 8px; }
.rf-reply input {
  flex: 1; min-height: 44px; padding: 10px 12px; background: #17181a;
  border: 1.5px solid #26262a; border-radius: 10px; color: #fbfcfd; font-size: 15px; outline: none;
}
.rf-reply input:focus { border-color: #6b7280; }
.rf-reply__send {
  display: flex; align-items: center; justify-content: center; width: 44px; height: 44px;
  border-radius: 10px; border: none; background: #fbfcfd; color: #111111; font-size: 18px; cursor: pointer; flex-shrink: 0;
}
.rf-reply__send:disabled { opacity: 0.45; cursor: not-allowed; }
.rf-close {
  min-height: 44px; border-radius: 10px; border: 1.5px solid #26262a; background: #1e1f21;
  color: #9ca3af; font-size: 13px; font-weight: 700; cursor: pointer;
}
.rf-closed-hint { font-size: 12px; color: #9ca3af; margin: 10px 0 0; text-align: center; }
html[data-theme='light'] .rf-thread { border-top-color: #e5e5e5; }
html[data-theme='light'] .rf-skeleton { background: #f0f0f1; }
html[data-theme='light'] .rf-err { color: #dc2626; }
html[data-theme='light'] .rf-retry { color: #111111; }
html[data-theme='light'] .rf-messages__empty { color: #6b7280; }
html[data-theme='light'] .rf-msg { background: #f9f9f9; }
html[data-theme='light'] .rf-msg--admin { background: #e3ecf8; }
html[data-theme='light'] .rf-msg__author { color: #2563eb; }
html[data-theme='light'] .rf-msg__author--me { color: #6b7280; }
html[data-theme='light'] .rf-msg__text { color: #111111; }
html[data-theme='light'] .rf-msg__time { color: #6b7280; }
html[data-theme='light'] .rf-reply input { background: #ffffff; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-reply input:focus { border-color: #9ca3af; }
html[data-theme='light'] .rf-reply__send { background: #111111; color: #ffffff; }
html[data-theme='light'] .rf-close { border-color: #e5e5e5; background: #f9f9f9; color: #6b7280; }
html[data-theme='light'] .rf-closed-hint { color: #6b7280; }
</style>

<style>
/* 关闭确认弹窗：Modal Teleport 到 body，scoped 够不到，写全局并挂在 .rf-confirm 下。
   缺省深色，html[data-theme='light'] 时换浅。 */
.rf-confirm .modal-content-wrapper { background: #17181a; border: 1.5px solid #26262a; border-radius: 14px; color: #fbfcfd; }
.rf-confirm .modal-header { border-bottom: 1px solid #26262a; padding: 14px 18px; }
.rf-confirm__title { color: #fbfcfd; font-size: 16px; font-weight: 800; margin: 0; }
.rf-confirm .modal-close { color: #9ca3af; }
.rf-confirm .modal-body { padding: 16px 18px; }
.rf-confirm .modal-footer { border-top: 1px solid #26262a; padding: 12px 18px; }
.rf-confirm__text { font-size: 14px; line-height: 1.7; color: #fbfcfd; margin: 0; }
.rf-confirm__row { display: flex; gap: 10px; }
.rf-confirm__btn { flex: 1; min-height: 44px; border-radius: 10px; font-size: 14px; font-weight: 700; cursor: pointer; }
.rf-confirm__btn--ghost { background: #1e1f21; border: 1.5px solid #333336; color: #9ca3af; }
.rf-confirm__btn--go { background: #fbfcfd; border: none; color: #111111; }
.rf-confirm__btn:disabled { opacity: 0.5; cursor: wait; }
html[data-theme='light'] .rf-confirm .modal-content-wrapper { background: #ffffff; border-color: #e5e5e5; color: #111111; }
html[data-theme='light'] .rf-confirm .modal-header { border-bottom-color: #e5e5e5; }
html[data-theme='light'] .rf-confirm__title { color: #111111; }
html[data-theme='light'] .rf-confirm .modal-close { color: #6b7280; }
html[data-theme='light'] .rf-confirm .modal-footer { border-top-color: #e5e5e5; }
html[data-theme='light'] .rf-confirm__text { color: #111111; }
html[data-theme='light'] .rf-confirm__btn--ghost { background: #f9f9f9; border-color: #e5e5e5; color: #6b7280; }
html[data-theme='light'] .rf-confirm__btn--go { background: #111111; color: #ffffff; }
</style>
