/**
 * 官网落地页文案（GET /api/site/landing）
 *
 * 落地页的首屏文案、合作方名单、设备区、展示区与卖点三卡文本，
 * 管理后台「官网内容」页可配（SystemConfig 单键 landing_content），
 * 前端只做数据驱动渲染。
 *
 * 回落：初始值就是现行写死文案（与后端 LANDING_DEFAULT_CONTENT
 * 逐字一致）。拉取失败（后端没起、老版本后端没有这个端点）或
 * 下发结构不完整时保持内置文案，官网不开天窗。
 */
import { reactive } from 'vue'
import { landingApi, type LandingContent } from '@/api'

export const landingContent = reactive<LandingContent>({
  hero: {
    title_lines: ['你的私人影院', '随时随地开场'],
    subs: [
      '无广告干扰，沉浸观影从按下播放开始',
      '手机、电视、电脑，观看进度多端同步',
      '新片自动入库，追剧更新不用催',
    ],
    scroll_hint: '↓ 向下探索更多精彩',
    specs: [
      { value: '4K', sub: '超高清' },
      { value: 'HDR10+', sub: '高动态范围' },
      { value: 'Dolby Vision', sub: '杜比视界' },
      { value: '无广告干扰', sub: '沉浸体验' },
    ],
  },
  partners: {
    eyebrow: 'STREAMING PARTNERS',
    names: ['Netflix', 'Disney+', 'HBO Max'],
  },
  devices: {
    eyebrow: 'CLIENTS & SPECS',
    title_lines: ['在你顺手的设备上，', '以它最好的规格播放'],
    sub: '播放交给专业客户端，服务器只做整理与供给——手机、电视、电脑，打开就能看。',
    marquee_clients: [
      'Emby', 'Infuse', 'Kodi', 'Apple TV', 'Android TV',
      'Samsung TV', 'LG webOS', 'Windows', 'macOS', 'iOS', 'Android',
    ],
    marquee_specs: [
      '4K Ultra HD', 'HDR10+', 'Dolby Vision', 'Dolby Atmos',
      'DTS:X', 'H.265 / HEVC', 'Hi-Res Audio', '空间音频',
    ],
  },
  showcase: {
    cinema: {
      eyebrow: 'CINEMA EXPERIENCE',
      title: '影院级画面',
      sub: '4K Ultra HD · HDR10+ · Dolby Vision',
    },
    audio: {
      eyebrow: 'AUDIO EXPERIENCE',
      title: '影院级声音',
      sub: 'Dolby Atmos · Hi-Res Audio · 空间音频 · DTS:X 全兼容',
    },
  },
  essentials: {
    eyebrow: 'SERVICE ESSENTIALS',
    title: '一份订阅，全部搞定',
    sub: '一个账号打通所有设备与服务，不用为每块屏幕单独折腾。',
    cards: [
      {
        eyebrow: 'MULTI-DEVICE SYNC',
        title_lines: ['一部片子，', '所有屏幕接着看'],
        desc: '在电视上看到一半，出门用手机继续。播放进度、收藏与观看记录，在你的所有设备间自动同步。',
        chips: ['TV', 'Computer', 'Tablet', '手机'],
        points: [],
      },
      {
        eyebrow: 'ZERO INTERRUPTION',
        title_lines: ['没有广告，', '只有电影'],
        desc: '从按下播放到片尾字幕，全程无插播、无贴片，沉浸感不被打断。',
        chips: [],
        points: [],
      },
      {
        eyebrow: 'EVER-GROWING LIBRARY',
        title_lines: ['片库自己长大'],
        desc: '新片自动扫描入库、刮削补齐海报与资料；追的剧更新了，自动出现在你的片库里。',
        chips: [],
        points: [
          { title: '自动入库', text: '新片到盘，自动扫描进片库' },
          { title: '资料补齐', text: '海报、简介与评分自动刮削归位' },
          { title: '追剧更新', text: '追的剧一出新集，自动跟上' },
        ],
      },
    ],
  },
})

let initialized = false

export async function initLandingContent(): Promise<void> {
  if (initialized) return
  initialized = true
  try {
    const data = await landingApi.get()
    if (!data) return
    /* 结构完整才接管：主标题/副文案/三卡缺一不可，残缺下发保持内置文案 */
    if (
      Array.isArray(data.hero?.title_lines) && data.hero.title_lines.length &&
      Array.isArray(data.hero?.subs) && data.hero.subs.length &&
      Array.isArray(data.essentials?.cards) && data.essentials.cards.length === 3
    ) {
      landingContent.hero = data.hero
      landingContent.partners = data.partners
      landingContent.devices = data.devices
      landingContent.showcase = data.showcase
      landingContent.essentials = data.essentials
    }
  } catch {
    /* 保持内置文案：落地页仍完整渲染现行版本 */
  }
}
