<template>
  <!-- 充值等待屏：与表单态共用同一张底部浮卡（同卡片内切换，不是新弹窗）。
       纯展示组件：轮询/状态机在 TopupSheet 里，这里只按 phase 画三种态。
       数值照友站充值录屏逐帧量测（~/workspace/tmp/uhd-topup-frame-study.md
       「等待屏」一节，并经本单对帧图 f070 的逐行剖面复核修正）：
       银行灰圆 Ø40、标题 17.5/700、金额 27/800、红胶囊高 30（字 11.5）、
       二维码卡 242 见方（码区 192）、「稍后处理」钮高 50。 -->
  <div class="tw-wrap">
    <div class="tw-head">
      <button type="button" class="tw-close" aria-label="关闭" @click="emit('close')">
        <X :size="20" />
      </button>
    </div>

    <!-- 等待付款态 -->
    <template v-if="phase === 'waiting'">
      <div class="tw-bank" aria-hidden="true"><Landmark :size="22" /></div>
      <h3 class="tw-title">安全充值</h3>
      <p class="tw-amount"><span class="tw-amount__yen">¥</span> {{ amountCny }}</p>
      <p class="tw-note">请在新窗口完成付款，支付成功后余额会自动到账。</p>
      <div class="tw-deadline"><Clock :size="12" /> 请在 10 分钟之内完成付款</div>
      <div class="tw-qr-card">
        <canvas ref="qrCanvas" class="tw-qr" aria-label="支付二维码" />
      </div>
      <button type="button" class="tw-defer" @click="emit('close')">稍后处理</button>
    </template>

    <!-- 到账成功态 -->
    <template v-else-if="phase === 'paid'">
      <div class="tw-bank tw-bank--ok" aria-hidden="true"><Check :size="22" /></div>
      <h3 class="tw-title">充值成功</h3>
      <p class="tw-amount"><span class="tw-amount__yen">$</span> {{ creditUsd }}</p>
      <p class="tw-note">已到账，余额已更新。</p>
      <button type="button" class="tw-defer" @click="emit('close')">完成</button>
    </template>

    <!-- 失败态：failed（支付失败）/ expired（订单过期）/ timeout（10 分钟未到账） -->
    <template v-else>
      <div class="tw-bank tw-bank--bad" aria-hidden="true"><CircleAlert :size="22" /></div>
      <h3 class="tw-title">{{ failTitle }}</h3>
      <p class="tw-note">{{ failNote }}</p>
      <button type="button" class="btn-primary tw-retry" @click="emit('retry')">重新充值</button>
      <button type="button" class="tw-defer" @click="emit('close')">稍后处理</button>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Check, CircleAlert, Clock, Landmark, X } from 'lucide-vue-next'

const props = defineProps<{
  phase: 'waiting' | 'paid' | 'failed'
  /** waiting/failed 才有意义：'failed' 支付失败 / 'expired' 订单过期 / 'timeout' 10 分钟未到账 */
  failKind: 'failed' | 'expired' | 'timeout'
  /** 应付人民币（两位小数串，下单返回的权威值） */
  amountCny: string
  /** 到账美元（两位小数串，paid 态用） */
  creditUsd: string
  /** 支付跳转地址：二维码内容，只含 pay_url，不含任何令牌或用户信息 */
  payUrl: string
}>()

const emit = defineEmits<{
  close: []
  retry: []
}>()

const qrCanvas = ref<HTMLCanvasElement | null>(null)

const failTitle = computed(() =>
  props.failKind === 'expired' ? '订单已过期' : props.failKind === 'timeout' ? '还在等付款' : '支付失败')

/** 人话失败原因：只按订单状态机写实，不编造「已扣款会退回」这类未核实承诺。
 * 后端不对 pending 充值单做定时过期（查无过期清理逻辑），所以 timeout 态不许
 * 写「订单已失效」——用户在网关页晚一点付完，回调仍会入账。 */
const failNote = computed(() =>
  props.failKind === 'expired'
    ? '这笔订单已超时失效，请重新下单。'
    : props.failKind === 'timeout'
      ? '10 分钟内没有收到付款通知。如果你已经付过款，余额稍后会自动到账；也可以重新下单。'
      : '这笔订单没有完成支付，请重新下单。')

/** 二维码：仓内已有 qrcode ^1.5.3，照 TotpPanel 的动态导入写法
 * （避开 @types/qrcode 的 node 类型污染）；QR 本体橙 #EE7035（帧取样），
 * 画 452px、CSS 缩到 226px，视网膜屏也清晰。 */
async function renderQr() {
  const canvas = qrCanvas.value
  if (!canvas || !props.payUrl) return
  try {
    const QRCode = (await import('qrcode/lib/browser.js')).default
    await QRCode.toCanvas(canvas, props.payUrl, {
      width: 384,
      margin: 1,
      color: { dark: '#EE7035', light: '#ffffff' },
    })
    // toCanvas 会把 width 写进 canvas 行内 style（384px），会撑破 242 的卡：
    // 清掉行内尺寸、让 CSS 的 192px 接管显示尺寸，位图仍是 384px、视网膜屏清晰
    canvas.style.width = ''
    canvas.style.height = ''
  } catch {
    /* 二维码画不出来也不阻塞等待：新窗口支付页照常能付 */
  }
}

onMounted(() => {
  if (props.phase === 'waiting') void nextTick(renderQr)
})

watch(
  () => props.payUrl,
  () => {
    if (props.phase === 'waiting') void nextTick(renderQr)
  },
)
</script>

<style scoped>
.tw-wrap { text-align: center; }
.tw-head { display: flex; justify-content: flex-end; }
.tw-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: var(--tq-dim);
  cursor: pointer;
}
/* 银行图标灰圆 Ø40（帧值 #E7E4E7，深色下同样用这块浅芯片，图标可读） */
.tw-bank {
  width: 40px;
  height: 40px;
  margin: 4px auto 0;
  border-radius: 50%;
  background: #e7e4e7;
  color: #6b6c70;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tw-bank--ok { background: rgba(34, 197, 94, 0.18); color: #4ade80; }
html[data-theme='light'] .tw-bank--ok { background: #dcf2e3; color: #15803d; }
.tw-bank--bad { background: rgba(192, 57, 43, 0.18); color: #f87171; }
html[data-theme='light'] .tw-bank--bad { background: #f1e4ed; color: #c0392b; }

.tw-title { margin: 10px 0 0; font-size: 17.5px; font-weight: 700; }
/* 金额 27/800，¥ 小一号（帧口径） */
.tw-amount { margin: 6px 0 0; font-size: 27px; font-weight: 800; line-height: 1.2; }
.tw-amount__yen { font-size: 19px; font-weight: 700; }
.tw-note { margin: 8px 0 0; font-size: 12px; line-height: 1.6; color: var(--tq-dim); }
/* 红胶囊：高 30、底 #F1E4ED、深红字（帧值约 #C0392B 区段）；字号 11.5
   是对帧图的逐行剖面实测（胶囊字形高 21 帧 px ≈ 11.4pt），不是 13px */
.tw-deadline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  margin-top: 12px;
  border-radius: 999px;
  background: #f1e4ed;
  color: #c0392b;
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
}

/* 二维码卡：242 见方、白底、边 #E8E5E8、圆角 20（帧图外框剖面实测约
   446 帧 px ≈ 242pt；帧研究里的 262pt 量的是含阴影的外延）；码区 192，
   对应帧图码区 332 帧 px ≈ 180pt（含 margin:1 的一圈白边） */
.tw-qr-card {
  width: 242px;
  height: 242px;
  margin: 16px auto 0;
  padding: 25px;
  box-sizing: border-box;
  background: #ffffff;
  border: 1px solid #e8e5e8;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.tw-qr { width: 192px; height: 192px; display: block; }
/* 「稍后处理」/「完成」：白底细边全圆角胶囊，高 50、15px（帧口径）；
   深色下走令牌对应面（卡面＋边），浅色按帧值白底黑字 */
.tw-defer {
  width: 100%;
  height: 50px;
  margin-top: 16px;
  border-radius: 25px;
  border: 1px solid var(--tq-border);
  background: var(--tq-card);
  color: inherit;
  font-family: inherit;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}
html[data-theme='light'] .tw-defer { background: #ffffff; color: #111111; }
.tw-retry { width: 100%; height: 50px; margin-top: 20px; border-radius: 25px; font-size: 15.5px; font-weight: 700; }
.tw-retry + .tw-defer { margin-top: 12px; }
</style>
