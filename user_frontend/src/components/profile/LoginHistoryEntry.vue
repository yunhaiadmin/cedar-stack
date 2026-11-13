<template>
  <div class="pc-entry">
    <PcEntryRow
      :icon="History"
      name="登录记录"
      expandable
      :expanded="open"
      @click="toggle"
    />
    <div v-if="open" class="pc-expand">
      <div v-if="lhError" class="sx-err">登录记录加载失败，请稍后重试<button type="button" class="sx-mini-btn" @click="loadLoginHistory(true)">重试</button></div>
      <div v-else-if="lhLoading && lhItems.length === 0" class="sx-loading"><span class="sx-spin" aria-hidden="true"></span>正在加载…</div>
      <div v-else-if="lhItems.length === 0" class="sx-empty">还没有登录记录</div>
      <ul v-else class="lh-list">
        <li v-for="(it, i) in lhItems" :key="i" class="lh-row">
          <span class="sx-status" :class="it.success ? 'sx-status--ok' : 'sx-status--bad'"><span class="sx-dot" aria-hidden="true"></span>{{ it.success ? '成功' : '失败' }}</span>
          <span class="lh-main">
            <span class="lh-line1">{{ it.reason_label }} · {{ fmtTime(it.created_at) }}</span>
            <span class="lh-line2">{{ it.ip || '未知 IP' }}<span v-if="it.region"> · {{ it.region }}</span></span>
            <span class="lh-ua" :title="it.user_agent || ''">{{ it.user_agent || '未知设备' }}</span>
          </span>
        </li>
      </ul>
      <div v-if="!lhError && lhItems.length < lhTotal" class="lh-more">
        <button type="button" class="sx-mini-btn" :disabled="lhLoading" @click="loadLoginHistory(false)">{{ lhLoading ? '正在加载…' : '加载更多' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 登录记录（自旧 AccountSettingsView 原样抽出）：只读、只查本人，
 * limit/offset 分页；首次展开才加载（数据与原页一致）。加载失败留
 * 错误态重试，不静默吞。
 */
import { ref } from 'vue'
import { History } from 'lucide-vue-next'
import PcEntryRow from './PcEntryRow.vue'
import { authApi, type LoginHistoryItem } from '@/api'
import { parseServerTime } from '@/utils/serverTime'

const LH_PAGE = 20

const open = ref(false)
const started = ref(false)
const lhItems = ref<LoginHistoryItem[]>([])
const lhTotal = ref(0)
const lhLoading = ref(false)
const lhError = ref(false)

function toggle() {
  open.value = !open.value
  if (open.value && !started.value) {
    started.value = true
    void loadLoginHistory(true)
  }
}
function fmtTime(iso: string | null): string {
  if (!iso) return '时间未知'
  // 登录记录是 UTC naive 串，须按 UTC 解析再转本地（PA-047）
  const d = parseServerTime(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}
async function loadLoginHistory(reset: boolean) {
  if (lhLoading.value) return
  lhLoading.value = true
  lhError.value = false
  try {
    const data = await authApi.getLoginHistory({
      limit: LH_PAGE,
      offset: reset ? 0 : lhItems.value.length,
    })
    lhTotal.value = data.total
    lhItems.value = reset ? data.items : [...lhItems.value, ...data.items]
  } catch {
    lhError.value = true
  } finally {
    lhLoading.value = false
  }
}
</script>

<style scoped>
/* 展开面板与记录行：状态行模具（说明书 2.6）——状态=圆点+文字、
   行间 1px --uhd-l-divider；加载态=旋转圈+灰字（1.6）、空态=虚线卡（2.7） */
.pc-expand {
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  padding: 14px 2px 18px;
  font-family: var(--uhd-font-sans);
}
.sx-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
.sx-mini-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.sx-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.sx-spin {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-divider);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 0.8s linear infinite;
}
.sx-err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 16px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.sx-empty {
  padding: 26px 20px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  text-align: center;
  font-size: var(--uhd-fs-body);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.sx-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  width: 58px;
  padding-top: 3px;
  font-size: var(--uhd-fs-caption);
  white-space: nowrap;
}
.sx-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.sx-status--ok { color: var(--uhd-success); }
.sx-status--ok .sx-dot { background: var(--uhd-dot-ok); }
.sx-status--bad { color: var(--uhd-danger); }
.sx-status--bad .sx-dot { background: var(--uhd-dot-bad); }
.lh-list { list-style: none; margin: 0; padding: 0; }
.lh-row {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 11px 0; border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
}
.lh-row:first-child { border-top: none; padding-top: 2px; }
.lh-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.lh-line1 { font-size: var(--uhd-fs-caption); color: var(--uhd-l-text); }
.lh-line2 { font-size: var(--uhd-fs-aux); color: var(--uhd-l-text-2); }
.lh-ua {
  font-size: var(--uhd-fs-aux-sm); color: var(--uhd-l-text-3);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;
}
.lh-more { text-align: center; margin-top: 12px; }
</style>
