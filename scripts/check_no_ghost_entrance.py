#!/usr/bin/env python3
"""入场动画防重影契约检查：整块淡入与 blur 入场不许复发。

事故史（都发生在 user_frontend，用户真机录屏/截图实证）：
  - #101（2026-11-13）：LoginView 入场关键帧含 `filter: blur(6px)→0`，0.56s 内
    整卡文字被涂抹成双份（用户读作「文字重影」）。当时只摘了登录页自己的
    authColumnIn，共享关键帧 `uhd-blur-in` 里的 blur 没清——它还被
    NotFoundView、OperatorView、UhdModal、UhdNavPill、PricingView 用着，
    同机制残留全站。
  - 片库首页重影（2026-11-13）：StreamHomeView 的 `.sl-content` 挂
    `animation: sl-in 0.45s ease both`，整块内容（Hero＋继续观看整排封面）
    从 opacity 0 淡入，逐帧实证中间帧整块约三成不透明——单份封面、无双
    DOM、无 blur，纯半透明中间态被肉眼读成「鬼影」，再叠 App 路由转场更重。

复发机制：入场动画是「顺滑感」的默认加法，审查时只看落定帧没人看中间帧；
注释留档拦不住下一轮。只有 CI 里的硬断言能拦住。

检查三条规则（对象是源码，不是构建产物；先剥注释再查，注释里提及
blur/关键帧名不算违规，但也不许拿注释当豁免——查的是真声明）：
  1. StreamHomeView.vue 的 `.sl-content` 规则不得再声明 `animation`
     （骨架→内容直接切换，海报行/继续观看行不参与任何整块淡入）；
  2. StreamHomeView.vue 内任何 @keyframes 的起始帧（from/0%）不得设
     `opacity: 0`（整块内容入场淡入的指纹；骨架自身的呼吸脉冲
     sl-pulse 起始 opacity 为 1，不在此限）；
  3. user_frontend/src 全域：任何「入场型」关键帧（起始帧 opacity: 0）
     不得在任一帧含 `filter: blur(...)`——这正是 #101 的机制。
     常驻装饰动画（起始帧不淡入、如官网光钉 pin-breathe 的恒定虚化）
     不是入场，不在此限。

用法：python3 scripts/check_no_ghost_entrance.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
USER_SRC = ROOT / "user_frontend" / "src"
HOME_VIEW = USER_SRC / "views" / "panel" / "StreamHomeView.vue"

CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
TEMPLATE_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
KEYFRAMES_RE = re.compile(r"@keyframes\s+([\w-]+)\s*\{")


def fail(msg: str) -> NoReturn:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def strip_comments(text: str) -> str:
    text = TEMPLATE_COMMENT_RE.sub("", text)
    return CSS_COMMENT_RE.sub("", text)


def keyframes_blocks(text: str) -> list[tuple[str, str, int]]:
    """返回 (name, block_text, start_line) 列表，按大括号配平截取完整块。"""
    out: list[tuple[str, str, int]] = []
    for m in KEYFRAMES_RE.finditer(text):
        i = m.end()
        depth = 1
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        out.append((m.group(1), text[m.start():i], text[: m.start()].count("\n") + 1))
    return out


def frames_of(block: str) -> list[tuple[str, str]]:
    """把 @keyframes 块拆成 (selector, declarations) 帧列表。"""
    body = block[block.index("{") + 1 : block.rindex("}")]
    frames: list[tuple[str, str]] = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        frames.append((m.group(1).strip(), m.group(2)))
    return frames


def starts_from_opacity_zero(block: str) -> bool:
    """入场指纹：起始帧（from 或 0%）把 opacity 设为 0。"""
    for selector, decls in frames_of(block):
        sels = {s.strip() for s in selector.split(",")}
        if sels & {"from", "0%"} and re.search(r"opacity\s*:\s*0(?![\d.])", decls):
            return True
    return False


def check_home_view() -> None:
    if not HOME_VIEW.exists():
        fail(f"找不到 {HOME_VIEW.relative_to(ROOT)}，契约对象缺失")
    src = strip_comments(HOME_VIEW.read_text(encoding="utf-8"))

    # 规则 1：.sl-content 规则里不许再有 animation 声明
    m = re.search(r"\.sl-content\s*\{([^{}]*)\}", src)
    if m and re.search(r"(?<![\w-])animation\s*:", m.group(1)):
        fail(
            "StreamHomeView.vue 的 `.sl-content` 又挂上了 animation——"
            "整块内容淡入的中间帧就是封面重影（2026-11-13 录屏逐帧实证），"
            "骨架→内容必须直接切换。"
        )

    # 规则 2：本文件内不许出现起始帧 opacity: 0 的关键帧
    for name, block, line in keyframes_blocks(src):
        if starts_from_opacity_zero(block):
            fail(
                f"StreamHomeView.vue:{line} 关键帧 `{name}` 起始帧 opacity: 0——"
                "这是整块内容入场淡入的指纹，重影会跟着回来；"
                "骨架呼吸（sl-pulse）起始 opacity 为 1，不受此限。"
            )


def check_entrance_keyframes() -> None:
    # 规则 3：全域入场型关键帧不许含 filter: blur
    files = sorted(USER_SRC.rglob("*.vue")) + sorted(USER_SRC.rglob("*.css"))
    for path in files:
        src = strip_comments(path.read_text(encoding="utf-8", errors="ignore"))
        for name, block, line in keyframes_blocks(src):
            if starts_from_opacity_zero(block) and re.search(
                r"filter\s*:[^;}]*blur\s*\(", block
            ):
                rel = path.relative_to(ROOT)
                fail(
                    f"{rel}:{line} 入场关键帧 `{name}` 含 filter: blur——"
                    "入场期间整段文字/封面被涂抹成重影（#101 同机制，"
                    "2026-11-13 用户录屏实证），入场只许淡入/位移，不许 blur。"
                )


def main() -> None:
    check_home_view()
    check_entrance_keyframes()
    print("OK: 无整块淡入/blur 入场（.sl-content 无 animation、入场关键帧无 blur）")


if __name__ == "__main__":
    main()
