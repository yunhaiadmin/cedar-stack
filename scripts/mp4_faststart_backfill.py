#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量大 MP4 的 moov 前置脚本——入口已关停（2026-11-22）。

谷歌盘调用削减第一批第 5 项：生产复核零触发（三容器 env 无
MP4_FASTSTART_*、日志零命中、DB 无相关任务/配置）后关停本入口。
脚本现在无论 env 与 --apply 如何设置都只打印说明后退出，不建 DB
会话、不下载、不调用 ffmpeg、不改网盘、不改库。

本体逻辑（backend.emby_server.mp4_faststart 的 run_backfill 等）保留；
重启本入口须改代码走 PR，不许只翻 env 复活整文件下载/回写路径。
"""
from __future__ import annotations

import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="存量大 MP4 faststart。入口已关停，不改任何文件。")
    parser.add_argument(
        "--apply", action="store_true",
        help="（已关停，保留参数兼容旧调用）真正重写。")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="（已关停，保留参数兼容旧调用）最多处理多少条。")
    parser.parse_args(argv)
    print("mp4 faststart 存量入口已关停（2026-11-22），未改任何文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
