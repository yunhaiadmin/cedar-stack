# libmedia 播放器静态托管（勿手动改这里的文件）

这里是 `@libmedia/avplayer` **1.3.1** 的 `dist/esm` 原始构建产物
（入口 `1.3.1/avplayer.js` + 全部 `*.avplayer.js` 分片），由
`src/utils/softDecode.ts` 在软解起播时按 URL 动态加载。

为什么不走 npm 包 + vite 打包：该包是 webpack 构建，内部用
cheap 的函数序列化拼 Worker 启动代码；经 rollup 重新打包后
标识符被改名，Worker 启动即 `W is not defined`、load() 永远
不返回（2026-11-15 生产软解无声卡死的第一层根因）。原始产物
整套托管、运行时原样加载，是实测唯一稳妥的接法（与 /wasm 的
自托管口径一致）。

升级版本时：整目录替换为新版 `dist/esm/*.js`，目录名改成新
版本号，并同步改 `softDecode.ts` 的 `SOFT_PLAYER_SCRIPT_URL`
与 `tests/softDecode.test.mjs` 里的结构断言。
