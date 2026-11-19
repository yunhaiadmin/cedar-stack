"""官网落地页文案能力（用户端未登录首页 LandingView）。

落地页的首屏文案、卖点、合作方名单等全部营销文本原先写死在
user_frontend 的 LandingView.vue 里，改一个字都要发一版前端。
现改为 SystemConfig 单键 ``landing_content``（家族 landing，整份
JSON，结构校验在 config_schema 的 coerce_landing_content），
管理后台「官网内容」页编辑，公开端点 /api/site/landing 下发，
前端只做数据驱动渲染。

读取口径：
- 存了 landing_content 且结构可用 → 以它为准（管理员全权接管）；
- 没存 / 存量坏 JSON → 回落 LANDING_DEFAULT_CONTENT，即现行写死
  文案的逐字转写（一字不丢）；单个字段坏掉时 coerce 读侧回落
  该字段的默认文案，不让一条坏配置让官网开天窗。

不进配置的（版式与口径，见 config_schema 注释）：卖点三卡的栅格
与插图/数据行位置、卡内设备示意与时间轴标注、片库数据行（走
/api/site/library-stats 实时统计）、页脚免责声明、Hero 背景图。
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from sqlalchemy.orm import Session

from backend import config_schema
from backend.integrations import store

logger = logging.getLogger(__name__)

KEY = "landing_content"


def default_config() -> dict[str, Any]:
    """未存配置时的默认整份文案 = 现行写死文案（深拷贝，调用方
    可自由改动返回值）。"""
    return copy.deepcopy(config_schema.LANDING_DEFAULT_CONTENT)


def has_custom(db: Session) -> bool:
    """是否存过自定义文案（管理端展示「已自定义/使用默认」用）。"""
    raw = store.read_values(db, [KEY], {KEY: ""}).get(KEY) or ""
    return bool(str(raw).strip())


def read_config(db: Session) -> dict[str, Any]:
    """读整份落地页文案（管理端 GET 与公开下发共用）：存量行结构
    可用则以它为准，否则回落默认。直查 DB（与 site_drawer /
    branding 能力同口径），保存经 store.write_values 写时失效，
    不存在跨进程缓存延迟。"""
    raw = store.read_values(db, [KEY], {KEY: ""}).get(KEY) or ""
    if str(raw).strip():
        coerced = config_schema.coerce_landing_content(raw, strict=False)
        if coerced is not None:
            return coerced
        logger.warning("landing_content 存量值不可用，已回落默认文案")
    return default_config()


def public_info(db: Session) -> dict[str, Any]:
    """公开下发形状：整份文案（结构恒完整，前端直接渲染）。"""
    return read_config(db)
