/**
 * v4 游戏化「探险日志」入口配置 —— 唯一数据源
 *
 * 所有游戏设施的名称、路径、图标、描述都在这里定义，不在组件里 hardcode。
 * 新增游戏页面时：1) 在 router/index.ts 加路由；2) 在这里加一条 facilities 记录。
 * 后续如需走后端配置（管理后台可配），把这里换成接口拉取即可，组件层不用改。
 */

/** 营地地图分组 key（大本营各分区共用；头像菜单排序也用 order） */
export type GameFacilityGroup = 'daily' | 'social' | 'asset' | 'account'

export interface GameFacility {
  /** 唯一 key */
  key: string
  /** 显示名（卡片标题） */
  name: string
  /** 路由路径，必须已在 src/router/index.ts 声明 */
  path: string
  /** 地图针表情。页面标题和头像菜单不再使用这枚图标。 */
  icon: string
  /** 一句话介绍 */
  desc: string
  /** 印章文字（v4-stamp 风格） */
  stamp: string
  /** 营地地图分组：daily 每日必做 / social 社交 / asset 资产 / account 账号 */
  group: GameFacilityGroup
  /** 组内排序（按现有数组顺序从 0 排起，头像菜单与大本营都按它排） */
  order: number
  /** 总开关：false 时大本营地图与头像菜单都不显示（默认 true，向后兼容） */
  enabled: boolean
  /** 新手引导里用的更短一句话（小白版介绍；不写则 fallback desc） */
  guideTip?: string
}

/** 营地地图各分区的分区牌文案（顺序即地图展示顺序） */
export const gameFacilityGroups: {
  key: GameFacilityGroup
  icon: string
  title: string
  sub: string
}[] = [
  { key: 'daily', icon: '⛺', title: '营地门口', sub: '每日必做' },
  { key: 'social', icon: '📡', title: '传讯高地', sub: '社交' },
  { key: 'asset', icon: '📒', title: '账房小屋', sub: '资产' },
  { key: 'account', icon: '⚙️', title: '账号小屋', sub: '账号' },
]

/** 全部游戏设施（营地设施）—— order 即数组顺序，0 起 */
export const gameFacilities: GameFacility[] = [
  {
    key: 'operator',
    name: '播放账号',
    path: '/game/operator',
    icon: '🔌',
    desc: '买完会员来这儿接线 —— 拿账号密码，去播放器里连上就能看。',
    stamp: '营地 · 接线处',
    group: 'daily',
    order: 0,
    enabled: true,
    guideTip: '刚买完会员？先来这儿拿账号密码，再去播放器里登录。',
  },
  {
    key: 'requests',
    name: '求片反馈',
    path: '/game/requests',
    icon: '💬',
    desc: '想看的片、想追的新集、资源有问题，都在这里提交和查看进度。',
    stamp: '帮助 · 求片反馈',
    group: 'daily',
    order: 3,
    enabled: true,
  },
  {
    key: 'account',
    name: '账号设置',
    path: '/game/account',
    icon: '⚙️',
    desc: '改登录密码、看我的订阅、管我的设备 —— 账号自己的事都在这儿。',
    stamp: '账号 · 小屋',
    group: 'account',
    order: 10,
    enabled: true,
    guideTip: '改密码、看订阅、清设备，账号的事来这儿办。',
  },
]

/**
 * 按 key 取设施印章（各游戏页 <span class="v4-stamp"> 模板直接绑定，
 * 不在组件里 hardcode —— R27 修复：模板硬编码「探险日志 · 营地设施」根本没读这里）
 */
export function facilityStamp(key: string): string {
  return gameFacilities.find((f) => f.key === key)?.stamp ?? '探险日志 · 营地设施'
}
