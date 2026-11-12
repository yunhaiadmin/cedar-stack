"""Season 名字污染回归（2026-11-12，用户截图报「好多资源名字污染了」）。

生产实证（visible 口径，1,339 条）：季文件夹名被写成条目名——
episode 行「Season 01 S01」998 条、「Season 1950 S19」68 条，
series 行「Season 01」「Season 1960」等 5 条。

根因（scanner.py）：
- ``parse_media_filename`` 的标题回退链在 raw 被挖空、父目录是季目录时，
  把 folder 名（「Season 01」）当成标题；
- ``_series_name_of`` 只跳一层季目录，双季文件夹（``Season 01/Season 01/``）
  落在外层季目录上；年季（``Season 1950/1960``）根本不认成季目录。

这里钉住：三条生产真实路径不再产出污染名；series 名推导逐层跳季目录；
季标记本身永远不当标题。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server.scanner import (  # noqa: E402
    ScanFile,
    _episode_display_name,
    _is_season_like_name,
    _series_name_of,
    parse_media_filename,
)


def _p(path, lib="tvshows"):
    return parse_media_filename(path, lib)


def _display(path):
    r = _p(path)
    return _episode_display_name(r["name"], r["season"], r["episode"])


# ==================== 生产真实路径：episode 名不再是 Season ====================

def test_s0101_compact_form_no_longer_season_polluted():
    # 生产行：44 库「Season 01 S01」（一吻不定情 S0101）
    r = _p("mount://4/video/剧集/国产剧/一吻不定情 (2018)/Season 01/"
           "一吻不定情.Only.Kiss.Without.Love.S0101.1080p.mp4")
    assert r["name"] == "一吻不定情"
    assert r["year"] == 2018
    assert (r["season"], r["episode"]) == (1, None)
    assert _display("mount://4/video/剧集/国产剧/一吻不定情 (2018)/Season 01/"
                    "一吻不定情.Only.Kiss.Without.Love.S0101.1080p.mp4") == "一吻不定情 S01"


def test_sxxep_form_no_longer_season_polluted():
    # 生产行：45 库「Season 02 S02」（时效警察 S02EP01）
    r = _p("mount://4/video/剧集/日韩剧/时效警察 (2006)/Season 02/"
           "时效警察.Jikokeisatsu.S02EP01.1080p.mkv")
    assert r["name"] == "时效警察"
    assert r["year"] == 2006
    assert (r["season"], r["episode"]) == (2, None)


def test_year_season_folder_no_longer_season_polluted():
    # 生产行：43 库「Season 1950 S19」（猫和老鼠 S1950E01）
    r = _p("mount://3/nastool/动漫/猫和老鼠 (1940)/Season 1950/"
           "Tom and Jerry (1940) - S1950E01 - Little Quacker.mkv")
    assert r["name"] == "猫和老鼠"
    assert r["year"] == 1940
    assert "Season" not in _display(
        "mount://3/nastool/动漫/猫和老鼠 (1940)/Season 1950/"
        "Tom and Jerry (1940) - S1950E01 - Little Quacker.mkv")


def test_e_only_file_in_season_folder_uses_series_title():
    # 文件名只有集标记（E01）时，标题取剧集目录名，不取季目录名
    r = _p("/lib/某剧 (2020)/Season 01/E01.mkv")
    assert r["name"] == "某剧"
    assert (r["season"], r["episode"]) == (1, 1)


def test_bare_number_file_in_season_folder_uses_series_title():
    # 文件名只剩数字（01.mkv）时同样不取季目录名
    r = _p("/lib/某剧 (2020)/Season 01/01.mkv")
    assert r["name"] == "某剧"


# ==================== series 名推导：逐层跳季目录 ====================

def _sf(dir_rel):
    return ScanFile(stored_path="", name="", local_dir=None,
                    dir_rel=dir_rel, mount_id=4)


def test_series_name_skips_doubled_season_folders():
    # 生产行：series 2588859「Season 01」（老婆这周要出墙，Season 01/Season 01/）
    name = _series_name_of(
        _sf("video/剧集/日韩剧/老婆这周要出墙 (2016)/Season 01/Season 01"),
        {"name": "x", "year": 2016})
    assert name == "老婆这周要出墙"


def test_series_name_skips_year_season_folder():
    # 生产行：series 925121「Season 1960」（猫和老鼠，Season 1960/）
    name = _series_name_of(
        _sf("nastool/动漫/猫和老鼠 (1940)/Season 1960"),
        {"name": "x", "year": 1940})
    assert name == "猫和老鼠"


def test_series_name_skips_season_s_prefix_folder():
    # 「Season S01」同样是季目录，不当剧名
    name = _series_name_of(_sf("video/剧集/日韩剧/某剧 (2011)/Season S01"),
                           {"name": "x", "year": 2011})
    assert name == "某剧"


def test_series_name_normal_path_unchanged():
    name = _series_name_of(
        _sf("video/剧集/国产剧/一吻不定情 (2018)/Season 01"),
        {"name": "x", "year": 2018})
    assert name == "一吻不定情"


def test_series_name_falls_back_to_unknown_not_season():
    # 退无可退时宁可「未知剧集」，也不把季标记写成剧名
    name = _series_name_of(_sf("Season 01"), {"name": "Season 01"})
    assert name == "未知剧集"


def test_series_guid_skips_doubled_season_folders():
    # 同根：guid 也要逐层跳季目录，否则同一部剧会被拆成多个 series 行
    from backend.emby_server.scanner import _series_guid_of
    g1 = _series_guid_of(_sf(
        "video/剧集/日韩剧/老婆这周要出墙 (2016)/Season 01/Season 01"))
    g2 = _series_guid_of(_sf(
        "video/剧集/日韩剧/老婆这周要出墙 (2016)/Season 01"))
    g3 = _series_guid_of(_sf(
        "video/剧集/日韩剧/老婆这周要出墙 (2016)/Season 02"))
    assert g1 == g2 == g3


# ==================== 回填脚本纯函数 ====================

def test_backfill_helpers():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "backfill_season_names",
        os.path.join(os.path.dirname(__file__), "..",
                     "scripts", "backfill_season_names.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.is_polluted_series_name("Season 01")
    assert mod.is_polluted_series_name("Season 1960")
    assert not mod.is_polluted_series_name("一吻不定情")
    assert mod.is_polluted_episode_name("Season 01 S01")
    assert mod.is_polluted_episode_name("Season 1950 S19")
    assert not mod.is_polluted_episode_name("一吻不定情 S01")
    assert not mod.is_polluted_episode_name("第 1 集")
    # 文件名干净集标题
    assert mod.episode_title_from_filename(
        "mount://3/nastool/动漫/猫和老鼠 (1940)/Season 1950/"
        "Tom and Jerry (1940) - S1950E01 - Little Quacker.mkv") == "Little Quacker"
    assert mod.episode_title_from_filename("x/S02EP01.1080p.mkv") == ""
    # 无歧义 S/E 推导
    assert mod.derive_se_numbers("x/一吻不定情.S0101.1080p.mp4") == (1, 1)
    assert mod.derive_se_numbers("x/时效警察.S02EP01.mkv") == (2, 1)
    # 年季 S1950E01 有歧义，不推导
    assert mod.derive_se_numbers("x/Tom and Jerry - S1950E01 - Little Quacker.mkv") == (None, None)
    # 目录推导剧名（双季文件夹 + 年季）
    assert mod.series_name_from_child_path(
        "mount://4/video/剧集/日韩剧/老婆这周要出墙 (2016)/Season 01/Season 01/x.mp4"
    ) == "老婆这周要出墙"
    assert mod.series_name_from_child_path(
        "mount://3/nastool/动漫/猫和老鼠 (1940)/Season 1950/x.mkv"
    ) == "猫和老鼠"
    assert mod.series_name_from_child_path(
        "mount://4/video/剧集/日韩剧/某剧 (2011)/Season 01/x.mp4") == "某剧"

def test_is_season_like_name():
    for bad in ["Season 01", "Season 1", "Season 1950", "Season S01",
                "S01", "S1950", "第1季", "第一季", "01", "1"]:
        assert _is_season_like_name(bad), bad
    for good in ["一吻不定情", "猫和老鼠", "Frieren", "S01E01",
                 "Season 01 (2024)", "2012", "24kGoldn"]:
        assert not _is_season_like_name(good), good
