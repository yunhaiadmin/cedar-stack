/** 官网落地页文案 API（/api/admin/landing/*，见 backend/api/admin_landing.py） */
import { del, get, put } from '@/utils/request'

export interface LandingSpec {
  value: string
  sub: string
}

export interface LandingPoint {
  title: string
  text: string
}

export interface LandingCard {
  eyebrow: string
  title_lines: string[]
  desc: string
  chips: string[]
  points: LandingPoint[]
}

export interface LandingShowcaseBlock {
  eyebrow: string
  title: string
  sub: string
}

export interface LandingContent {
  hero: {
    title_lines: string[]
    subs: string[]
    scroll_hint: string
    specs: LandingSpec[]
  }
  partners: {
    eyebrow: string
    names: string[]
  }
  devices: {
    eyebrow: string
    title_lines: string[]
    sub: string
    marquee_clients: string[]
    marquee_specs: string[]
  }
  showcase: {
    cinema: LandingShowcaseBlock
    audio: LandingShowcaseBlock
  }
  essentials: {
    eyebrow: string
    title: string
    sub: string
    cards: LandingCard[]
  }
}

export interface LandingContentResponse {
  config: LandingContent
  /** 是否存过自定义文案（false = 正在用默认文案） */
  has_custom: boolean
}

export const fetchLandingContent = () =>
  get<LandingContentResponse>('/landing/content')

export const updateLandingContent = (data: LandingContent) =>
  put<LandingContentResponse>('/landing/content', data)

export const resetLandingContent = () =>
  del<LandingContentResponse>('/landing/content')
