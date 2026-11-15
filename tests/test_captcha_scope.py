"""人机验证已整体摘除（2026-11-15 用户亲令「把 cf 完整关了」，专指 CF 验证）。

原围栏（2026-11-06）钉的是「守卫只许接在网站登录/注册与后台登录」；验证摘除后
本文件翻转为摘除围栏：守卫本体、接线点、能力注册、前端挂件契约端点都不许回来——
谁把 captcha 守卫或 ``integrations.captcha`` 重新接进后端，这里先红。

播放器 / Emby 程序化登录做不了人机验证这条老口径继续有效：EA / Emby 协议侧
连引用都不许有（douban.py 里 "captcha page" 是豆瓣自家反爬页的识别文案，
与本站人机验证无关，不在扫描口径内——扫描只认守卫调用与模块导入）。
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

REPO = Path(__file__).resolve().parent.parent

_GUARD_CALL = re.compile(r"(?:integrations_)?captcha\.(?:guard|verify_request)\s*\(")
_CAPTCHA_IMPORT = re.compile(r"integrations(?:\.captcha| import captcha)")


def _py_files(*roots: str):
    for root in roots:
        base = REPO / root
        if base.exists():
            yield from base.rglob("*.py")


def test_captcha_module_and_wiring_fully_removed():
    # 守卫本体已删
    assert not (REPO / "backend/integrations/captcha.py").exists(), \
        "backend/integrations/captcha.py 又出现了：人机验证已于 2026-11-15 整体摘除"
    # 能力中心不再注册 captcha（系统设置→人机验证 整组随之消失）
    from backend import integrations

    assert "captcha" not in integrations.CAPABILITY_MODULES
    # 后端任何位置都不许再调用守卫或导入守卫模块
    offenders = {}
    for path in _py_files("backend", "emby_api"):
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _GUARD_CALL.search(text) or _CAPTCHA_IMPORT.search(text):
            offenders[rel] = "引用了已摘除的人机验证守卫"
    assert not offenders, f"人机验证守卫被重新接线：{offenders}"
    # 挂件信息端点（前端契约）已删：emby_portal 不许再有 /captcha 路由
    portal = (REPO / "backend/api/emby_portal.py").read_text(encoding="utf-8")
    assert '"/captcha"' not in portal and "'/captcha'" not in portal


def test_player_paths_do_not_reference_captcha_at_all():
    """EA / Emby 协议侧（emby_api、backend/emby_server）连引用都不许有。"""
    offenders = []
    for path in _py_files("emby_api", "backend/emby_server"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _CAPTCHA_IMPORT.search(text) or _GUARD_CALL.search(text):
            offenders.append(path.relative_to(REPO).as_posix())
    assert not offenders, f"播放器侧引用了人机验证：{offenders}"
