"""播放可观测：单路径快照组装

文案口径随 Drive 直链开关（drive_direct_play_enabled）现算，不写死：
- 开关开：Drive 文件 302 到谷歌直链（播放器自拉，字节不经本机），
  本进程出流量恒接近 0；115/WebDAV/AList/rclone 挂载源暂不支持（410）。
- 开关关（2026-11-23 起生产现行）：播放走服务器中继，字节经本机，
  line_stats 的请求数 / 出流量即本进程中继实测。
面板展示：播放流量（本进程）：请求数 / 出流量字节。
（转码删除后不再有转码自拉路径。）
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.emby_server import line_stats, playback_strategy


def snapshot(db: Session) -> dict:
    """单路径完整快照（管理端一次拿全）"""
    counts = line_stats.snapshot()
    counter = counts[0] if counts else {}

    if playback_strategy.drive_direct_play_enabled(db):
        label = "302 直链"
        summary = ("Drive 文件 302 到谷歌直链（播放器自拉，字节不经本机）；"
                   "115 等挂载源暂不支持。")
        scope_note = ("流量计数只统计本进程（302 直链时代播放字节不经本机，"
                      "出流量恒接近 0）。")
    else:
        label = "服务器中继"
        summary = ("Drive 直链已关，播放走服务器中继（字节经本机转发）；"
                   "计数为本进程中继实测。")
        scope_note = ("流量计数只统计本进程（中继模式播放字节经本机，"
                      "出流量即中继实测）。")

    return {
        "path": "relay",
        "label": label,
        "summary": summary,
        "requests": int(counter.get("requests") or 0),
        "bytes_out": int(counter.get("bytes_out") or 0),
        "idle_seconds": counter.get("idle_seconds"),
        "uptime_seconds": line_stats.uptime_seconds(),
        "scope_note": scope_note,
        # 兼容老前端：lines 保留单元素（label 与顶层快照一致）
        "lines": [{
            "line": "relay",
            "label": label,
            "requests": int(counter.get("requests") or 0),
            "bytes_out": int(counter.get("bytes_out") or 0),
            "idle_seconds": counter.get("idle_seconds"),
        }],
        "total_users": 0,
        "default_line": "relay",
    }


def card_for(db: Session, line: str) -> Optional[dict]:
    """单路径卡片（兼容旧调用）"""
    snap = snapshot(db)
    for card in snap["lines"]:
        if card["line"] == line:
            return card
    return None


__all__ = ["card_for", "snapshot"]
