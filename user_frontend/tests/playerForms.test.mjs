// 播放面形态契约测试（2026-11-18 参考站 逐帧盘点差异批·播放面形态组）：
// 运行：cd user_frontend && node tests/playerForms.test.mjs
// 覆盖两处：
//  A) 选集/选版本弹窗（PlayerSchemeSheet）加载失败态 G-155：
//     拉取失败不再被 allSettled 静默吞掉，须有「Load failed」＋「重新获取」重试钮。
//  B) 一起看面板（PlayerTogetherSheet）形态组 G-187/188/190/191/200/202/203/204/206/209：
//     标题/副行/主钮/开启中态/已进房叠层/复制钮与反馈/成员标签/房主皇冠/结束钮/删房间码行。
// 惯例同 tests/detailLoadRace.test.mjs：node 直接运行的源码契约检查，
// 因为这些形态活在 .vue 里，无法用纯模块单测直接驱动。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const scheme = readFileSync(join(ROOT, 'src/components/panel/PlayerSchemeSheet.vue'), 'utf8')
const together = readFileSync(join(ROOT, 'src/components/player/PlayerTogetherSheet.vue'), 'utf8')

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}

/* ---------- A) G-155 选集弹窗失败态 ---------- */
check('A1 loadFailed 状态声明在位', scheme.includes('const loadFailed = ref(false)'))
check(
  'A2 refresh() 以条目拉取 rejected 落定 loadFailed',
  scheme.includes("loadFailed.value = itemResult.status === 'rejected'"),
)
check('A3 refresh() 入口复位 loadFailed', scheme.includes('loadFailed.value = false'))
check('A4 失败态文案「Load failed」在位', scheme.includes('Load failed'))
check('A5 重试钮文案「重新获取」在位', scheme.includes('重新获取'))
check(
  'A6 重试钮点击重跑 refresh',
  /class="psp-failed__retry"[^>]*@click="refresh"/.test(scheme) ||
    /@click="refresh"[^>]*class="psp-failed__retry"/.test(scheme),
)
check(
  'A7 失败态分支挂在 loading 之后（v-else-if）',
  scheme.includes('v-else-if="loadFailed"'),
)
check(
  'A8 固定区在失败态不渲染',
  scheme.includes('v-if="!loading && !loadFailed && !needsBinding"'),
)

/* ---------- B) 一起看面板形态组 ---------- */
check('B1 邀请态标题「邀请朋友一起看」', together.includes('邀请朋友一起看'))
check('B2 副行「最多 10 人，房主控制播放」', together.includes('最多 10 人，房主控制播放'))
check('B3 旧说明长文已删', !together.includes('开一个一起看房间，把链接发给朋友'))
check('B4 主钮文案「开启同看」', together.includes('开启同看'))
check('B5 旧主钮文案「创建一起看」已删', !together.includes('创建一起看'))
check('B6 开启中态文案「正在开启...」', together.includes('正在开启...'))
check('B7 旧开启中文案「正在创建…」已删', !together.includes('正在创建…'))
check(
  'B8 开启中扇形旋转光效（conic-gradient＋旋转动画）',
  together.includes('conic-gradient') && /@keyframes\s+wt-/.test(together),
)
check('B9 已进房内容有独立叠层卡（wt-room-card）', together.includes('wt-room-card'))
check('B10 复制钮文案「复制邀请链接」', together.includes('复制邀请链接'))
check('B11 复制反馈文案「已复制链接」', together.includes('已复制链接'))
check('B12 「邀请链接」小节标题行已删', !together.includes('wp-sec">邀请链接'))
check('B13 房间码行已删', !together.includes('房间码'))
check('B14 旧链接框已删', !together.includes('wt-link-box'))
check('B15 成员标签无人数括号', !together.includes('成员（'))
check('B16 成员标签「成员」在位', together.includes('wp-sec">成员</p>'))
check('B17 房主标识改皇冠图标', together.includes('Crown') && together.includes('wt-crown'))
check('B18 旧金色「房主」胶囊徽标已删', !together.includes('wt-host-badge'))
check('B19 房主结束钮文案「结束同看」', together.includes('结束同看'))
check('B20 旧结束钮文案「结束一起看」已删', !together.includes('结束一起看'))
check('B21 非房主「退出一起看」保留（友站未覆盖分支不动）', together.includes('退出一起看'))

console.log(`\n${passed} passed`)
