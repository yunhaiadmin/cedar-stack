/** 一起看管理 API（/api/admin/watch-together/*，见 backend/api/admin_watch_together.py） */
import { get, post } from '@/utils/request'

export interface WtMember {
  user_id: number
  username: string
  is_host: boolean
}

export interface WtRoomState {
  playing: boolean
  /** 按房间播放态外推的当前进度（秒） */
  position: number
  rate: number
  ts: number
}

export interface WtRoom {
  code: string
  item_id: string
  item_name: string
  host_id: number
  host_name: string
  /** 建房时间（epoch 秒） */
  created_at: number | null
  members: WtMember[]
  member_count: number
  state: WtRoomState
  max_members: number
  ttl_seconds: number
}

export const fetchWtRooms = () =>
  get<{ total: number; items: WtRoom[] }>('/watch-together/rooms')

export const fetchWtRoom = (code: string) =>
  get<WtRoom>(`/watch-together/rooms/${encodeURIComponent(code)}`)

/** 强制关房：服务端广播结束并删房间记录，写审计日志 */
export const closeWtRoom = (code: string) =>
  post<{ ok: boolean; code: string; member_count: number }>(
    `/watch-together/rooms/${encodeURIComponent(code)}/close`)
