#!/usr/bin/env python3
"""前端 chunk 加载失败恢复契约检查：部署换包后旧页空白的兜底不许失效。

事故史：
  - 2026-10-13（#788/#796）：部署把全部 chunk 换了新哈希，用户旧构建懒加载
    旧 chunk → 404 → 页面打不开。初版兜底挂在 router.onError，判定正则只
    覆盖 Chrome/Firefox 的动态 import 失败文案。
  - 2026-11-13（用户 iPhone 实锤）：部署后旧标签页只剩壳、正文空白，兜底
    没触发——iOS Safari 对「chunk 已删、服务器 try_files 回了 index.html」
    报的是模块 MIME 错误（Failed to load module script: Expected a
    JavaScript module script but the server responded with a MIME type of
    "text/html"），不在原正则内；路由子 chunk 预加载失败不走 router.onError
    （走 vite:preloadError），视图内 defineAsyncComponent 失败则静默渲染空。

恢复实现只许一套：user_frontend/src/utils/chunkRecovery.ts。本检查钉住：
  1. 判定样本集齐全（Chrome 样本、Safari 旧样本、Safari MIME 样本、
     ChunkLoadError、Firefox MIME 样本）＋ sessionStorage 刷新预算键；
  2. 三处接线都在：router.onError 调统一恢复、main.ts 监听
     vite:preloadError 且接同一恢复、RootView 的 LandingView 异步组件
     带 loadingComponent（骨架）与 onError（接同一恢复）；
  3. 单一实现纪律：router/index.ts 不许再自带一份判定样本/预算键。

用法：python3 scripts/check_chunk_recovery.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UTIL = ROOT / "user_frontend" / "src" / "utils" / "chunkRecovery.ts"
ROUTER = ROOT / "user_frontend" / "src" / "router" / "index.ts"
MAIN = ROOT / "user_frontend" / "src" / "main.ts"
ROOT_VIEW = ROOT / "user_frontend" / "src" / "views" / "RootView.vue"
SKELETON = ROOT / "user_frontend" / "src" / "components" / "ui" / "LandingSkeleton.vue"

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
        failures.append(f"{label}: 不许再出现 '{needle}'（判定/计数只许 utils/chunkRecovery.ts 一套）")


def main() -> int:
    util = read(UTIL, "chunkRecovery")
    if util:
        # 统一入口与判定函数必须导出
        expect(util, "export function handleChunkLoadError", "chunkRecovery")
        expect(util, "export function isChunkLoadError", "chunkRecovery")
        expect(util, "export function resetChunkRecoveryState", "chunkRecovery")
        # 判定样本集：每条对应一类真实浏览器报错形态。只在 CHUNK_ERROR_RE
        # 定义段内查（文件头注释里也写了这些样本，光删正则不能靠注释蒙混过关）
        start = util.find("const CHUNK_ERROR_RE")
        end = util.find("/i", start) if start >= 0 else -1
        regex_block = util[start:end] if start >= 0 and end > start else ""
        if not regex_block:
            failures.append("chunkRecovery 判定样本: 找不到 CHUNK_ERROR_RE 定义段")
        for sample in (
            "Failed to fetch dynamically imported module",  # Chrome / Firefox
            "Importing a module script failed",  # Safari 旧版
            "Loading chunk",  # 旧打包器
            "ChunkLoadError",  # webpack 系
            "Failed to load module script",  # Safari MIME 形态（2026-11-13 主因）
            "Expected a JavaScript",  # Safari MIME 形态
            "MIME type of",  # Safari MIME 形态（text/html 上下文）
            "text\\/html",  # Safari MIME 形态（正则里是转义写法 text\/html）
            "disallowed MIME type",  # Firefox 模块 MIME 被拦
        ):
            expect(regex_block, sample, "chunkRecovery 判定样本")
        # 刷新预算：sessionStorage 计数键不许改名（跨版本标签页共用同一预算）
        expect(util, "spa-chunk-reload-count", "chunkRecovery 预算键")
        expect(util, "sessionStorage", "chunkRecovery 预算存储")
        # 防无限刷新两件套（2026-11-13 真渲染实测抓到无条件清零会让计数
        # 永远涨不到上限）：启动导航不清计数、手动重试强制清零
        expect(util, "firstSuccessResetSeen", "chunkRecovery 防循环")
        expect(util, "forceClearBudget", "chunkRecovery 防循环")

    router = read(ROUTER, "router/index.ts")
    if router:
        expect(router, "from '@/utils/chunkRecovery'", "router 接线")
        expect(router, "handleChunkLoadError(err)", "router 接线")
        expect(router, "resetChunkRecoveryState()", "router 接线")
        # 失败导航同样触发 afterEach：不清预算才不会把恢复计数误清成死循环
        expect(router, "if (!failure)", "router 防循环")
        # 单一实现纪律：判定样本与预算键不许在 router 里再存一份
        expect_absent(router, "Failed to fetch dynamically imported module", "router 单一实现")
        expect_absent(router, "spa-chunk-reload-count", "router 单一实现")

    main_ts = read(MAIN, "main.ts")
    if main_ts:
        expect(main_ts, "'vite:preloadError'", "main.ts preloadError 接线")
        expect(main_ts, "preventDefault()", "main.ts preloadError 接线")
        expect(main_ts, "handleChunkLoadError", "main.ts preloadError 接线")

    root_view = read(ROOT_VIEW, "RootView.vue")
    if root_view:
        expect(root_view, "loadingComponent", "RootView 异步组件")
        expect(root_view, "onError", "RootView 异步组件")
        expect(root_view, "handleChunkLoadError", "RootView 异步组件")
        expect(root_view, "LandingSkeleton", "RootView 异步组件")
    read(SKELETON, "LandingSkeleton.vue")

    print("=" * 60)
    if failures:
        print(f"❌ chunk 恢复契约检查失败（检查了 {checks} 项）")
        for item in failures:
            print(f"   - {item}")
        return 1

    print(f"✅ chunk 恢复契约检查通过（{checks} 项：判定样本＋三处接线＋单一实现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
