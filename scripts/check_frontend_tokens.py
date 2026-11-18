#!/usr/bin/env python3
"""前端设计令牌契约检查：禁止引用「没人定义」的 CSS 变量，禁止令牌自引用。

起因：用户端的 `mobile-tokens.css` 写了整套间距 / 排版令牌，但**从未被 main.ts 引入**，
而 ListItem / SectionHeader / StatCard 三个组件一直在用 `var(--space-md)`、`var(--card-bg)`：
变量解析失败 → `padding: var(--space-md) 0` 整条声明作废，卡片直接没有背景。
类型检查看不出来（CSS 字符串就是字符串），构建也不会失败——只能在构建期用契约检查兜住。

另外 index.css 里曾有一批「自引用」的兼容映射（`--text-primary: var(--text-primary)`）：
CSS 规范里这是循环引用，变量会变成 invalid at computed-value time，
所有 `color: var(--text-primary)` 静默退化成「继承父级颜色」，标题层级全乱且不报错。

检查五条规则：
  1. 任何 `var(--x)` 的 `--x` 必须在**被引入**的样式表里定义过；
  2. 不允许 `--x: var(--x)` 自引用；
  3. 不允许纯别名构成的循环（`--a: var(--b)` + `--b: var(--a)`）；
  4. src 下不允许存在「谁都没引入」的样式表（上面那次事故的根源）。
  5. 两端 uhd-tokens.css 必须逐字一致：user_frontend 那份是唯一真源，
     admin_frontend 是同值副本（后台重设计批 0，2026-11-18）。历史上副本
     曾漂移成过期版（344 行 vs 真源 487 行、缺 29 个令牌）且无人察觉——
     改 --uhd-* 令牌时只许改真源，再把副本同步成逐字相同，否则 CI 失败。
  6. 管理端壳（Layout.vue / CommandPalette.vue / uhd-admin.css 壳模具）的
     字号只许引令牌（后台重设计批 1，2026-11-18）：壳文件里曾散着 13.5px /
     11.5px / 10.5px 这类不在 --uhd-fs-* 梯队里的分数值与逐处手写的整数值，
     改一处漏一处；壳模具的导航项圆角/字号、品牌字、眉题钉死引
     --uhd-radius-navitem / --uhd-fs-body / --uhd-fs-lead / --uhd-fs-aux。

样式表集合从入口（main.ts / index.html 的 <style>）出发，沿 @import 与 import 语句递归收集，
所以「文件存在但没接进应用」不会被误判成已定义。

用法：python3 scripts/check_frontend_tokens.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTENDS = ("user_frontend", "admin_frontend")

DEFINE_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
# 一条完整声明：`--x: 值;`（值里带分号的情况在自定义属性里不合法，不必考虑）
DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]*);")
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
USE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
# 只有「整条值就是一个 var(--x)，没有兜底值」才参与循环判定：
# 带 fallback 的写法（var(--x, 8px)）本身不会构成循环。
PURE_ALIAS_RE = re.compile(r"^var\(\s*(--[A-Za-z0-9_-]+)\s*\)$")
CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\()?['"]([^'"]+)['"]""")
TS_IMPORT_RE = re.compile(r"""import\s+['"]([^'"]*\.css)['"]""")
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S)

failures: list[str] = []
checks = 0


def resolve(base: Path, spec: str) -> Path | None:
    """把相对引用解析成文件路径；包名（node_modules）与远程地址一律忽略"""
    if not spec or spec.startswith(("http://", "https://", "//", "~", "@")):
        return None
    candidate = (base.parent / spec).resolve()
    if candidate.suffix == "":
        candidate = candidate.with_suffix(".css")
    return candidate if candidate.exists() else None


def collect_stylesheets(frontend: str) -> tuple[dict[Path, str], set[Path]]:
    """返回（被引入的样式表 → 内容）与（src 下所有样式表）

    dict 的顺序按 BFS 收集顺序，也就是**级联顺序**（入口先写的在后面生效）：
    同名令牌的最后一个声明胜出，判定别名就得按这个顺序取最后一次声明。
    """
    src = ROOT / frontend / "src"
    all_sheets = {p.resolve() for p in src.rglob("*.css")}
    loaded: dict[Path, str] = {}
    queue: list[Path] = []

    main_ts = src / "main.ts"
    if main_ts.exists():
        for m in TS_IMPORT_RE.finditer(main_ts.read_text(encoding="utf-8")):
            found = resolve(main_ts, m.group(1))
            if found:
                queue.append(found)

    # 样式表也可能被组件直接 import，或被 <style> 里的 @import 引入
    for path in list(src.rglob("*.ts")) + list(src.rglob("*.vue")):
        text = path.read_text(encoding="utf-8")
        for m in TS_IMPORT_RE.finditer(text):
            found = resolve(path, m.group(1))
            if found:
                queue.append(found)
        for block in STYLE_BLOCK_RE.findall(text):
            for m in CSS_IMPORT_RE.finditer(block):
                found = resolve(path, m.group(1))
                if found:
                    queue.append(found)

    index = 0
    while index < len(queue):
        sheet = queue[index]
        index += 1
        if sheet in loaded or sheet.suffix != ".css":
            continue
        text = sheet.read_text(encoding="utf-8")
        loaded[sheet] = text
        for m in CSS_IMPORT_RE.finditer(text):
            found = resolve(sheet, m.group(1))
            if found:
                queue.append(found)

    return loaded, all_sheets


def find_alias_cycles(aliases: dict[str, str]) -> list[list[str]]:
    """aliases: 变量 → 它唯一的别名目标；返回所有环形链"""
    cycles: list[list[str]] = []
    for start in aliases:
        seen: list[str] = []
        node = start
        while node in aliases:
            if node in seen:
                cycles.append(seen[seen.index(node):] + [node])
                break
            seen.append(node)
            node = aliases[node]
    # 同一个环会从任意一个成员出发被再发现一次，去重（按环上最小成员）
    unique: dict[str, list[str]] = {}
    for cycle in cycles:
        unique.setdefault(min(cycle), cycle)
    return list(unique.values())


def check_frontend(frontend: str) -> None:
    global checks
    src = ROOT / frontend / "src"
    loaded, all_sheets = collect_stylesheets(frontend)

    defined: dict[str, str] = {}
    # 同名令牌以「最后一次声明」为准：值 → (声明序号, 别名目标 or None)
    latest: dict[str, tuple[int, str | None]] = {}
    selfref: list[str] = []
    seq = 0
    for sheet, text in loaded.items():
        rel = sheet.relative_to(ROOT)
        body = COMMENT_RE.sub("", text)
        for lineno, line in enumerate(body.splitlines(), 1):
            for m in DEFINE_RE.finditer(line):
                defined.setdefault(m.group(1), f"{rel}:{lineno}")
        for m in DECL_RE.finditer(body):
            seq += 1
            name, value = m.group(1), m.group(2).strip()
            alias = PURE_ALIAS_RE.match(value)
            target = alias.group(1) if alias else None
            latest[name] = (seq, target)
            if target == name:
                selfref.append(f"{rel}:{body[:m.start()].count(chr(10)) + 1}  {name}: var({name})")
    aliases = {name: target for name, (_seq, target) in latest.items() if target and target != name}

    used: dict[str, set[str]] = {}
    for path in list(src.rglob("*.vue")) + list(src.rglob("*.css")) + list(src.rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        for m in USE_RE.finditer(text):
            used.setdefault(m.group(1), set()).add(str(path.relative_to(ROOT)))

    checks += 1
    missing = sorted(name for name in used if name not in defined)
    if missing:
        failures.append(f"{frontend}: {len(missing)} 个令牌被引用但没有任何被引入的样式表定义它们")
        for name in missing:
            where = sorted(used[name])
            failures.append(f"    {name} ← {where[0]}" + (f" 等 {len(where)} 处" if len(where) > 1 else ""))

    checks += 1
    cycles = find_alias_cycles(aliases)
    if selfref or cycles:
        total = len(selfref) + len(cycles)
        failures.append(f"{frontend}: {total} 处令牌循环引用（CSS 里会整条失效并静默降级）")
        for item in selfref:
            failures.append("    " + item)
        for cycle in cycles:
            failures.append("    " + " → ".join(cycle))

    checks += 1
    orphans = sorted(p.relative_to(ROOT) for p in all_sheets - set(loaded))
    if orphans:
        failures.append(f"{frontend}: {len(orphans)} 个样式表没有任何入口引入（写了也不会生效）")
        for path in orphans:
            failures.append(f"    {path}")


# U0 冻结（UI 统一 LC-09/LC-10）：main.ts 全量引入的主题 CSS 不许新增；
# --neo-* 体系只许退场（U3/A5），不许在新文件里出现。
# neo-noir-tokens.css 被删除（A5）时，记得同步从 NEO_FREEZE 里删掉它这一行。
# U0 冻结（UI 统一 LC-09/LC-10）：main.ts 全量引入的主题 CSS 不许新增；
# R40：用相对 src 的相对路径登记+比对（原来只比文件名，同名 CSS 可绕过冻结）。
MAIN_CSS_ALLOWLIST = {
    "user_frontend": {"styles/aurora.css", "styles/neo-noir-tokens.css", "styles/index.css", "styles/mobile.css", "styles/v4-paper.css", "styles/uhd-tokens.css", "styles/uhd-topbar.css"},
    "admin_frontend": {"styles/aurora.css", "styles/tokens.css", "styles/base.css", "styles/index.css", "styles/element-plus-theme.css", "styles/responsive.css", "styles/uhd-tokens.css", "styles/uhd-admin.css"},
}
NEO_FREEZE = {
    "user_frontend": {
        "components/AppDock.vue", "components/ui/Modal.vue",
        "styles/aurora.css", "styles/index.css", "styles/mobile.css",
        "styles/neo-noir-tokens.css",
    },
    "admin_frontend": set(),
}
NEO_TOKEN_RE = re.compile(r"--neo-[A-Za-z0-9_-]+")


def check_freeze(frontend: str) -> None:
    global checks
    src = ROOT / frontend / "src"
    main_ts = src / "main.ts"

    checks += 1
    imported = set()
    for m in TS_IMPORT_RE.finditer(main_ts.read_text(encoding="utf-8")):
        found = resolve(main_ts, m.group(1))
        if found:
            # R40：比对相对 src 的相对路径，不只比文件名（同名 CSS 绕过冻结）；
            # 解析到 src 之外（../ 之类）时回退到文件名，保证不抛错
            try:
                imported.add(str(found.relative_to(src)))
            except ValueError:
                imported.add(found.name)
    extra = sorted(imported - MAIN_CSS_ALLOWLIST[frontend])
    if extra:
        failures.append(
            f"{frontend}: main.ts 新增了未登记的主题 CSS 全量引入（UI 统一 U0 冻结）："
            + ", ".join(extra)
        )

    checks += 1
    neo_files = set()
    for path in list(src.rglob("*.vue")) + list(src.rglob("*.css")) + list(src.rglob("*.ts")):
        if NEO_TOKEN_RE.search(path.read_text(encoding="utf-8")):
            neo_files.add(str(path.relative_to(src)))
    new_neo = sorted(neo_files - NEO_FREEZE[frontend])
    if new_neo:
        failures.append(
            f"{frontend}: {len(new_neo)} 个文件新增了 --neo-* 引用"
            "（UI 统一 U0 冻结：neo 体系只许退场、不许扩张）"
        )
        for f in new_neo:
            failures.append(f"    {f}")


# 管理端 UI 统一（2026-11-15 定版，标准第 3/5 条）：结构层冻结。
# el-card 与 views 里的裸 el-table 只许退场、不许扩张：存量文件登记在下面两张表里，
# 逐批收编后从表中删行；表外文件新出现即失败。收编进度见
# admin_frontend/src/components/ui/README.md 第 7 节验收单。
# 注：el-card 存量已随批 3 WalletAdmin 收编清零；裸表存量已删 MediaCollections /
# WalletAdmin 两行（批 3）。
ADMIN_EL_CARD_FREEZE: set[str] = set()  # 批 3 已清零（原存量仅 WalletAdmin.vue）
# 后台重设计批 2（2026-11-18）：三页存量裸表（MemberCodes / PoolAdmin /
# SystemHealth）已全部收编 DataTable，冻结表就此改为白名单制——views 里
# 不许再出现裸 el-table；DataTable 实在承载不了的例外页才许登记在这里，
# 且必须注明原因（等 parent 定夺），不许先斩后奏。
ADMIN_BARE_TABLE_WHITELIST: dict[str, str] = {}
EL_CARD_RE = re.compile(r"<el-card[\s>]")
EL_TABLE_RE = re.compile(r"<el-table[\s>]")


def check_admin_structure() -> None:
    global checks
    src = ROOT / "admin_frontend" / "src"

    checks += 1
    card_files = set()
    table_files = set()
    for path in src.rglob("*.vue"):
        rel = str(path.relative_to(src))
        text = path.read_text(encoding="utf-8")
        if EL_CARD_RE.search(text):
            card_files.add(rel)
        if rel.startswith("views/") and EL_TABLE_RE.search(text):
            table_files.add(rel)
    new_cards = sorted(card_files - ADMIN_EL_CARD_FREEZE)
    if new_cards:
        failures.append(
            "admin_frontend: 新增了 el-card 用法（UI 统一标准第 3 条：卡片一律 SectionCard，"
            "el-card 只许退场）："
        )
        for f in new_cards:
            failures.append(f"    {f}")
    new_tables = sorted(table_files - set(ADMIN_BARE_TABLE_WHITELIST))
    if new_tables:
        failures.append(
            "admin_frontend: views 里出现了裸 el-table（UI 统一标准第 5 条：表格一律 DataTable；"
            "DataTable 承载不了的例外须登记 ADMIN_BARE_TABLE_WHITELIST 并注明原因）："
        )
        for f in new_tables:
            failures.append(f"    {f}")


def check_uhd_tokens_sync() -> None:
    """规则 5：admin 的 uhd-tokens.css 副本必须与用户端真源逐字一致（批 0 地基）"""
    global checks
    checks += 1
    source = ROOT / "user_frontend" / "src" / "styles" / "uhd-tokens.css"
    copy = ROOT / "admin_frontend" / "src" / "styles" / "uhd-tokens.css"
    if not source.exists() or not copy.exists():
        failures.append("uhd-tokens 同步检查：两端 uhd-tokens.css 必须都存在")
        return
    if source.read_bytes() == copy.read_bytes():
        return
    src_lines = source.read_text(encoding="utf-8").splitlines()
    copy_lines = copy.read_text(encoding="utf-8").splitlines()
    failures.append(
        "uhd-tokens 两端不一致：admin_frontend/src/styles/uhd-tokens.css 是用户端真源的"
        "同值副本，不许漂移（改令牌只改 user_frontend 那份，再逐字同步副本）"
        f"（真源 {len(src_lines)} 行 / 副本 {len(copy_lines)} 行）"
    )
    shown = 0
    for i in range(max(len(src_lines), len(copy_lines))):
        a = src_lines[i] if i < len(src_lines) else "<缺行>"
        b = copy_lines[i] if i < len(copy_lines) else "<缺行>"
        if a != b:
            failures.append(f"    第 {i + 1} 行起不一致：真源「{a.strip()[:60]}」≠ 副本「{b.strip()[:60]}」")
            shown += 1
            if shown >= 3:
                break


# 后台重设计批 1（2026-11-18）：壳字号口径。Layout.vue / CommandPalette.vue 是
# 壳本体（侧栏/顶栏/抽屉/命令面板），font-size 不许再写数值字面量，一律引
# --uhd-fs-*（分数值归相邻档）；uhd-admin.css 壳模具的导航项/品牌字/眉题同理
# 钉死引统一令牌，防止模具与真源再次漂移。
SHELL_FONT_LITERAL_RE = re.compile(r"font-size:\s*\d+(?:\.\d+)?(?:px|rem|em)\b")
SHELL_FONT_FRACTION_RE = re.compile(r"font-size:\s*\d+\.\d+(?:px|rem|em)\b")
ADMIN_SHELL_VUE = (
    "admin_frontend/src/views/Layout.vue",
    "admin_frontend/src/components/CommandPalette.vue",
)
ADMIN_SHELL_FILES = ADMIN_SHELL_VUE + ("admin_frontend/src/styles/uhd-admin.css",)
ADMIN_SHELL_MOLD_TOKENS = (
    "var(--uhd-radius-navitem)",
    "var(--uhd-fs-body)",
    "var(--uhd-fs-lead)",
    "var(--uhd-fs-aux)",
)


def check_admin_shell() -> None:
    global checks

    checks += 1
    bad = []
    for rel in ADMIN_SHELL_VUE:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if SHELL_FONT_LITERAL_RE.search(line):
                bad.append(f"    {rel}:{i}  {line.strip()[:80]}")
    if bad:
        failures.append(
            "admin_frontend: 壳文件（Layout.vue / CommandPalette.vue）里还有 font-size "
            "数值字面量（后台重设计批 1：壳字号一律引 --uhd-fs-* 令牌）："
        )
        failures.extend(bad)

    checks += 1
    bad = []
    for rel in ADMIN_SHELL_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if SHELL_FONT_FRACTION_RE.search(line):
                bad.append(f"    {rel}:{i}  {line.strip()[:80]}")
    if bad:
        failures.append(
            "admin_frontend: 壳文件里还有分数值 font-size（--uhd-fs-* 梯队没有分数档，"
            "须归入相邻档）："
        )
        failures.extend(bad)

    checks += 1
    mold = (ROOT / "admin_frontend/src/styles/uhd-admin.css").read_text(encoding="utf-8")
    missing = [t for t in ADMIN_SHELL_MOLD_TOKENS if t not in mold]
    if missing:
        failures.append(
            "admin_frontend: uhd-admin.css 壳模具没有引统一令牌（后台重设计批 1："
            "导航项圆角/字号、品牌字、眉题走 --uhd-*）：缺少 " + ", ".join(missing)
        )


# 后台重设计批 2（2026-11-18）：带列表筛选/搜索区的页面必须用共享 FilterBar，
# 不许再手写筛选条。清单为准绳（2026-11-18 全量盘点 49 个 view）：
# 前 10 个是批 2 之前已收编的，后 11 个是批 2 本批收编的。
# 盘点里不进清单的形态（登记在此备查，不算漏检）：页头/卡头的范围切换 radio
# （Goods／Servers／Subscriptions／MemberCodes 的当前服/全部服，与 MediaSeek
# 既有共存形态一致）、Servers 的统计卡切换、Dashboard 的图表口径 radio、
# SystemHealth 的告警记录状态页签（el-tabs，FilterBar 无页签形态）、
# LibraryScope 选择器内的本地过滤输入框。
ADMIN_FILTERBAR_REQUIRED = (
    "views/Devices.vue",
    "views/Invitations.vue",
    "views/LoginLogs.vue",
    "views/MediaSeek.vue",
    "views/OperationLogs.vue",
    "views/Orders.vue",
    "views/ShareGuard.vue",
    "views/StorageBindings.vue",
    "views/Users.vue",
    "views/WalletAdmin.vue",
    # —— 批 2 收编 ——
    "views/Announcements.vue",
    "views/Coupons.vue",
    "views/DualDriveAudit.vue",
    "views/GovernanceReports.vue",
    "views/GovernanceSuspensions.vue",
    "views/MemberCodes.vue",
    "views/MissingEpisodes.vue",
    "views/PoolAdmin.vue",
    "views/Subscriptions.vue",
    "views/Tickets.vue",
    "views/WelfareLotteryProposals.vue",
)


def check_admin_filterbar() -> None:
    """批 2 断言：盘点清单内的带筛选页面必须引用 FilterBar"""
    global checks
    checks += 1
    src = ROOT / "admin_frontend" / "src"
    missing = []
    for rel in ADMIN_FILTERBAR_REQUIRED:
        path = src / rel
        if not path.exists() or "FilterBar" not in path.read_text(encoding="utf-8"):
            missing.append(rel)
    if missing:
        failures.append(
            "admin_frontend: 下列带筛选/搜索区的页面还没有用共享 FilterBar"
            "（批 2 口径：筛选一律 FilterBar，手写筛选条只许退场）："
        )
        for f in missing:
            failures.append(f"    {f}")


for frontend in FRONTENDS:
    check_frontend(frontend)
    check_freeze(frontend)
check_admin_structure()
check_uhd_tokens_sync()
check_admin_shell()
check_admin_filterbar()

print(f"设计令牌契约检查：{checks} 项")
if failures:
    print("\n".join(failures))
    print("❌ 设计令牌契约检查失败")
    sys.exit(1)
print("✅ 设计令牌契约检查通过（引用的令牌都有定义 · 无循环引用 · 没有孤立样式表 · 主题引入冻结 · neo 冻结 · 两端 uhd-tokens 逐字同步 · 壳字号走令牌 · 裸表白名单 · 筛选走 FilterBar）")
