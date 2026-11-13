#!/usr/bin/env python3
"""移动端字号阶梯契约检查：移动端字号收紧层不许被删/改坏，桌面阶梯不许被顺手动。

事故史：
  - 2026-11-13 22:59（用户真机截图亲点）：「移动端字体全局偏大」——
    「团队管理」「订阅与财务」等标题明显比友站 UHDNow 大。根因是全站
    从来没有移动端字号层：uhd-tokens.css 的桌面阶梯（页题 34 / 分组 24 /
    展示 48 / 价格 56）原样落到 390px 手机，各页写死的标题字号
    （概览套餐名 40、流量数字 58、团队页题 30、菜单组题 20/行 17、
    片库 Hero 标题 clamp 下限 40）也没有移动收紧。

修复只走全局层（本检查钉住这三处，值以 390px 真渲染实测为准）：
  1. user_frontend/src/styles/uhd-tokens.css 末尾的移动阶梯块
     （@media max-width:768px 只重定义上半段 8 个 --uhd-fs-* 令牌），
     且桌面 :root 原值一字不许动（桌面与手机设施 1:1，只许移动断点内收）；
  2. user_frontend/src/styles/mobile.css 的「写死字号收紧层」
     （各页写死字号的全局移动覆盖，含 !important 终局口径）；
  3. admin_frontend/src/styles/uhd-tokens.css 的 @keyframes uhd-blur-in
     不许带 blur（与 user 端重影修复 #144 同口径：入场只许淡入/位移，
     blur 揭示会把整段文字/封面涂抹成重影）。

用法：python3 scripts/check_mobile_type_scale.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_TOKENS = ROOT / "user_frontend/src/styles/uhd-tokens.css"
MOBILE_CSS = ROOT / "user_frontend/src/styles/mobile.css"
ADMIN_TOKENS = ROOT / "admin_frontend/src/styles/uhd-tokens.css"

# 桌面阶梯原值（不许动）与移动阶梯目标值（≤768px）
DESKTOP_TOKENS = {
    "--uhd-fs-title": "18px",
    "--uhd-fs-h3": "24px",
    "--uhd-fs-h3-lg": "32px",
    "--uhd-fs-h1-app": "34px",
    "--uhd-fs-h2": "40px",
    "--uhd-fs-display": "48px",
    "--uhd-fs-price": "56px",
    "--uhd-fs-h1": "72px",
}
MOBILE_TOKENS = {
    "--uhd-fs-title": "17px",
    "--uhd-fs-h3": "20px",
    "--uhd-fs-h3-lg": "26px",
    "--uhd-fs-h1-app": "28px",
    "--uhd-fs-h2": "30px",
    "--uhd-fs-display": "34px",
    "--uhd-fs-price": "40px",
    "--uhd-fs-h1": "40px",
}
# mobile.css 收紧层：选择器 -> 移动目标值（390px 真渲染实测口径）
MOBILE_OVERRIDES = {
    ".v4-loghead h1": "28px",
    ".v4-sec-title": "20px",
    ".pc-gtitle": "20px",
    ".ov-card__plan": "32px",
    ".ov-quota__big": "40px",
    ".ov-quota__label": "15px",
    ".ov-sec__title": "20px",
    ".am-brand__title": "18px",
    ".am-username": "22px",
    ".am-group__title": "16px",
    ".am-row": "15px",
    ".am-logout": "15px",
    ".tm-title": "26px",
    ".tm-sec__title": "20px",
    ".tm-teamhead__name": "22px",
    ".sh-title": "30px",
    ".sub-settle__btn": "15px",
}

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def token_value(block: str, name: str) -> str | None:
    m = re.search(re.escape(name) + r"\s*:\s*([0-9.]+px)\s*;", block)
    return m.group(1) if m else None


def media_block(css: str) -> str:
    """取最后一个 @media (max-width: 768px) 块的全文（按花括号配平）。"""
    idx = css.rfind("@media (max-width: 768px)")
    if idx < 0:
        return ""
    start = css.find("{", idx)
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start : i + 1]
    return css[start:]


def main() -> int:
    user_css = USER_TOKENS.read_text(encoding="utf-8")
    mobile_css = MOBILE_CSS.read_text(encoding="utf-8")
    admin_css = ADMIN_TOKENS.read_text(encoding="utf-8")

    # 1) 桌面令牌原值不许动：取首个 :root 块核对
    first_root = user_css[user_css.find(":root") :]
    first_root = first_root[: first_root.find("\n}")]
    for name, want in DESKTOP_TOKENS.items():
        got = token_value(first_root, name)
        check(got == want, f"桌面令牌 {name} 应为 {want}，实际 {got}（桌面阶梯不许动）")

    # 2) 移动阶梯块存在且 8 个令牌值对
    ladder = media_block(user_css)
    check(bool(ladder), "uhd-tokens.css 缺移动端字号阶梯块（@media max-width:768px）")
    for name, want in MOBILE_TOKENS.items():
        got = token_value(ladder, name)
        check(got == want, f"移动令牌 {name} 应为 {want}，实际 {got}")

    # 3) mobile.css 收紧层：逐条核对选择器与目标值（须带 !important 终局口径）
    layer = media_block(mobile_css)
    check("写死字号收紧层" in mobile_css, "mobile.css 缺「写死字号收紧层」段落")
    for sel, want in MOBILE_OVERRIDES.items():
        pat = re.compile(re.escape(sel) + r"[^{]*\{[^}]*font-size:\s*" + re.escape(want) + r"\s*!important")
        check(bool(pat.search(layer)), f"mobile.css 收紧层缺 {sel} -> {want} !important")
    m = re.search(r"\.sub-settle__price\s+b\s*\{[^}]*font-size:\s*([0-9.]+px)\s*!important", layer)
    check(m and m.group(1) == "20px", "mobile.css 收紧层缺 .sub-settle__price b -> 20px !important")

    # 4) admin uhd-blur-in 关键帧不许带 blur（按花括号配平取块，避免越界误报）
    kidx = admin_css.find("@keyframes uhd-blur-in")
    check(kidx >= 0, "admin uhd-tokens.css 缺 @keyframes uhd-blur-in")
    if kidx >= 0:
        start = admin_css.find("{", kidx)
        depth = 0
        kend = start
        for i in range(start, len(admin_css)):
            if admin_css[i] == "{":
                depth += 1
            elif admin_css[i] == "}":
                depth -= 1
                if depth == 0:
                    kend = i
                    break
        check("blur" not in admin_css[start : kend + 1], "admin @keyframes uhd-blur-in 又带回了 blur（重影口径，只许淡入）")

    if failures:
        print("❌ 移动端字号阶梯契约检查失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("✅ 移动端字号阶梯契约检查通过（桌面阶梯原值未动 · 移动阶梯 8 令牌 · 收紧层 17+1 条 · admin 关键帧无 blur）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
