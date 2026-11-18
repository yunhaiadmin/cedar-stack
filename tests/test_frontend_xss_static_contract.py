"""前端存储型 XSS 静态契约：两端前端禁止新增未转义渲染入口。

背景（2026-11-18 黑盒审计收尾，评估见
reports/stored-xss-render-assessment-20261118.md）：用户可控内容
（工单标题/正文、弹幕、求片/反馈）后端原文存、原文发、无消毒器，
全站防线只有一条——两端前端全部走 Vue {{ }} 文本插值、靠框架默认
转义。这条防线是单点的：谁给这些字段加一个 v-html / innerHTML
渲染点，存储型 XSS 当场成立。本测试把「不许新增未转义渲染入口」
钉成随后端冒烟 CI 跑的契约：

1. 扫描 user_frontend/src 与 admin_frontend/src 的全部 .vue/.ts/.js，
   禁用模式：v-html、.innerHTML 赋值、insertAdjacentHTML、
   dangerouslyUseHTMLString、document.write(。
2. 命中必须逐条列入下方 EXEMPTIONS（文件＋行内容特征＋理由）才
   放行；新增豁免等于在 PR 里明示过审，不许静默加。清单之外的
   命中一律红，失败信息打出 文件:行号 与命中行内容。
3. 豁免不许变僵尸：每条豁免必须仍对应现存命中，命中行被改/删后
   对应测试会红，逼着同步清理清单（r44 allowlist 同口径）。

注：扫描是逐行文本匹配，连注释里写出禁用词也会命中——有意从严，
要讨论这些 API 请走豁免清单，不要在前端源码里随手提这些词。
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
SCAN_ROOTS = [REPO / "user_frontend" / "src", REPO / "admin_frontend" / "src"]
SCAN_SUFFIXES = {".vue", ".ts", ".js"}

_BANNED = [
    ("v-html 指令", re.compile(r"\bv-html\b")),
    (".innerHTML 赋值", re.compile(r"\.innerHTML\s*\+?=(?!=)")),
    ("insertAdjacentHTML", re.compile(r"\binsertAdjacentHTML\b")),
    ("dangerouslyUseHTMLString", re.compile(r"\bdangerouslyUseHTMLString\b")),
    ("document.write(", re.compile(r"\bdocument\.write\s*\(")),
]

# 豁免清单：(仓库相对路径, 行内容特征子串, 理由)。
# 新增一条 = 在 PR 里明示这处未转义渲染为什么安全、为什么不能走文本插值。
EXEMPTIONS = [
    (
        "user_frontend/src/views/Probe4kView.vue",
        "stageRef.value.innerHTML = ''",
        "4K 探针页每轮尝试前以常量空串清空 stage 容器（抹掉上一轮的"
        "解码 DOM），不写入任何变量或用户数据，无未转义渲染面；"
        "2026-11-18 存储型 XSS 渲染评估实证。",
    ),
]


def _scan_files() -> list[pathlib.Path]:
    files = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        files.extend(
            p for p in root.rglob("*") if p.is_file() and p.suffix in SCAN_SUFFIXES
        )
    return sorted(files)


def _scan_hits() -> list[tuple[str, int, str, str]]:
    """全部原始命中：(仓库相对路径, 行号, 模式名, 行原文)。"""
    hits = []
    for path in _scan_files():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pat in _BANNED:
                if pat.search(line):
                    hits.append((rel, lineno, label, line))
    return hits


def _is_exempt(rel: str, line: str) -> bool:
    return any(
        rel == ex_path and feature in line for ex_path, feature, _ in EXEMPTIONS
    )


def test_frontend_src_roots_exist():
    """扫描根必须都在且有文件——目录改名/搬家后守卫不许静默变空转。"""
    for root in SCAN_ROOTS:
        assert root.exists(), f"前端源码根不存在（守卫失去扫描面）：{root}"
    per_root = {
        root: sum(1 for p in _scan_files() if root in p.parents) for root in SCAN_ROOTS
    }
    empty = [str(r) for r, n in per_root.items() if n == 0]
    assert not empty, f"前端源码根下无可扫描文件（守卫失去扫描面）：{empty}"


def test_no_unexempted_unescaped_render_entrypoints():
    """清单之外的未转义渲染入口命中 = 红（存储型 XSS 单点防线契约）。"""
    bad = [h for h in _scan_hits() if not _is_exempt(h[0], h[3])]
    assert not bad, (
        "两端前端出现未豁免的未转义渲染入口（用户可控内容只靠 Vue 文本"
        "转义这一条防线，新增 v-html/innerHTML 类渲染点即打开存储型 "
        "XSS）：\n"
        + "\n".join(
            f"  {rel}:{lineno} [{label}] {line.strip()}"
            for rel, lineno, label, line in bad
        )
        + "\n能改走 {{ }} 文本插值的不要豁免；确需保留的，把（文件＋行"
        "内容特征＋理由）加进本测试的 EXEMPTIONS 过审。"
    )


def test_xss_guard_exemptions_still_live():
    """每条豁免必须仍对应现存命中——命中行被改/删后清单同步清理。"""
    hits = _scan_hits()
    stale = [
        f"{ex_path}（特征：{feature}）"
        for ex_path, feature, _ in EXEMPTIONS
        if not any(rel == ex_path and feature in line for rel, _, _, line in hits)
    ]
    assert not stale, (
        "EXEMPTIONS 里有已不对应任何现存命中的僵尸豁免，请删掉或更新"
        "行内容特征：" + ", ".join(stale)
    )
