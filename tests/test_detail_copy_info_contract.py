"""详情页文案与信息表静态契约（2026-11-18 友站逐帧盘点差异批）。

覆盖 G-98 / G-100 / G-124 / G-126 / G-127 / G-130 / G-132 七条，
把已定版的详情页呈现钉成契约，防止后续改动把文案、键集、链接
形态悄悄改回去：

- G-98 元信息行顺序与文案：年份 →「N 季」（带空格）→ 电影时长
  （「2 小时 19 分」带空格文案）→ ★评分。共享 formatDuration
  不许顺手改（选集弹层/集行还在用旧文案，见 StreamHomeView
  注释先例），详情页用本地 spaced 口径。
- G-100 主按钮彩虹描边流动动画在位（keyframes + flow 令牌）。
- G-124 / G-127 信息表键集：剧集＝剧名/首播/季数/类型/链接；
  电影＝片名/原名/上映/片长/类型/链接；旧键（主演/年份/分级/
  出品）不许回潮。
- G-126 TMDB/IMDb 为可点外链胶囊（themoviedb.org、imdb.com/title），
  不许退回纯文本 ID 标。
- G-130 推荐卡 meta 带类型段（年份 · 类型 · 评分）。
- G-132 规格与类型同流描边胶囊（md-chips--spec 分行形态退役）、
  声道文案带空格（「5.1 声道」）。
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
MDV = REPO / "user_frontend/src/views/panel/MediaDetailView.vue"
EMBY_TS = REPO / "user_frontend/src/api/emby.ts"
TOKENS = REPO / "user_frontend/src/styles/uhd-tokens.css"


def _mdv() -> str:
    return MDV.read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def test_g98_meta_order_and_spacing():
    src = _mdv()
    block = _block(src, "const metaParts", "return parts")
    # 「N 季」带空格
    assert "`${it.ChildCount} 季`" in block, "季数文案应为「N 季」（数字与季间带空格）"
    # 顺序：季数 → 电影时长 → 评分（时长在评分之前）
    i_season = block.index("ChildCount")
    i_dur = block.index("formatDurationSpaced")
    i_rating = block.index("CommunityRating")
    assert i_season < i_dur < i_rating, "元信息行顺序应为 季数 → 时长 → 评分"
    # spaced 时长文案本体
    helper = _block(src, "function formatDurationSpaced", "\n}\n")
    assert " 小时 " in helper and " 分`" in helper, "详情时长应为「2 小时 19 分」带空格文案"


def test_g98_shared_format_duration_untouched():
    src = EMBY_TS.read_text(encoding="utf-8")
    assert "`${h}小时${m}分钟`" in src, "共享 formatDuration 文案不许随本批改动（其他面在用）"


def test_g100_rainbow_flow_animation():
    src = _mdv()
    assert "@keyframes md-rainbow-flow" in src, "主按钮应有彩虹描边流动动画 keyframes"
    assert "animation: md-rainbow-flow" in src, ".md-btn--play 应挂流动动画"
    tokens = TOKENS.read_text(encoding="utf-8")
    assert "--uhd-rainbow-border-flow" in tokens, "uhd-tokens 应有双周期彩虹描边 flow 令牌"
    assert "var(--uhd-rainbow-border-flow) border-box" in src, "主按钮描边层应引用 flow 令牌"


def test_g124_g127_info_table_keys():
    src = _mdv()
    for label in ("剧名", "首播", "季数", "片名", "原名", "上映", "片长", "类型", "链接"):
        assert f"<dt>{label}</dt>" in src, f"信息表应有「{label}」行"
    for label in ("主演", "年份", "分级", "出品"):
        assert f"<dt>{label}</dt>" not in src, f"信息表旧键「{label}」不许回潮"
    assert "item.OriginalTitle" in src, "电影「原名」行应渲染 DTO 现成的 OriginalTitle"


def test_g126_external_link_chips():
    src = _mdv()
    assert "https://www.themoviedb.org/tv/" in src, "剧集 TMDB 应链到 /tv/{id}"
    assert "https://www.themoviedb.org/movie/" in src, "电影 TMDB 应链到 /movie/{id}"
    assert "https://www.imdb.com/title/" in src, "IMDb 应链到 /title/{id}"
    assert "TMDB {{ tmdbId }}" not in src, "不许退回纯文本 ID 标"
    assert 'target="_blank"' in src, "外链胶囊应新开页"


def test_g130_recommendation_meta_has_genre():
    src = _mdv()
    fn = _block(src, "function recMeta", "\n}\n")
    assert "ProductionYear" in fn and "Genres" in fn, "推荐卡 meta 应含年份与类型两段"
    assert ':meta="recMeta(rec)"' in src, "推荐卡应走 recMeta 传参"


def test_g132_spec_genre_unified_flow():
    src = _mdv()
    assert "md-chips--spec" not in src, "规格分行形态（md-chips--spec）应退役、与类型同流"
    assert "md-chip--genre" not in src, "类型胶囊应与规格同款 .md-chip，不再用行内小胶囊"
    assert 'v-for="g in genreChips"' in src and 'v-for="c in specChips"' in src
    assert "'5.1 声道'" in src, "声道文案应为「5.1 声道」（带空格）"
    assert "'5.1声道'" not in src, "声道文案不许无空格"
