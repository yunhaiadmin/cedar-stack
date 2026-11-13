<template>
  <!-- 须知卡：各类型同构（原站求片必看 / 反馈须知结构；追新、修正卡按反馈须知同构，推定）。
       内容只取现行真实规则改写成功能文案，不搬原站文案原文。 -->
  <section class="rf-notice" aria-label="须知">
    <h2 class="rf-notice__title">{{ title }}</h2>
    <div class="rf-notice__row rf-notice__row--gray">
      <p class="rf-notice__row-title">{{ grayTitle }}</p>
      <p class="rf-notice__row-body">{{ grayBody }}</p>
    </div>
    <div v-if="kind === 'seek'" class="rf-notice__row rf-notice__row--red">
      <p class="rf-notice__row-title">请先搜索</p>
      <p class="rf-notice__row-body">提交前先搜一下，库里已有的片不用求，直接去看；重名求片会转成附议，不重复建单。</p>
    </div>
    <div v-if="kind === 'seek'" class="rf-notice__grid">
      <div class="rf-notice__cell">
        <p class="rf-notice__cell-title">附议越多，处理优先级越高</p>
        <p class="rf-notice__cell-body">大家都在求的片，会先处理。</p>
      </div>
      <div class="rf-notice__cell">
        <p class="rf-notice__cell-title">选季提交更准确</p>
        <p class="rf-notice__cell-body">剧集可以按季提交，只求缺的那几季。</p>
      </div>
      <div class="rf-notice__cell">
        <p class="rf-notice__cell-title">原名提交需人工确认</p>
        <p class="rf-notice__cell-body">搜不到对应条目时按片名提交，会人工核对。</p>
      </div>
      <div class="rf-notice__cell">
        <p class="rf-notice__cell-title">进度在「我的请求」查看</p>
        <p class="rf-notice__cell-body">提交后可以在「我的请求」页签看处理状态。</p>
      </div>
    </div>
    <div v-else class="rf-notice__tall">
      <div v-for="cell in tallCells" :key="cell.title" class="rf-notice__cell rf-notice__cell--tall">
        <p class="rf-notice__cell-title">{{ cell.title }}</p>
        <p class="rf-notice__cell-body">{{ cell.body }}</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  kind: 'seek' | 'chase' | 'correction' | 'feedback'
  /** 追新分电影/剧集（kind='chase' 时用），缺省按剧集 */
  media?: 'movie' | 'series'
  /** 本月求片剩余额度（getMyRequests 的 monthly_remaining；拿不到为 null） */
  monthlyRemaining?: number | null
}>()

const title = computed(() =>
  props.kind === 'seek'
    ? '求片必看'
    : props.kind === 'chase'
      ? '追新须知'
      : props.kind === 'correction'
        ? '修正须知'
        : '反馈须知',
)
const grayTitle = computed(() =>
  props.kind === 'seek'
    ? '额度说明'
    : props.kind === 'chase'
      ? '追新是什么'
      : props.kind === 'correction'
        ? '什么算修正'
        : '提交前先确认',
)
const grayBody = computed(() => {
  if (props.kind === 'seek') {
    return props.monthlyRemaining == null
      ? '每月有固定求片次数，额度用完请下月再提交。'
      : `每月有固定求片次数，本月还剩 ${props.monthlyRemaining} 次，额度用完请下月再提交。`
  }
  if (props.kind === 'chase') {
    return props.media === 'movie'
      ? '电影追新针对已上映影片的续集、加长版等新版本；想看还没上架的片，请走求片提交。'
      : '追新针对已在库的剧集；想看还没上架的片，请走求片提交。'
  }
  if (props.kind === 'correction') return '片名、年份、季集、海报、简介这些资料标错了，用修正信息；播放不了、账号订阅有问题，请走其他反馈。'
  return '提交前先选对问题类型，描述写清片名、集数和具体情况，处理更快。'
})
const tallCells = computed(() => {
  if (props.kind === 'chase') {
    if (props.media === 'movie') {
      return [
        { title: '写清片名', body: '一句话标题写片名，方便快速找到这部片。' },
        { title: '写清想追什么', body: '续集、加长版、导演剪辑版，想要哪个写清楚。' },
        { title: '管理员会回复', body: '处理进度和回复都在「我的请求」里查看。' },
        { title: '关闭后不能再回复', body: '确认问题解决后再关闭记录。' },
      ]
    }
    return [
      { title: '写清剧名', body: '一句话标题写剧名，方便快速找到这部剧。' },
      { title: '写清季与集', body: '想追第几季第几集，在说明里写清楚。' },
      { title: '管理员会回复', body: '处理进度和回复都在「我的请求」里查看。' },
      { title: '关闭后不能再回复', body: '确认问题解决后再关闭记录。' },
    ]
  }
  if (props.kind === 'correction') {
    return [
      { title: '写清是哪部片', body: '片名、年份、第几季第几集，方便快速定位。' },
      { title: '写清错在哪', body: '现在标的是什么、正确的应该是什么，写在说明里。' },
      { title: '管理员会回复', body: '处理进度和回复都在「我的请求」里查看。' },
      { title: '关闭后不能再回复', body: '确认问题解决后再关闭记录。' },
    ]
  }
  return [
    { title: '写清位置', body: '片名、集数、发生时间，方便定位问题。' },
    { title: '不要贴密码', body: '账号问题只描述现象，不要在描述里写密码。' },
    { title: '管理员会回复', body: '处理进度和回复都在「我的请求」里查看。' },
    { title: '关闭后不能再回复', body: '确认问题解决后再关闭记录。' },
  ]
})
</script>

<style scoped>
/* 须知卡：缺省深色（原站 §6.4），html[data-theme='light'] 时换浅（§6.2）。
   数值来源：实测清单 §10（卡圆角约 20、灰行高约 80、小格高约 70 圆角约 12、
   反馈变体 4 个高格约 210px 高；标题 14–15px/600–700、行标题 13px/700）。 */
.rf-notice {
  background: #17181a;
  border-radius: 20px;
  padding: 18px 16px;
}
.rf-notice__title { margin: 0 0 12px; font-size: 15px; font-weight: 700; color: #fbfcfd; }
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
.rf-notice__tall {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
.rf-notice__cell-title { margin: 0 0 4px; font-size: 13px; font-weight: 700; color: #fbfcfd; }
.rf-notice__cell-body { margin: 0; font-size: 13px; line-height: 1.6; color: #9ca3af; }
@media (max-width: 768px) {
  .rf-notice__tall { grid-template-columns: 1fr 1fr; }
  .rf-notice__cell--tall { min-height: 150px; }
}
html[data-theme='light'] .rf-notice { background: #ffffff; }
html[data-theme='light'] .rf-notice__title { color: #111111; }
html[data-theme='light'] .rf-notice__row--gray { background: #ededed; }
html[data-theme='light'] .rf-notice__row--red { background: #fcf2f3; }
html[data-theme='light'] .rf-notice__row--red .rf-notice__row-title { color: #b91c1c; }
html[data-theme='light'] .rf-notice__row-title { color: #111111; }
html[data-theme='light'] .rf-notice__row-body { color: #6b7280; }
html[data-theme='light'] .rf-notice__cell { background: #f9f9f9; border-color: transparent; }
html[data-theme='light'] .rf-notice__cell-title { color: #111111; }
html[data-theme='light'] .rf-notice__cell-body { color: #6b7280; }
</style>
