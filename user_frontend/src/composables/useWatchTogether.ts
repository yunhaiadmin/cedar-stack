/**
 * 一起看（Watch Together）前端会话客户端。
 *
 * 通道：复用后端既有通知长连 /ws/{user_id}?token=<access_token>
 * （backend/websocket.py 的 JWT 鉴权，与通知同一条连接口径），
 * 一起看消息以 wt.* 的 action/type 区分，不新开第二套通道。
 *
 * 红线：这条通道只传控制事件与进度（play/pause/seek、房间播放态、
 * 成员名单、条目 guid）。本模块不接收、不转发任何流地址——取流
 * 永远走播放页自己的 PlaybackInfo（各自账号的签名链路）。
 */
import { computed, ref } from 'vue'
import { tokenStore } from '@/api'
import { useUserStore } from '@/stores/user'

export interface WtMember {
  user_id: number
  username: string
  is_host: boolean
}

export interface WtState {
  playing: boolean
  position: number
  rate: number
  ts: number
}

export interface WtSnapshot {
  code: string
  item_id: string
  host_id: number
  members: WtMember[]
  state: WtState
}

export interface WtControlEvent {
  event: 'play' | 'pause' | 'seek'
  position: number
  playing: boolean
  rate: number
  from_user_id: number
  from_username: string
  ts: number
}

interface Handlers {
  onCreated?: (snap: WtSnapshot) => void
  onJoined?: (snap: WtSnapshot) => void
  onEvent?: (e: WtControlEvent) => void
  onSync?: (s: WtState) => void
  onMembers?: (members: WtMember[]) => void
  onMemberJoined?: (member: WtMember) => void
  onMemberLeft?: (userId: number) => void
  onHostChanged?: (hostId: number) => void
  onEnded?: (reason: string) => void
  onError?: (message: string, code: string) => void
}

export function useWatchTogether(handlers: Handlers) {
  const userStore = useUserStore()

  const connected = ref(false)
  const inRoom = ref(false)
  const code = ref('')
  const hostId = ref(0)
  const members = ref<WtMember[]>([])
  const roomState = ref<WtState | null>(null)
  const busy = ref(false)
  const lastError = ref('')

  const isHost = computed(
    () => inRoom.value && hostId.value === (userStore.user?.id ?? -1),
  )
  const memberCount = computed(() => members.value.length)

  let ws: WebSocket | null = null
  let connectPromise: Promise<void> | null = null
  let busyTimer: number | undefined

  function resetRoom(): void {
    inRoom.value = false
    code.value = ''
    hostId.value = 0
    members.value = []
    roomState.value = null
  }

  function setBusy(v: boolean): void {
    busy.value = v
    window.clearTimeout(busyTimer)
    if (v) {
      // 兜底：服务端没回音时别让按钮永远转圈
      busyTimer = window.setTimeout(() => {
        if (busy.value) {
          busy.value = false
          lastError.value = '服务器没回应，再试一次。'
          handlers.onError?.(lastError.value, 'timeout')
        }
      }, 8000)
    }
  }

  function applySnapshot(snap: WtSnapshot): void {
    inRoom.value = true
    code.value = snap.code
    hostId.value = snap.host_id
    members.value = snap.members || []
    roomState.value = snap.state
    lastError.value = ''
    setBusy(false)
  }

  function onMessage(raw: MessageEvent): void {
    let msg: Record<string, unknown>
    try {
      msg = JSON.parse(String(raw.data)) as Record<string, unknown>
    } catch {
      return
    }
    const type = String(msg.type || '')
    if (!type.startsWith('wt.')) return
    switch (type) {
      case 'wt.created':
        applySnapshot(msg as unknown as WtSnapshot)
        handlers.onCreated?.(msg as unknown as WtSnapshot)
        break
      case 'wt.joined':
        applySnapshot(msg as unknown as WtSnapshot)
        handlers.onJoined?.(msg as unknown as WtSnapshot)
        break
      case 'wt.member_joined':
        members.value = (msg.members as WtMember[]) || members.value
        handlers.onMembers?.(members.value)
        if (msg.member) handlers.onMemberJoined?.(msg.member as WtMember)
        break
      case 'wt.member_left':
        members.value = (msg.members as WtMember[]) || members.value
        handlers.onMembers?.(members.value)
        handlers.onMemberLeft?.(Number(msg.user_id))
        break
      case 'wt.host_changed':
        hostId.value = Number(msg.host_id)
        members.value = (msg.members as WtMember[]) || members.value
        handlers.onMembers?.(members.value)
        handlers.onHostChanged?.(hostId.value)
        break
      case 'wt.event':
        handlers.onEvent?.(msg as unknown as WtControlEvent)
        break
      case 'wt.sync': {
        const s: WtState = {
          playing: !!msg.playing,
          position: Number(msg.position) || 0,
          rate: Number(msg.rate) || 1,
          ts: Number(msg.ts) || Date.now() / 1000,
        }
        roomState.value = s
        handlers.onSync?.(s)
        break
      }
      case 'wt.ended':
        resetRoom()
        setBusy(false)
        handlers.onEnded?.(String(msg.reason || 'dissolved'))
        break
      case 'wt.error': {
        setBusy(false)
        const message = String(msg.message || '一起看出了点问题，稍后再试。')
        lastError.value = message
        handlers.onError?.(message, String(msg.error || ''))
        break
      }
    }
  }

  function ensureConnected(): Promise<void> {
    if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve()
    if (connectPromise) return connectPromise
    const uid = userStore.user?.id
    const token = tokenStore.access
    if (!uid || !token) {
      return Promise.reject(new Error('还没登录，不能用一起看。'))
    }
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}/ws/${uid}?token=${encodeURIComponent(token)}`
    connectPromise = new Promise<void>((resolve, reject) => {
      const sock = new WebSocket(url)
      ws = sock
      const fail = () => {
        if (ws === sock) ws = null
        connectPromise = null
        connected.value = false
        reject(new Error('一起看连接没建起来，稍后再试。'))
      }
      sock.onopen = () => {
        connected.value = true
        connectPromise = null
        resolve()
      }
      sock.onerror = fail
      sock.onclose = () => {
        if (ws === sock) ws = null
        connectPromise = null
        connected.value = false
        if (inRoom.value) {
          // 连接断了等于离房（服务端按断开清理），本地同步状态
          resetRoom()
          handlers.onEnded?.('disconnected')
        }
      }
      sock.onmessage = onMessage
    })
    return connectPromise
  }

  function send(payload: Record<string, unknown>): void {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
    }
  }

  async function createRoom(itemId: string): Promise<void> {
    lastError.value = ''
    setBusy(true)
    try {
      await ensureConnected()
      send({ action: 'wt.create', item_id: itemId })
    } catch (e) {
      setBusy(false)
      lastError.value = e instanceof Error ? e.message : '一起看连接失败。'
      handlers.onError?.(lastError.value, 'connect')
    }
  }

  async function joinRoom(roomCode: string): Promise<void> {
    lastError.value = ''
    setBusy(true)
    try {
      await ensureConnected()
      send({ action: 'wt.join', code: roomCode })
    } catch (e) {
      setBusy(false)
      lastError.value = e instanceof Error ? e.message : '一起看连接失败。'
      handlers.onError?.(lastError.value, 'connect')
    }
  }

  function sendEvent(event: 'play' | 'pause' | 'seek', position: number, playing?: boolean): void {
    if (!inRoom.value) return
    const payload: Record<string, unknown> = { action: 'wt.event', event, position }
    if (playing !== undefined) payload.playing = playing
    send(payload)
  }

  function sendState(position: number, playing: boolean, rate: number): void {
    if (!inRoom.value || !isHost.value) return
    send({ action: 'wt.state', position, playing, rate })
  }

  function leave(): void {
    if (inRoom.value) send({ action: 'wt.leave' })
    resetRoom()
    setBusy(false)
  }

  function dissolve(): void {
    if (inRoom.value) send({ action: 'wt.dissolve' })
    // 服务端会给全房（含自己）回 wt.ended；本地先复位不等回音
    resetRoom()
    setBusy(false)
  }

  function dispose(): void {
    window.clearTimeout(busyTimer)
    if (inRoom.value) send({ action: 'wt.leave' })
    resetRoom()
    if (ws) {
      try {
        ws.onclose = null
        ws.close()
      } catch {
        /* 关闭失败无声 */
      }
      ws = null
    }
    connected.value = false
  }

  return {
    connected,
    inRoom,
    code,
    hostId,
    members,
    roomState,
    busy,
    lastError,
    isHost,
    memberCount,
    createRoom,
    joinRoom,
    sendEvent,
    sendState,
    leave,
    dissolve,
    dispose,
  }
}
