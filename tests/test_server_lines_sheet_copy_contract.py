"""概览线路弹窗文案契约：ServerLinesSheet 五处呈现与友站原文对齐。

背景（2026-11-18 双站录屏逐帧盘点，差异清单
reports/参考站-gap-list-20261118.md 的 G-21/G-22/G-23/G-27/G-28）：
概览页 Emby 区「服务器地址」弹层的标题、说明、分组头与底部提示
此前是我方改写版，与友站 原文不一致。本测试把五条对齐
钉成随后端冒烟 CI 跑的静态契约，防文案日后被随手改回去：

- G-21 标题「Emby 线路」（旧「Emby 服务线路」多「服务」二字）；
- G-22 标题下说明两行「在兼容 Emby 的第三方客户端里添加服务器，」
  「填入线路地址即可。」（旧无说明段）；
- G-23 小节头「线路」（旧「播放线路」）＋同行刷新图标，刷新经
  refresh 事件接父页账号卡重拉（PersonalCenterView 的 loadOverview）；
- G-27 「客户端」两行副文「各平台可用的第三方客户端」/
  「添加服务器与登录步骤」，右侧图标为 ↗（ArrowUpRight）；
- G-28 底部说明两句「没有特别标注端口的线路，都使用 HTTPS 443
  端口。」「不确定选哪条，可以先去线路测速。」（测速链接保留）。

只钉文案与字段呈现，不涉及尺寸数值（清单尺寸为帧估算，未经复量）。
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
SHEET = REPO / "user_frontend/src/components/profile/ServerLinesSheet.vue"
PARENT = REPO / "user_frontend/src/views/game/PersonalCenterView.vue"


def _sheet() -> str:
    assert SHEET.exists(), f"线路弹层组件不存在：{SHEET}"
    return SHEET.read_text(encoding="utf-8")


def test_g21_title_is_emby_lines() -> None:
    src = _sheet()
    assert ">Emby 线路<" in src, "G-21：弹层标题应为「Emby 线路」"
    assert "Emby 服务线路" not in src, "G-21：旧标题「Emby 服务线路」不许残留（含注释/aria）"


def test_g22_intro_two_lines_present() -> None:
    src = _sheet()
    assert "在兼容 Emby 的第三方客户端里添加服务器，" in src, "G-22：缺说明第一行"
    assert "填入线路地址即可。" in src, "G-22：缺说明第二行"


def test_g23_group_label_and_refresh() -> None:
    src = _sheet()
    assert ">线路<" in src, "G-23：小节头应为「线路」"
    assert ">播放线路<" not in src, "G-23：旧小节头「播放线路」不许残留"
    assert "RefreshCw" in src, "G-23：小节头同行应有刷新图标（RefreshCw）"
    assert "refresh" in src, "G-23：刷新应经 refresh 事件通知父页重拉账号卡"
    parent = PARENT.read_text(encoding="utf-8")
    assert '@refresh="loadOverview"' in parent, (
        "G-23：父页应把 refresh 接到账号卡重拉 loadOverview"
    )


def test_g27_client_rows_copy_and_icon() -> None:
    src = _sheet()
    assert "各平台可用的第三方客户端" in src, "G-27：支持的客户端副文不对"
    assert "添加服务器与登录步骤" in src, "G-27：配置教程副文不对"
    assert "手机、电视、电脑客户端下载" not in src, "G-27：旧副文不许残留"
    assert "下载、添加服务器、登录，分步说明" not in src, "G-27：旧副文不许残留"
    assert "ArrowUpRight" in src, "G-27：两行右侧图标应为 ↗（ArrowUpRight）"
    assert "ChevronRight" not in src, "G-27：旧右侧图标 ChevronRight 不许残留"


def test_g28_footer_copy() -> None:
    src = _sheet()
    assert "没有特别标注端口的线路，都使用 HTTPS 443 端口。" in src, "G-28：底部第一句不对"
    assert "不确定选哪条，可以先去" in src, "G-28：底部第二句不对"
    assert "哪条快用哪条拿不准就先" not in src, "G-28：旧底部改写版不许残留"
    assert 'to="/game/speedtest"' in src, "G-28：「线路测速」链接落点应保留"
