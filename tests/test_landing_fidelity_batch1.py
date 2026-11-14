"""首批公开落地页保真：构图与 390 档标题不许再靠整页裁切藏字。

只锁本批改过的源码契约，不碰计费、权限、价格或注册开关。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / "user_frontend/src/views/marketing/LandingView.vue"
NAV = ROOT / "user_frontend/src/components/uhd/UhdNavPill.vue"
ACCOUNT = ROOT / "user_frontend/src/components/uhd/UhdAccountLayout.vue"


def _css(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _media(css: str, query: str) -> str:
    idx = css.find(query)
    assert idx >= 0, query
    start = css.find("{", idx)
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start : i + 1]
    raise AssertionError(f"unclosed {query}")


def test_desktop_hero_is_full_bleed_not_a_corner_card():
    css = _css(LANDING)
    assert "hero-prism-desktop.webp" in LANDING.read_text(encoding="utf-8")
    assert "hero-visual-frame" in css
    assert "min(620px, 46vw)" not in css
    assert "hero-beam" not in css
    visual = re.search(r"\.hero-visual\s*\{([^}]*)\}", css)
    assert visual, "缺少 .hero-visual"
    block = visual.group(1)
    assert "inset: 0" in block
    assert "mask-image" not in block


def test_hero_places_copy_left_specs_low_hint_right():
    css = _css(LANDING)
    assert ".hero-copy" in css
    assert ".hero-scrim" in css
    hint = re.search(r"\.hero-scroll-hint\s*\{([^}]*)\}", css)
    assert hint
    body = hint.group(1)
    assert "right:" in body
    assert "left: 50%" not in body
    specs = re.search(r"\.hero-specs\s*\{([^}]*)\}", css)
    assert specs
    assert "margin-top: 64px" not in specs.group(1)


def test_logged_out_and_logged_in_hero_actions_stay():
    src = LANDING.read_text(encoding="utf-8")
    assert "免费注册" in src and "查看定价" in src
    assert "进入后台" in src and "前往流媒体" in src
    assert "goRegister" in src and "goPricing" in src
    assert "goConsole" in src and "goStream" in src


def test_narrow_headings_wrap_instead_of_page_clip():
    css = _css(LANDING)
    assert "overflow-x: hidden" not in css
    narrow = _media(css, "@media (max-width: 420px)")
    assert "sec-title--platforms" in narrow
    assert "overflow-wrap: anywhere" in css
    src = LANDING.read_text(encoding="utf-8")
    assert "data-v-" in src and "setAttribute(scope.name" in src
    wrap = re.search(r"\.pt-char-wrap\s*\{([^}]*)\}", css)
    assert wrap
    assert "padding:" in wrap.group(1)
    title = re.search(r"\.hero-title\s*\{([^}]*)\}", css)
    assert title and "overflow: hidden" not in title.group(1)


def test_mobile_menu_keeps_keyboard_and_overlay():
    src = LANDING.read_text(encoding="utf-8")
    assert "onMenuKey" in src
    assert "Escape" in src
    assert 'role="dialog"' in src
    assert "aria-expanded" in src
    css = _css(LANDING)
    assert "position: fixed" in css
    assert ".m-sheet" in css


def test_account_sidebar_mobile_control_is_real():
    """源码契约：窄屏有可达的打开钮、遮罩和 Esc。不是浏览器回归。"""
    src = ACCOUNT.read_text(encoding="utf-8")
    css = _css(ACCOUNT)
    narrow = _media(css, "@media (max-width: 768px)")
    assert "uhd-account__navbtn" in src
    assert "Escape" in src
    assert "uhd-account__scrim" in src
    assert "is-nav-open" in narrow
    assert "position: fixed" in narrow
    assert "display: none" in narrow


def test_shared_nav_escape_and_focus():
    src = NAV.read_text(encoding="utf-8")
    css = _css(NAV)
    assert "Escape" in src
    assert "aria-expanded" in src
    assert ":focus-visible" in css


def test_scrolled_nav_becomes_one_capsule_without_transform():
    css = _css(NAV)
    shrunk = css.split(".uhd-nav--shrink.is-shrunk .uhd-nav__pill", 1)[1]
    block = shrunk.split("}", 1)[0]
    assert "position: static" in block
    assert "transform: none" in block
