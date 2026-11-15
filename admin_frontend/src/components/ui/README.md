# 管理端共享原语（暗房影院）

管理端从 v2.54 起与用户端共用「暗房影院」设计系统：暖黑底 · 实色表面 · 放映机琥珀单一强调色 ·
衬线标题 · 发丝线边框；白日模式是奶油纸色。本目录是**改造其它页面时唯一应该用的积木**，
`views/Dashboard.vue` 是参考实现——改页面前先读它。

```ts
import { PageHeader, SectionCard, StatTile, EmptyState } from '@/components/ui'
```

> 这几个组件也会被 unplugin-vue-components 自动注册（`src/components/**`），
> 但请**显式 import**：读代码的人一眼知道它们从哪来。

---

## 1. 令牌：只用 `--au-*`

| 用途 | 令牌 |
| --- | --- |
| 画布 / 次级画布 | `--au-bg` / `--au-bg-soft` |
| 卡片 / 抬起一层 / 再抬一层 | `--au-surface` / `--au-surface-2` / `--au-surface-3` |
| 正文 / 次级 / 说明 / 占位与禁用 | `--au-text` / `--au-text-2` / `--au-text-3` / `--au-text-4` |
| 发丝线 / 加重线 / 焦点描边 | `--au-border` / `--au-border-strong` / `--au-border-focus` |
| 琥珀主色 / 悬停压深 / 更深 | `--au-primary` / `--au-primary-strong` / `--au-primary-deep` |
| 琥珀浅底 / 中间档 / 描边 | `--au-primary-soft` / `--au-primary-mid` / `--au-primary-border` |
| 琥珀底上的字 | `--au-on-primary` |
| 语义色（各带 `-soft` 底、`-border` 描边） | `--au-success` / `--au-warning` / `--au-danger` / `--au-info` |
| 中性悬停底 | `--au-violet-soft`（名字是历史遗留，值是中性暖灰） |
| 输入框底 / 聚焦底 / 描边 | `--au-input-bg` / `--au-input-bg-focus` / `--au-input-border` |
| 遮罩 / 浮层底 | `--au-scrim` / `--au-overlay-menu` |
| 圆角 | `--au-r-sm` 8 · `--au-r-md` 10（控件）· `--au-r-lg` 14（卡片）· `--au-r-full` |
| 阴影 | `--au-shadow-1`（几乎不用）/ `--au-shadow-2`（只给浮层：弹窗、下拉、抽屉） |
| 字体 | `--au-font-serif`（标题）/ `--au-font-sans`（正文）；等宽仍是 `--font-mono` |
| 动效 | `--au-ease` · `--au-fast` .16s · `--au-med` .26s |

- 定义在 `src/styles/aurora.css`，与 `user_frontend/src/styles/aurora.css` **同名同值**；改值先改用户端。
- `src/styles/tokens.css` 现在只是**别名层**：`--bg-card`、`--primary`、`--text-muted`、`--radius-lg`…
  全部指向 `--au-*`，老页面零改动就换了肤。**新代码不要再用这些旧名字。**
- 深浅主题只靠 `html[data-theme='light']` 切换（`composables/useTheme.ts`），
  **不要**在页面里写 `html[data-theme='light'] .xxx { … }` 分叉——用令牌就自动跟着切。
- 品牌主题色由 `composables/branding.ts` 注入覆盖 `--au-primary` 一族，所以**别写死琥珀色值**。

## 2. 设计规则（与用户端一致）

1. **一支强调色**：只有琥珀。不要青色、紫色、渐变、发光阴影（`--gradient-brand` 现在就是纯琥珀）。
2. **数字走正文色**：颜色只用来提示「有没有要看的」（警告 / 危险），不做装饰；`tone="accent"` 一页最多一两处。
3. **分层靠发丝线，不靠阴影**：卡片 = `--au-surface` + `1px --au-border` + `--au-r-lg`，无 box-shadow。
4. **标题走衬线**：`h1/h2/h3` 已全局衬线（`base.css`）；写成 div 的标题加 `.au-serif`。正文保持无衬线。
5. **小眉题**：分组名 / 页面归属用 `.au-eyebrow`（琥珀、11–12px、字距 .1em）。
6. **焦点**：`outline: 2px solid var(--au-border-focus)` 或输入框的 `0 0 0 3px var(--au-primary-soft)` 光环。
7. **动效**：只淡入（透明度），不上浮、不缩放（按下 `scale(.98)` 除外）。

## 3. 组件

### `PageHeader` — 页头

```vue
<PageHeader eyebrow="运营中心" title="订单" description="按支付状态筛选；点订单号看明细与退款。">
  <template #actions>
    <el-button @click="exportCsv">导出</el-button>
    <el-button type="primary" @click="create">新建订单</el-button>
  </template>
</PageHeader>
```

| prop | 类型 | 说明 |
| --- | --- | --- |
| `title` | string（必填） | 衬线大标题 |
| `eyebrow` | string | 标题上方的琥珀眉题，通常写侧边栏分组名 |
| `description` | string | 一两行说明：这页做什么 / 数据口径 |
| `as` | `'h1' \| 'h2' \| 'h3'` | 默认 `h2`（顶栏已有 h1） |

插槽：`actions`（右侧操作区，窄屏自动换行到下方）、`description`（富文本说明）、`icon`（标题前图标）。

> 顶栏已经显示路由 `meta.title`。页头标题可以更具体（「经营驾驶舱」），也可以与之相同；
> 不需要页头的工具型页面（日志表格）可以不放，直接从 `SectionCard` 开始。

### `SectionCard` — 区块卡片（替代 `.admin-card` + `.card-header`）

```vue
<SectionCard title="趋势" :icon="TrendingUp" meta="近 14 天">
  <template #actions><el-radio-group v-model="days">…</el-radio-group></template>
  …内容…
  <template #footer>数据每 5 分钟刷新</template>
</SectionCard>

<!-- 里面直接放表格：flush 去掉内容区内边距 -->
<SectionCard title="全部订单" flush>
  <el-table :data="rows">…</el-table>
</SectionCard>
```

| prop | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 衬线标题；不传且没有 `actions` 插槽就不渲染标题行 |
| `meta` | string | 标题后的灰色小字（范围、计数） |
| `icon` | lucide 组件 | 标题前琥珀图标，传组件本身：`:icon="TrendingUp"` |
| `description` | string | 标题下一行说明 |
| `tone` | `'default' \| 'accent'` | `accent` = 琥珀发丝描边（需要注意的块） |
| `flush` | boolean | 内容区不留内边距（表格 / 贴边列表） |
| `as` | string | 根元素标签，默认 `section` |

插槽：默认、`title`（富标题）、`actions`、`footer`。多个 SectionCard 之间的间距交给父级
`display:flex; gap:16px`（`.admin-page` 已经是 `gap: 16px`），**不要**给卡片写 margin。

### `StatTile` — 数据瓦片（替代 `.stat-tile` / `.kpi-card`）

```vue
<StatTile label="待处理工单" :value="12" :icon="Ticket" tone="danger"
          to="/tickets?status=open" hint="点开只看进行中" layout="icon-left" />
<StatTile label="兑换码" :value="used" :suffix="`/ ${total}`" hint="已核销 / 已生成" to="/exchange-codes" />
```

| prop | 类型 | 说明 |
| --- | --- | --- |
| `label` | string（必填） | 小标签 |
| `value` | string \| number（必填） | 大数字（等宽数字） |
| `suffix` | string | 数字后的小字（单位 / 分母） |
| `hint` | string | 脚注，允许换行 |
| `icon` | lucide 组件 | `stack` 布局放在标签前；`icon-left` 放左侧方块 |
| `tone` | `plain \| ok \| info \| warn \| danger \| accent` | 见设计规则 2；warn/danger 会染数字并加重描边 |
| `to` | RouteLocationRaw | 有就渲染成 RouterLink（整块可点，带筛选深链） |
| `layout` | `'stack' \| 'icon-left'` | 默认 `stack`；页面顶部 KPI 用 `icon-left` |
| `title` | string | 悬停提示 |

插槽：`value`（自定义数字区）、`hint`、`label-extra`（标签后挂徽章，如 `<span class="au-badge au-badge-amber">当前</span>`）。
网格用 `display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px`。

### `EmptyState` — 空状态（替代 `.empty-hint` / `.empty-state` / `el-empty`）

```vue
<EmptyState :icon="Inbox" title="还没有工单" description="用户提交的工单会出现在这里。">
  <template #actions><el-button @click="reload">刷新</el-button></template>
</EmptyState>
<EmptyState compact title="暂无播放数据" />   <!-- 卡片 / 表格里 -->
```

| prop | 类型 | 说明 |
| --- | --- | --- |
| `title` | string（必填） | 衬线一句话 |
| `description` | string | 补充说明 / 下一步 |
| `icon` | lucide 组件 | 圆形淡色图标 |
| `compact` | boolean | 嵌在卡片里用，上下留白收小 |

表格空态：`<el-table>` 的 `#empty` 插槽里放 `<EmptyState compact … />`。

## 4. 全局原子类（`styles/aurora.css`，与用户端同名）

`.au-btn` + `.au-btn-primary | .au-btn-ghost | .au-btn-danger` + `.au-btn-sm` ——
给 RouterLink / `<a>` 做按钮外观（表单动作仍用 `el-button`）。
`.au-badge` + `.au-badge-amber | -green | -rose | -info | -muted` —— 行内状态徽章（与 `el-tag` 同配方）。
`.au-card`、`.au-eyebrow`、`.au-serif`、`.au-num`、`.au-skeleton`、`.au-anim-up`。

## 5. Element Plus

`styles/element-plus-theme.css` 已把 button / input / select / table / dialog / drawer / tabs / tag /
pagination / form / message / notification / dropdown / menu / switch / date picker / card …
全部映射到 `--au-*`。页面里**不要再覆盖 EP 的颜色**；只在需要时调尺寸 / 布局。
语义按钮（success / warning / danger / info）是「浅底 + 同色描边 + 同色字」，只有 `type="primary"` 是实心琥珀——
一个区域只放一个 primary。

## 6. 改造一个页面的清单

1. 顶部换成 `PageHeader`（眉题写侧边栏分组名，见 `views/Layout.vue` 的 `navGroups`）。
2. `.admin-card` + `.card-header` → `SectionCard`；表格卡片加 `flush`。
3. `.stat-tile` / 自写 KPI 卡 → `StatTile`；手写的空提示 → `EmptyState`。
4. scoped 样式里：删掉写死的颜色（`#22d3ee`、`#7fe6f6`、`rgba(34,211,238,…)`、`#a78bfa`、`#6ee7b7`…）、
   `box-shadow` 装饰、`linear-gradient` 背景、`backdrop-filter`；旧变量名换成 `--au-*`。
5. 删除页面里的 `html[data-theme='light']` 分叉规则（令牌自己会切）。
6. `npm run type-check && npm run build`，再跑 `python3 scripts/check_frontend_tokens.py`。
7. 深色 / 白日两档各看一眼，窄屏（≤768px）看一眼。

## 7. 管理端 UI 统一标准（2026-11-15 定版 · 验收单）

基准：共享原语套件本身；样板页 `Dashboard`（列表/通用页）＋ `Settings`（设置型页）。
以下 13 条是各批改造 PR 的验收单——交卷时逐条对照，不满足的要在 PR 说明里写明理由与例外登记。

1. **页头**：一律 `PageHeader`：eyebrow＝侧栏分组名、title 与路由 `meta.title` 一致或更具体、
   description 一句话写清页面用途/数据口径；actions 右置，全页头只许一个 `type="primary"` 主按钮。
   壳页不叠第二层页头（子页带页头，沿 Codes 现行做法）。
   【验收】页面模板首个块为 PageHeader；grep 页头区 `type="primary"` ≤ 1。
2. **页面骨架**：根节点 `.admin-page`（flex 纵列、gap 16px），卡片之间不写 margin；
   内容最大宽度、内边距沿 Layout 现行口径不动。
   【验收】根节点 class 含 `admin-page`；SectionCard 上无 margin 样式。
3. **卡片**：一律 `SectionCard`（title＋meta＋actions）；表格卡加 `flush`；
   `tone="accent"` 一页最多 1–2 处；禁止再用 el-card 与自绘 `.card/.admin-card`。
   【验收】`scripts/check_frontend_tokens.py` 的 el-card 冻结检查通过（存量仅 WalletAdmin，批 3 清零）。
4. **筛选**：列表页筛选一律 `FilterBar` 声明式配置（搜索框＋select＋日期范围），
   禁止手写 `.xxx-filters` 与 el-form inline 筛选行；筛选状态走 `useQueryFilter` 同步到 URL。
   【验收】筛选区由 FilterBar 渲染；页面无 `*-filters` 自绘筛选类、无 inline 筛选表单。
5. **表格**：`DataTable` 为唯一表格载体（内置分页、移动端卡片模式、骨架屏、空态、多选、陈旧数据提示）；
   裸 el-table 只允许出现在 DataTable 内部，或 SectionCard flush 的只读小表（如详情键值表），
   且须在 PR 说明里注明理由。
   【验收】`check_frontend_tokens.py` 的裸 el-table 冻结检查通过（存量文件清单只许减不许增，逐批收编清零）。
6. **分页**：统一由 DataTable 的 pagination 配置出 el-pagination，不再各页自带；
   页大小口径沿 DataTable 现行默认值，列表默认 20 条/页一档（以组件现值为准，不新造）。
   【验收】页面模板无自带 el-pagination（DataTable 内部除外）。
7. **按钮**：表单/动作一律 el-button；一个区域一个 primary，语义色（success/warning/danger）
   用 EP 浅底描边口径（主题已映射）；行内操作 `size="small"`；
   危险操作统一走 `useDangerOps` 确认，不再各页自写 ElMessageBox 文案。
   【验收】同一 SectionCard/actions 区 primary ≤ 1；删除类操作经 useDangerOps。
8. **弹层**：新建/编辑短表单用 el-dialog（footer：取消＋保存，保存带 loading）；
   详情/长表单/多段内容用 el-drawer（沿 Tickets 详情抽屉样板）；
   同一页不混用两种承载同一类操作；弹层宽度沿 EP 主题现行口径，不在页面里覆写。
   【验收】同类操作只用一种弹层；dialog footer 为「取消＋保存」且保存钮绑 loading。
9. **表单**：el-form＋label-width 全站统一 **90px**；设置型页面按 Settings 样板：
   SectionCard 分组＋卡内表单＋统一保存条/未保存提示，不再每组各放一个保存钮。
   【验收】grep `label-width` 只有 90px（或不写、走默认继承）；设置页只有一个统一保存入口。
10. **空态与加载**：整页/卡片空态用 `EmptyState`，表格空态用 DataTable 内置或 EmptyState compact；
    加载用 au-skeleton 或 DataTable 骨架，禁止裸 `v-loading` 满屏转圈作为唯一反馈。
    【验收】空数据时页面出现 EmptyState/DataTable 空态而非空白或自绘提示行。
11. **KPI/统计行**：一律 `StatTile`（icon-left 布局用于页顶），数字走正文色，tone 只用于异常提示。
    【验收】页顶统计区由 StatTile 渲染，无自绘 KPI 卡类名。
12. **令牌与样式**：只用 `--au-*`；禁止写死 hex、linear-gradient、装饰 box-shadow、backdrop-filter；
    禁止页面内 `html[data-theme='light']` 分叉；scoped 样式只写布局/尺寸，不覆写 EP 颜色；
    交卷必过 `scripts/check_frontend_tokens.py` ＋ type-check ＋ build，深/浅两档各看一眼。
13. **字号间距**：标题走全局 h1/h2/h3（base.css 已统一）；卡片圆角 `--au-r-lg`、控件 `--au-r-md`；
    间距节奏 `--space-*`（16px 为卡间距基准）。
    【验收】页面不自设标题字号/字族；卡片圆角不写死数值。
