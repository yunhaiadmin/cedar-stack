#!/usr/bin/env python3
"""网页播放器 EA 鉴权契约检查：网页播放的每个 EA 请求必须带账号卡下发的 Emby 专用 token。

事故史：
  - 2026-11-13（站长真机《龙门相》＋云浏览器《相亲相爱一家人》双实证）：
    片库点播进 /play 页必报「播放没能开始 / Invalid access token」，重试同样。
    根因：user_frontend/src/api/webPlayer.ts 直接用门户 axios 实例的
    api.post 打 EA 的 PlaybackInfo / Sessions 接口，只带了门户 JWT，
    没带 X-Emby-Token。EA 自 2026-10-25 删门户 JWT 回退后只认 Emby token，
    只带 JWT 的请求一律 401 Invalid access token。生产 EA 日志实证：
    同一会话里经 emby.ts 发起的浏览/详情请求全 200，webPlayer 的
    PlaybackInfo 连续 4 次 401——网页播放路径全坏，第三方客户端不受影响。

鉴权头只许一套实现：emby.ts 的 embyAuthConfig（拼 X-Emby-Token 头，
token 是账号卡下发的网页端专用 token，与图片 api_key 同一把）。
本检查钉住：
  1. emby.ts 导出 embyAuthConfig，且其实现里拼的是 X-Emby-Token；
  2. webPlayer.ts 从 '@/api/emby' 引入 embyAuthConfig；
  3. webPlayer.ts 里每个 api.post 调用（PlaybackInfo、Sessions/Playing、
     Sessions/Playing/Progress、Sessions/Playing/Stopped）都把
     embyAuthConfig() 作为配置参数传入；
  4. webPlayer.ts 不许自拼 X-Emby-Token 头（防第二套实现）：钉的是
     拼头动作本身（对象字面量 'X-Emby-Token':），注释里提及不算。

用法：python3 scripts/check_web_player_emby_auth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EMBY_TS = ROOT / "user_frontend" / "src" / "api" / "emby.ts"
WEB_PLAYER_TS = ROOT / "user_frontend" / "src" / "api" / "webPlayer.ts"

failures: list[str] = []
checks = 0


def read(path: Path, label: str) -> str:
    global checks
    checks += 1
    if not path.exists():
        failures.append(f"{label}: 文件不存在（{path.relative_to(ROOT)}）")
        return ""
    return path.read_text(encoding="utf-8")


def expect(text: str, needle: str, label: str) -> None:
    global checks
    checks += 1
    if needle not in text:
        failures.append(f"{label}: 缺少关键内容 '{needle}'")


def expect_absent(text: str, needle: str, label: str) -> None:
    global checks
    checks += 1
    if needle in text:
        failures.append(f"{label}: 不许出现 '{needle}'")


def main() -> int:
    emby = read(EMBY_TS, "emby.ts")
    player = read(WEB_PLAYER_TS, "webPlayer.ts")

    # 1. 单一实现：embyAuthConfig 在 emby.ts 定义并导出，拼 X-Emby-Token
    expect(emby, "export function embyAuthConfig", "emby.ts 导出 embyAuthConfig")
    expect(emby, "'X-Emby-Token': key", "embyAuthConfig 拼 X-Emby-Token 头")

    # 2. webPlayer 复用同一套实现
    expect(player, "embyAuthConfig", "webPlayer.ts 引用 embyAuthConfig")
    expect(player, "from '@/api/emby'", "webPlayer.ts 从 '@/api/emby' 引入")

    # 3. 每个 api.post 调用都带 embyAuthConfig()
    global checks
    posts = player.split("api.post")
    checks += 1
    if len(posts) < 5:  # 文件头 + PlaybackInfo + Sessions 三处，至少 4 个调用
        failures.append(
            f"webPlayer.ts: api.post 调用数异常（{len(posts) - 1}，预期 ≥4）")
    for idx, chunk in enumerate(posts[1:], start=1):
        checks += 1
        window = chunk[:400]
        if "embyAuthConfig()" not in window:
            failures.append(
                f"webPlayer.ts: 第 {idx} 个 api.post 调用未传 embyAuthConfig()"
                "（EA 只认 Emby token，只带门户 JWT 必 401 Invalid access token）")

    # 4. 不许第二套实现：文件里不许出现拼头动作（注释提及不在此列）
    expect_absent(player, "'X-Emby-Token':", "webPlayer.ts 不许自拼 X-Emby-Token 头")
    expect_absent(player, '"X-Emby-Token":', "webPlayer.ts 不许自拼 X-Emby-Token 头")

    if failures:
        print(f"FAIL 网页播放器 EA 鉴权契约（{checks} 项检查，{len(failures)} 项失败）：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK 网页播放器 EA 鉴权契约（{checks} 项检查全过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
