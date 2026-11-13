<template>
  <!-- 须知卡组：每个类型一套（求片必看 / 追新须知 / 反馈须知），结构照 UHDNow
       原站（实测清单 §10＋2026-11-13 用户真机截图）：ⓘ 标题行＋宽行卡堆叠＋
       小格网格；内容是结构化数据（NOTICE_GROUPS）渲染，文案为我方口径自写，
       只对齐规则话题、不搬原站原文。 -->
  <section class="rf-notice" aria-label="须知">
    <h2 class="rf-notice__title">
      <span class="rf-notice__info" aria-hidden="true">i</span>{{ group.heading }}
    </h2>
    <div
      v-for="card in wideCards"
      :key="card.title"
      class="rf-notice__row"
      :class="'rf-notice__row--' + card.tone"
    >
      <p class="rf-notice__row-title">{{ card.title }}</p>
      <p v-if="card.body" class="rf-notice__row-body">{{ card.body }}</p>
    </div>
    <div v-if="cellCards.length" class="rf-notice__grid">
      <div
        v-for="card in cellCards"
        :key="card.title"
        class="rf-notice__cell"
        :class="{ 'rf-notice__cell--tall': card.tall }"
      >
        <p class="rf-notice__cell-title">{{ card.title }}</p>
        <p v-if="card.body" class="rf-notice__cell-body">{{ card.body }}</p>
        <p v-for="s in card.sections || []" :key="s.label" class="rf-notice__sec">
          <b>{{ s.label }}：</b>{{ s.text }}
        </p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface NoticeSection {
  label: string
  text: string
}
export interface NoticeCard {
  /** gray＝灰底宽行/强调，red＝红粉警示，plain＝普通小格 */
  tone: 'gray' | 'red' | 'plain'
  /** true＝整宽行卡（堆叠在小格网格之上） */
  wide?: boolean
  /** true＝高格（反馈须知的内容卡，带小节） */
  tall?: boolean
  title: string
  body?: string
  sections?: NoticeSection[]
}
export interface NoticeGroup {
  heading: string
  cards: NoticeCard[]
}

/* 须知内容（结构化数据）：话题与原站对齐，文字全部我方自写。
   红色警示卡的「无法受理」清单按我方口径：只写违法违规，不照搬原站清单。 */
const NOTICE_GROUPS: Record<'seek' | 'chase' | 'feedback', NoticeGroup> = {
  seek: {
    heading: '求片必看',
    cards: [
      {
        tone: 'gray', wide: true,
        title: '提交求片不代表一定入库',
        body: '把想看的片告诉我们，我们会结合站内收录安排和资源是否可得逐条评估；没法保证每一条都会上架，提交前先想清楚是不是真的想看。',
      },
      {
        tone: 'red', wide: true,
        title: '这些内容无法受理',
        body: '违法违规内容请不要提交，这类请求会被直接驳回；反复提交违规内容的，会被取消求片资格。',
      },
      {
        tone: 'plain',
        title: '请先检查片库',
        body: '先在片库里搜一遍：已经有的片直接看，不用再求；重复提交不会更快。',
      },
      {
        tone: 'plain',
        title: '处理时间',
        body: '热门又好找的片处理得快；冷门片源难找，时间没法保证，请不要重复催促。',
      },
      {
        tone: 'plain',
        title: '字幕需要等待',
        body: '新片刚出时常没有中文字幕，要等字幕组或 AI 字幕补上，入库可能会晚一些。',
      },
      {
        tone: 'plain',
        title: '数据以 TMDB 为准',
        body: '片名、译名和分类都以 TMDB 数据为准，提交后不能自己修改；标错了请走修正信息。',
      },
    ],
  },
  chase: {
    heading: '追新须知',
    cards: [
      {
        tone: 'gray', wide: true,
        title: '先确认缺集，再提交追新',
        body: '先到剧集详情里确认对应的季和集确实还没入库，再提交追新。剧集已经播出，不代表资源已经发布，没确认就提交是处理不了的。',
      },
      {
        tone: 'plain',
        title: '写明具体季和集',
        body: '在说明里把季和集写清楚，建议用 S02E06 这样的格式；信息不明确的单没法往下处理。',
      },
      {
        tone: 'plain',
        title: '热播剧等待 24 小时',
        body: '从这一集正式播出起先等满 24 小时，超过一天还没入库，再来提交追新。',
      },
      {
        tone: 'plain',
        title: '不要重复提交',
        body: '同一部剧已有未完成的追新时，去看那一条的处理进度就好，不要再提一条相同的。',
      },
      {
        tone: 'plain',
        title: '数据以 TMDB 为准',
        body: '季和集的划分以 TMDB 数据为准，提交时请一次把信息写完整。',
      },
    ],
  },
  feedback: {
    heading: '反馈须知',
    cards: [
      {
        tone: 'gray', wide: true,
        title: '先确认问题来自资源本身',
        body: '提交前请先在其他客户端或设备上试着复现，并排除自家网络、播放设置和设备解码能力的问题；情况写得越完整，定位得越快。',
      },
      {
        tone: 'plain', tall: true,
        title: '先排查播放兼容性',
        sections: [
          {
            label: '设备与客户端',
            text: '杜比视界、HDR、全景声和特殊字幕格式会受设备与客户端的支持限制，先把客户端更新到最新版，并尽量换一台设备确认。',
          },
          {
            label: '卡顿与加载',
            text: '先测一下网络，再换其他影片试试；只有同一资源能稳定复现问题时，才按资源问题提交。',
          },
        ],
      },
      {
        tone: 'plain', tall: true,
        title: '字幕与音频问题',
        sections: [
          {
            label: '新片字幕',
            text: '没有官方中文字幕的新片，要等字幕组或 AI 字幕，这类情况不属于资源故障。',
          },
          {
            label: '繁体字幕',
            text: '繁体字幕按资源自带的字幕提供，不单独要求转换。',
          },
          {
            label: '字幕与音轨异常',
            text: '字幕对不上轴、没有声音、爆音这类问题，请写清出现在哪一集、大概哪个时间点。',
          },
        ],
      },
    ],
  },
}

const props = defineProps<{
  kind: 'seek' | 'chase' | 'feedback'
}>()

const group = computed(() => NOTICE_GROUPS[props.kind])
const wideCards = computed(() => group.value.cards.filter((c) => c.wide))
const cellCards = computed(() => group.value.cards.filter((c) => !c.wide))
</script>

<style scoped>
/* 须知卡：缺省深色（原站 §6.4），html[data-theme='light'] 时换浅（§6.2）。
   数值来源：实测清单 §10（卡圆角约 20、灰行高约 80、小格高约 70 圆角约 12、
   反馈变体高格约 210px；标题 14–15px/600–700、行标题 13px/700）＋真机截图
   （移动端小格单列堆叠、桌面 2 列）。 */
.rf-notice {
  background: #17181a;
  border-radius: 20px;
  padding: 18px 16px;
}
.rf-notice__title {
  margin: 0 0 12px; font-size: 15px; font-weight: 700; color: #fbfcfd;
  display: flex; align-items: center; gap: 8px;
}
.rf-notice__info {
  flex: none; width: 20px; height: 20px; border-radius: 50%;
  border: 1.5px solid #55575c; color: #d4d4d8;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; font-style: italic; line-height: 1;
}
.rf-notice__row {
  border-radius: 12px;
  padding: 12px 14px;
  min-height: 80px;
  margin-bottom: 10px;
}
.rf-notice__row--gray { background: #323335; }
.rf-notice__row--red { background: rgba(252, 242, 243, 0.08); }
.rf-notice__row-title { margin: 0 0 4px; font-size: 13px; font-weight: 700; color: #fbfcfd; }
.rf-notice__row--red .rf-notice__row-title { color: #fca5a5; }
.rf-notice__row-body { margin: 0; font-size: 13px; line-height: 1.6; color: #9ca3af; }
.rf-notice__row--red .rf-notice__row-body { color: #d4d4d8; }
.rf-notice__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.rf-notice__cell {
  background: #1e1f21;
  border: 1px solid #1c1d1f;
  border-radius: 12px;
  padding: 12px 14px;
  min-height: 70px;
}
.rf-notice__cell--tall { min-height: 210px; }
.rf-notice__cell-title { margin: 0 0 4px; font-size: 13px; font-weight: 700; color: #fbfcfd; }
.rf-notice__cell-body { margin: 0; font-size: 13px; line-height: 1.6; color: #9ca3af; }
.rf-notice__sec { margin: 0 0 8px; font-size: 13px; line-height: 1.65; color: #9ca3af; }
.rf-notice__sec:last-child { margin-bottom: 0; }
.rf-notice__sec b { color: #fbfcfd; font-weight: 700; }
@media (max-width: 768px) {
  .rf-notice__grid { grid-template-columns: 1fr; }
  .rf-notice__cell--tall { min-height: 0; }
}
html[data-theme='light'] .rf-notice { background: #ffffff; }
html[data-theme='light'] .rf-notice__title { color: #111111; }
html[data-theme='light'] .rf-notice__info { border-color: #d4d4d8; color: #55575c; }
html[data-theme='light'] .rf-notice__row--gray { background: #ededed; }
html[data-theme='light'] .rf-notice__row--red { background: #fcf2f3; }
html[data-theme='light'] .rf-notice__row--red .rf-notice__row-title { color: #b91c1c; }
html[data-theme='light'] .rf-notice__row-title { color: #111111; }
html[data-theme='light'] .rf-notice__row-body { color: #6b7280; }
html[data-theme='light'] .rf-notice__row--red .rf-notice__row-body { color: #55575c; }
html[data-theme='light'] .rf-notice__cell { background: #f9f9f9; border-color: transparent; }
html[data-theme='light'] .rf-notice__cell-title { color: #111111; }
html[data-theme='light'] .rf-notice__cell-body { color: #6b7280; }
html[data-theme='light'] .rf-notice__sec { color: #6b7280; }
html[data-theme='light'] .rf-notice__sec b { color: #111111; }
</style>
