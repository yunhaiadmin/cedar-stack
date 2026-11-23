/** v2.3.0 经济系统管理 API（/api/admin/economy/*，详见 backend/api/admin.py） */
import { get, post, put, del } from '@/utils/request'

// ==================== 类型 ====================

export interface EconomyStats {
  orders: { pending: number; revenue: number; today_paid_count: number }
  invitations: number
}

export interface PlanRowFull {
  id: number
  name: string
  description: string | null
  price: number
  duration_days: number
  features: string[] | null
  /** 订阅分档权益表 {权益键: 值}（line_category 为字符串，其余为整数）；null/缺键 = 该项跟随全站默认（键与区间见后端 backend/entitlements.py） */
  entitlements?: Record<string, number | string> | null
  is_active: boolean
  is_popular: boolean
  sort_order: number
  /** 一个服一个：留空时后端归到面板当前服 */
  realm_id?: number | null
  realm_name?: string
}

export interface OrderRow {
  order_id: string
  kind: 'subscription'
  item_name: string
  username: string
  user_id: number
  amount: number
  status: string
  payment_method: string | null
  created_at: string | null
  paid_at: string | null
}

export interface InvitationRow {
  id: number
  inviter: string
  inviter_id?: number
  invitee: string
  invitee_id?: number
  created_at: string | null
}

export interface EconomySettings {
  [key: string]: string
}

// ==================== 统计 ====================

export const fetchEconomyStats = () => get<EconomyStats>('/economy/stats')

// ==================== 订阅总览 ====================

/** 按用户直接授予订阅（与用户管理页共享后端逻辑）；
 * 唯一实现：`admin.ts` 的 grantSubscription 只是转调这里（economy 版 data 还可带 realm_id，多服下跟随套餐开服）。 */
export const grantUserSubscription = (
  userId: number,
  data: { plan_id: number; duration_days: number; realm_id?: number }
) => post<{ success: boolean }>(`/users/${userId}/subscriptions`, data)

/** 唯一实现：`admin.ts` 的 extendSubscription 只是转调这里。 */
export const extendUserSubscription = (subscriptionId: number, days: number) =>
  post<{ success: boolean }>(`/subscriptions/${subscriptionId}/extend`, { days })

// ==================== 订阅套餐 ====================

/**
 * 套餐清单；`realm_id=0` = 全部服，不传 = 当前服。
 * 唯一实现：`admin.ts` 的 fetchPlans 只是转调这里（only_active: true，供授予订阅下拉只取启用中的套餐）；
 * 套餐管理页（Goods.vue）直接用这里取全量，不过滤。
 */
export const fetchEconomyPlans = (realm_id?: number, opts?: { only_active?: boolean }) => {
  const params: Record<string, number | boolean> = {}
  if (realm_id !== undefined) params.realm_id = realm_id
  if (opts?.only_active) params.only_active = true
  return get<{
    plans: PlanRowFull[]
    realm_id: number | null
    active_realm_id: number
    realms: { id: number; name: string }[]
  }>('/economy/plans', Object.keys(params).length ? params : undefined)
}

export const createEconomyPlan = (data: Omit<PlanRowFull, 'id'>) => post<{ success: boolean }>('/economy/plans', data)

export const updateEconomyPlan = (id: number, data: Omit<PlanRowFull, 'id'>) =>
  put<{ success: boolean }>(`/economy/plans/${id}`, data)

export const deleteEconomyPlan = (id: number) => del<{ success: boolean; message?: string }>(`/economy/plans/${id}`)

// ==================== 订单 ====================

export const fetchEconomyOrders = (params: {
  status_filter?: string
  kind?: string
  search?: string
  limit?: number
  offset?: number
} = {}) => get<{ total: number; orders: OrderRow[] }>('/economy/orders', params)

export const markOrderPaid = (orderId: string) =>
  post<{ success: boolean }>(`/economy/orders/${orderId}/mark-paid`)

// ==================== 订单关单 / 退款（v2.9.0） ====================
// 后端：backend/api/orders_admin.py

export interface RefundResult {
  success: boolean
  order_id: string
  kind: 'subscription'
  status: string
  /** 实际回滚的会员天数（订阅时） */
  revoked_days: number
  /** 同时撤回的邀请人返利（美元） */
  rebate_reversed: number
  /** 返利撤回失败（邀请人余额不足；计提行已标记 reversal_owed 待处理） */
  rebate_reversal_failed?: boolean
  /** 回滚后是否已撤销该订阅 */
  cancelled: boolean
  subscription_end?: string
}

export interface RefundRecord {
  order_id: string
  kind: 'subscription'
  status: 'refunded' | 'closed'
  username: string
  reason: string
  at: string | null
}

/** 关闭未支付的订单（作废，不碰权益） */
export const closeOrder = (orderId: string) =>
  post<{ success: boolean; status: string }>(`/economy/orders/${orderId}/close`)

/** 退款：默认回滚权益（会员天数 / 钱包返利） */
export const refundOrder = (
  orderId: string,
  data: { reason?: string; revoke_entitlement?: boolean; allow_negative?: boolean; reverse_rebate?: boolean },
) => post<RefundResult>(`/economy/orders/${orderId}/refund`, data)

/** 最近的退款 / 关单记录（带原因，供复盘对账） */
export const fetchRefundRecords = (limit = 20) =>
  get<{ records: RefundRecord[] }>('/economy/refunds', { limit })

// ==================== 优惠券（v2.10.0） ====================
// 后端：backend/api/coupons_admin.py + backend/coupons.py
// 额度是预订制的：下单占额度（reserved）、付款转已用（consumed）、关单/退款释放（released）。

export interface CouponRow {
  id: number
  code: string
  /** recharge 为存量已下线值（新建不再产生），列表里标注「已下线」 */
  kind: 'all' | 'subscription' | 'recharge'
  discount_type: 'percent' | 'fixed'
  /** percent: 实付百分比（90 = 九折）；fixed: 减免金额（美元） */
  value: number
  min_amount: number
  max_discount: number
  realm_id: number | null
  realm_name: string
  /** 限定套餐，null = 全部套餐可用 */
  plan_id: number | null
  plan_name: string
  /** 总次数上限，0 = 不限 */
  max_uses: number
  /** 已占用（预订 + 已消费） */
  use_count: number
  /** 每人限用，0 = 不限 */
  per_user_limit: number
  valid_from: string | null
  valid_until: string | null
  is_active: boolean
  note: string
  created_at: string | null
  stats: { reserved: number; consumed: number; released: number }
  usable: boolean
}

export interface CouponUsageRow {
  id: number
  coupon_id: number
  code: string
  user_id: number
  username: string
  order_id: string
  kind: string
  status: 'reserved' | 'consumed' | 'released'
  list_price: number
  discount_amount: number
  paid_amount: number
  created_at: string | null
  closed_at: string | null
}

export interface CouponSettings {
  enabled: boolean
  /** 超时未支付的预订多久自动关单并还额度（小时，0 = 不自动清理） */
  reserve_hours: number
  /** 当前占用中的预订数 */
  active_usage: number
}

export interface CouponCreatePayload {
  count: number
  code?: string
  kind: 'all' | 'subscription'
  discount_type: 'percent' | 'fixed'
  value: number
  min_amount?: number
  max_discount?: number
  realm_id?: number | null
  /** 限定套餐，null = 全部套餐可用 */
  plan_id?: number | null
  max_uses?: number
  per_user_limit?: number
  valid_until?: string
  valid_days?: number
  note?: string
}

export const fetchCoupons = (params: { kind?: string; active?: string; search?: string; limit?: number } = {}) =>
  get<{ enabled: boolean; total: number; coupons: CouponRow[] }>('/economy/coupons', params)

export const createCoupons = (data: CouponCreatePayload) =>
  post<{ success: boolean; count: number; coupons: CouponRow[] }>('/economy/coupons', data)

export const updateCoupon = (id: number, data: Partial<Omit<CouponCreatePayload, 'count' | 'code'>> & { is_active?: boolean; valid_from?: string }) =>
  put<{ success: boolean; coupon: CouponRow }>(`/economy/coupons/${id}`, data)

export const deleteCoupon = (id: number) =>
  del<{ success: boolean; message: string }>(`/economy/coupons/${id}`)

export const fetchCouponUsages = (params: { limit?: number; coupon_id?: number } = {}) =>
  get<{ records: CouponUsageRow[] }>('/economy/coupons/usages', params)

export const fetchCouponSettings = () => get<CouponSettings>('/economy/coupons/settings')

export const updateCouponSettings = (data: { reserve_hours?: number }) =>
  put<{ success: boolean; enabled: boolean; reserve_hours: number }>('/economy/coupons/settings', data)

// ==================== 邀请 ====================

export const fetchInvitations = (params: { limit?: number; inviter_id?: number } = {}) =>
  get<{ total?: number; records: InvitationRow[] }>('/economy/invitations', params)

// ==================== 经济设置 ====================

export const fetchEconomySettings = () => get<{ settings: EconomySettings }>('/economy/settings')

export const updateEconomySettings = (settings: EconomySettings) =>
  put<{ success: boolean; changed: string[] }>('/economy/settings', { settings })

// ==================== 防盗链（v2.52.0） ====================
// 后端：backend/api/admin_economy.py（/api/admin/hotlink-config）
// 配置读取唯一实现：backend/emby_server/play_sign.py

export interface HotlinkConfigPayload {
  play_sign_ttl: number
  /** Referer 白名单（逗号分隔域名），不传则后端不改 */
  allowed_referers?: string
}

export interface HotlinkConfig extends HotlinkConfigPayload {
  enabled: boolean
  defaults: { enabled: boolean; play_sign_ttl: number }
}

/** GET /hotlink-config 的白名单字段：后端返回归一化后的域名数组 */
export type HotlinkConfigGet = Omit<HotlinkConfig, 'allowed_referers'> & {
  allowed_referers?: string | string[]
}

export const fetchHotlinkConfig = () => get<HotlinkConfigGet>('/hotlink-config')

export const updateHotlinkConfig = (payload: HotlinkConfigPayload) =>
  put<HotlinkConfig>('/hotlink-config', payload)

// ==================== 列表去重缓存（v2.54.0） ====================
// 后端：backend/api/admin_economy.py（/api/admin/items-dedup-cache-config）
// 配置读取唯一实现：backend/emby_server/items_dedup_cache.py

export interface ItemsDedupCacheConfigPayload {
  ttl_seconds: number
  max_entries: number
}

export interface ItemsDedupCacheConfig extends ItemsDedupCacheConfigPayload {
  enabled: boolean
  defaults: ItemsDedupCacheConfigPayload & { enabled: boolean }
}

export const fetchItemsDedupCacheConfig = () =>
  get<ItemsDedupCacheConfig>('/items-dedup-cache-config')

export const updateItemsDedupCacheConfig = (payload: ItemsDedupCacheConfigPayload) =>
  put<ItemsDedupCacheConfig>('/items-dedup-cache-config', payload)

// ==================== 转存（v2.55.0） ====================
// 后端：backend/api/admin_economy.py（/api/admin/transfer-config）
// 配置读取唯一实现：backend/emby_server/transfer_variant.py

export interface TransferConfig {
  /** 跨 provider 转存总开关（默认开；关 = 只支持同 provider 转存） */
  cross_provider_transfer_enabled: boolean
  /** 跨 provider 搬字节在途并发上限（默认 2，范围 1~8） */
  transfer_cross_lane_concurrency: number
  /** 剧集预热总开关（默认开；关 = 播完不再给后续集建预热） */
  transfer_series_preheat_enabled: boolean
  /** 剧集预热搬字节独立日帽：强信号时每人每天最多建几张搬字节预热单（默认 2，范围 1~16；与 prefetch 帽分开计） */
  transfer_series_preheat_daily_max: number
  /** 剧集预热候选集数：播完一集后给同季后续几集建预热（默认 3，范围 1~10） */
  transfer_series_preheat_next_count: number
  /** 跨单 aging 提权阈值（秒）：慢组跨单排队超过该秒数后按点播组待遇参与挑选，防无限期饿死（默认 1800，范围 60~86400） */
  transfer_cross_aging_sec: number
  /** 转存执行道并发（默认 8，范围 1~32） */
  transfer_fast_lane_concurrency: number
  /** 单用户道内在飞上限（默认 2，范围 1~16） */
  transfer_per_owner_inflight: number
  /** 换绑节流豁免计数窗口天数（默认 7，范围 1~365） */
  transfer_rebind_waiver_window_days: number
  /** 窗口内换绑豁免可用次数（默认 1，范围 1~20） */
  transfer_rebind_waiver_max_uses: number
  /** 类别级熔断规则（JSON 数组文本 [{provider, error_kind}]；error_kind="*" = 该 provider 全部类别；默认 "[]" = 不暂停） */
  transfer_paused_categories: string
}

export type TransferConfigPayload = TransferConfig

export const fetchTransferConfig = () => get<TransferConfig>('/transfer-config')

export const updateTransferConfig = (payload: TransferConfigPayload) =>
  put<TransferConfig>('/transfer-config', payload)
// ==================== 挂载直读出流（2026-10-30） ====================
// 后端：backend/api/admin_economy.py（/api/admin/mount-serving-config）
// 配置读取唯一实现：backend/emby_server/mount_serving.py

export interface MountServingConfigPayload {
  enabled: boolean
  /** 白名单：storage_mounts id 列表，空 = 不介入任何 mount */
  mount_ids: number[]
  /** 本地根映射串：id=/绝对路径，逗号分隔 */
  local_roots: string
}

export interface MountServingConfig extends MountServingConfigPayload {
  defaults: { enabled: boolean; mount_ids: number[]; local_roots: string }
}

export const fetchMountServingConfig = () =>
  get<MountServingConfig>('/mount-serving-config')

export const updateMountServingConfig = (payload: MountServingConfigPayload) =>
  put<MountServingConfig>('/mount-serving-config', payload)

// ==================== Drive API 限速（2026-10-30） ====================
// 后端：backend/api/admin_economy.py（/api/admin/drive-api-config）
// 配置读取唯一实现：backend/emby_server/drive_changes.py

export interface DriveApiConfig {
  /** Drive API 全局速率上限（QPS）：扫描列举与 Changes 增量共用，默认 8，范围 1~20 */
  qps: number
  defaults: { qps: number }
}

export type DriveApiConfigPayload = { qps: number }

export const fetchDriveApiConfig = () =>
  get<DriveApiConfig>('/drive-api-config')

export const updateDriveApiConfig = (payload: DriveApiConfigPayload) =>
  put<DriveApiConfig>('/drive-api-config', payload)

// ==================== 播放交付计划（2026-11-24，O-02 补缺） ====================
// 后端：backend/api/admin_economy.py（/api/admin/delivery-plan-config）
// 配置读取唯一实现：backend/emby_server/delivery_plan.py

export interface DeliveryPlanConfig {
  /** 交付编排器总开关：关时播放回落现行直出路径 */
  enabled: boolean
  /** 交付计划 TTL（秒）：30~3600，默认 600 */
  ttl_sec: number
  defaults: { enabled: boolean; ttl_sec: number }
}

export type DeliveryPlanConfigPayload = { enabled: boolean; ttl_sec: number }

export const fetchDeliveryPlanConfig = () =>
  get<DeliveryPlanConfig>('/delivery-plan-config')

export const updateDeliveryPlanConfig = (payload: DeliveryPlanConfigPayload) =>
  put<DeliveryPlanConfig>('/delivery-plan-config', payload)

// ==================== 到期续费提醒（v2.8.0） ====================
// 后端：backend/api/reminders_admin.py + backend/reminders.py

/** 一次提醒动作的对象（面板预览用） */
export interface ReminderDue {
  user_id: number
  subscription_id: number
  /** 7d / 3d / 1d / expired */
  kind: string
  days_left: number
}

export interface ReminderStatus {
  enabled: boolean
  /** 提前几天提醒（默认 7/3/1） */
  thresholds: number[]
  interval_seconds: number
  /** 当前待发：临期 / 已到期 */
  pending_reminders: number
  pending_expired: number
  total_sent: number
  recent: { id: number; username: string; kind: string; sent_at: string | null }[]
}

export interface ReminderRunSummary {
  enabled: boolean
  dry_run: boolean
  thresholds: number[]
  reminded: number
  expired_notified: number
  skipped: number
  failed: number
  due: ReminderDue[]
}

export const fetchReminderStatus = () => get<ReminderStatus>('/economy/expiry-reminders')

export const updateReminderSettings = (
  settings: { expiry_reminder_days?: string }
) =>
  put<{ success: boolean; settings: Record<string, unknown>; status: ReminderStatus }>(
    '/economy/expiry-reminders/settings', { settings }
  )

/** 立即执行一轮；dryRun=true 只预览会发给谁（不落库、不发消息） */
export const runReminders = (dryRun = false) =>
  post<ReminderRunSummary>(`/economy/expiry-reminders/run${dryRun ? '?dry_run=true' : ''}`)

// 会员等级 API 已随等级体系于 2026-11-14 删除（/member-levels 端点下线）。
