# 网页软解 · 批 1 构建记录（LGPL 解码器自编＋自托管）

本目录是网页播放器 WASM 软解码器的构建与审计工装。产物（解码子集 wasm）
落在 `user_frontend/public/wasm/` 下，按 `/wasm/decode/`、`/wasm/resample/`、
`/wasm/stretchpitch/` 的路径形态自托管；批 1 只做制备，**不接生产播放器**
（衔接是批 2，开关 `web_softdecode_enabled` 默认关）。

## 固定版本

| 组件 | 版本 |
|---|---|
| libmedia（构建脚本＋解码胶水代码来源） | v1.3.1（zhaohappy/libmedia，tag v1.3.1） |
| FFmpeg | release/7.1 @ `f9db090b14c255a8f1c5f8f41a4c92b723228763` |
| emsdk | 3.1.74 |
| 产物变体 | simd（文件名 `*-simd.wasm`，播放器按能力探测请求此变体） |

## 与上游构建脚本的差异（全部留痕）

`upstream-build/` 下脚本源自 libmedia v1.3.1 的 `build/`，与上游的差异只有：

1. **去掉 `--enable-gpl`**（保留 `--enable-version3`）：产物许可由 GPLv3
   变为 LGPLv3。这是本单的核心目的。
2. **丢弃上游私有令牌** `--enable-websimd128` / `--disable-websimd128` /
   `--disable-wasmatomic`：任何 stock FFmpeg 版本都不认识这些选项
   （configure 会 die_unknown）——上游作者用的是私有改版 FFmpeg。
   本单基线 release/7.1 接受 `--arch=webassembly`，但没有 simd128
   configure 特性；脚本原有的 `-msimd128 -fvectorize -fslp-vectorize`
   编译标志保留，编译器可证明时仍会生成 SIMD 代码。
3. `build-ffmpeg-*.sh` 的其余行（含 `--disable-postproc`）与上游逐字一致
   （构建时以 `git show v1.3.1:build/...` 做了 diff 核对）。

另有两处**构建区布局**适配（不改脚本）：libmedia 的 `lib/ffmpeg` 指向
`lib/ffmpeg-simd`（符号链接，脚本从 `lib/ffmpeg/include` 取头文件）；
`libmedia/node_modules` 需有 `@libmedia/common`、`commander@13`、`tsx`
（wasm-opt 工具的运行时依赖）。

### 为什么基线是 7.1 而不是 8.0

先按 8.0 构建过一轮：构建与许可审计全过，但复测时播放器在
`analyze stream` 阶段失败/崩溃。定位为 **AVFrame ABI 不兼容**：
libmedia 1.3.1 的结构体定义（packages/avutil/src/struct/avframe.ts）
是 FFmpeg 7.1 的 AVFrame 布局（43 个字段，含 key_frame、
interlaced_frame、top_field_first、palette_has_changed 等）；
FFmpeg 8.0 删除了这些字段（37 个），第 5 个字段起全部错位，
解码器写出的帧被播放器按旧布局读成乱码。AVCodecParameters 与
AVPacket 两版布局一致（已逐字段核对），差异只在 AVFrame。
故基线钉在 release/7.1——它也是其 AVFrame 与 libmedia 定义一致的
最新 stock 版本。

### wasm-opt --bss 修正

上游链接脚本末尾的 `wasm-opt --bss`（裁掉 data 段尾部 BSS 零区，
由 cheap 加载器按 dylink.0 的 memsize 补零）在 v1.3.1 里**静默失效**：
`packages/cheap/build/wasm-opt.cjs` 读 `command.i`/`command.o`，
而 commander 暴露的是 `command.input`/`command.output`，
工具直接以 0 退出且不写文件。不修的症状：音频解码器各带约 17 MB
零填充（libavutil 运行时填充的 `ff_tx_tab_*` 缓冲）。`build-all.sh`
会生成一份只改这两处属性名的副本（`wasm-opt-fixed.cjs`，逻辑与上游
逐字一致）并在每个链接步后真正执行它。

## 构建

```bash
# 构建区布局：$ROOT/{libmedia,FFmpeg,emsdk} 三个同级目录
# libmedia 的 build/ 先用本目录 upstream-build/ 的脚本覆盖
SOFTDECODE_BUILD_ROOT=$ROOT tools/softdecode/build-all.sh
```

产物在 `$ROOT/libmedia/dist/{decode,resample,stretchpitch}/*-simd.wasm`，
人工拷入 `user_frontend/public/wasm/` 对应目录。每个 FFmpeg 构建的
configure 结果（`config.h`＋`ffbuild/config.mak`）由 build-all 归档到
`configure-summaries/<target>-simd/`。

## 审计（tools/softdecode/audit.sh，三件套全绿才许交付）

1. **configure 摘要断言**：12 个归档构建逐一断言 `CONFIG_GPL 0`、
   `CONFIG_NONFREE 0`、`CONFIG_GPLV3 0`、
   `FFMPEG_LICENSE "LGPL version 3 or later"`，且记录的
   `FFMPEG_CONFIGURATION` 不含 `--enable-gpl`；其中 10 个零外部库
   构建不含任何 `--enable-lib*`，av1/vp9 两份各只精确放行自己的
   `--enable-libdav1d` / `--enable-libvpx`（BSD 库，见增量一节），
   多一个即失败。GPL 与否由构建配置决定，这是主证据。
2. **strings 审计**（辅助证据）：产物不得含 libx264/libx265/libxvid/
   libpostproc/libfdk-aac/libmp3lame/libopencore、`--enable-gpl`、
   `GNU GENERAL PUBLIC LICENSE` 字样。注意裸串 `libfaac` 是 FFmpeg
   原生 AAC 解码器解析编码器版本元数据的格式串（aacdec.c），
   不是组件，未列入。
3. **文件白名单**：`public/wasm/` 下只许白名单 15 个文件
   （11 解码器＋resample＋stretchpitch＋两份许可文本），
   多一个（尤其 `encode/` 类）即失败。

## 自托管口径（批 2 落地时执行）

- 路径：`/wasm/decode/<codec>-simd.wasm`、`/wasm/resample/resample-simd.wasm`、
  `/wasm/stretchpitch/stretchpitch-simd.wasm`，与 avplayer 的
  `getWasm`/`wasmBaseUrl` 取件形态一致。
- 缓存：产物内容随版本钉死，响应头应为
  `Cache-Control: public, max-age=31536000, immutable`
  （一期原型已实证：配此头后二次播放零下载）。批 1 只保证仓库内
  静态资源就位与本地静态服务可取，响应头配置随批 2 的服务配置落地。

## 增量一（2026-11-26 构建、2026-11-27 收尾）：补 AV1/VP9/Opus/FLAC/MP3

基线不变（libmedia v1.3.1、FFmpeg release/7.1、emsdk 3.1.74、只出
simd 变体）。新增五件解码器：opus/flac/mp3 是 FFmpeg 原生解码器
（零外部库，与批 1 同法逐 codec 构建）；av1/vp9 各链一个 BSD 外部
库——dav1d 1.5.1（BSD-2-Clause）与 libvpx v1.15.2（BSD-3-Clause），
源码克隆与 libmedia/FFmpeg 同级放在构建区，先编两库、产物
`lib/dav1d`、`lib/libvpx` 软链到各自的 `-simd` 变体（与
`lib/ffmpeg` 同法），再逐 codec 走
`build-ffmpeg-decoder.sh <codec> 1 0 0` → 归档摘要 →
`build-decoder.sh <codec> 1 0 0` → `wasm-opt --bss`。

### 本批适配留痕

1. `upstream-build/build-dav1d-lib.sh`（新增，源自上游同名脚本）：
   删 `-Denable_wasm_atomic` / `-Denable_wasm_simd`——stock
   dav1d 1.5.1 的 `meson_options.txt` 没有这两个选项（上游作者的
   私有改版 dav1d 才有），其余逐字一致。
2. `upstream-build/build-libvpx-lib.sh`（新增，源自上游同名脚本）：
   configure 改 `--target=generic-gnu`、删上游的
   `--cpu=i686-gnu`（wasm 目标无 i686 概念），其余逐字一致。
3. 构建区布局适配（不改脚本）：`dav1d.pc` 须软链进 emsdk
   sysroot 的 `lib/pkgconfig/`。emconfigure 会把
   `PKG_CONFIG_PATH` 清空、把 `PKG_CONFIG_LIBDIR` 钉死在 sysroot
   两处目录，外部导出的 `PKG_CONFIG_PATH` 对 FFmpeg configure
   不可见；FFmpeg 对 libdav1d 只有 pkg-config 一条探测路
   （libvpx 另有直链回退，故 vp9 不受此影响）。

### 事故留痕：av1 首轮冒名产物

首轮构建里 av1 的 configure 因上述 pkg-config 可见性问题失败
（`ERROR: dav1d >= 0.5.0 not found using pkg-config`），而
`build-ffmpeg-decoder.sh` 不因 configure 失败而失败——流水线沿用
上一轮（mp3）的配置，又产出一份 mp3 解码器冒名
`av1-simd.wasm`（与 mp3 成品 md5 相同、归档摘要也是 mp3 的），
驱动仍报 BUILD_ALL_DONE。交付前逐件 md5＋strings 核对抓出：
补上 `.pc` 软链后单独重编 av1（862,038 B，含 dav1d 符号，
摘要为 `--enable-decoder=libdav1d`＋`--enable-libdav1d`）。
教训钉死：构建脚本报 DONE 不算数，摘要与产物必须逐件验明身份。

### 前端放行（同批）

`user_frontend/src/utils/softDecode.ts`：`SOFT_VIDEO_CODECS` 加
av1/vp9、`SOFT_AUDIO_CODECS` 加 opus/flac/mp3、`CODEC_WASM_FILE`
加 167=vp9、225=av1、86017=mp3、86028=flac、86076=opus（ID 以
libmedia 的 AVCodecID 枚举为准，与 FFmpeg 7.1 `codec_id.h`
一致）。4K 护栏与 MSE 分流未动；TrueHD 两边都无软解件，仍不在
集内。

## 复测结果（2026-11-14，自编产物替换一期原型解码器，同脚本同口径）

原型三档样本（30 秒 testsrc2＋正弦音）：1080p HEVC Main＋AC3、
1080p HEVC Main10＋AC3、720p HEVC＋AC3，Chromium 156 headless。
三档均出画出声、播放器事件链完整、零错误、seek 后持续推进。

| 指标（冷启 A 档，test2 口径） | 一期官方 GPL 产物 | 本单自编 LGPL 产物 |
|---|---|---|
| 首帧（视频） | 1,487 ms | 1,371 ms |
| 首音 | 1,331 ms | 1,187 ms |
| 解码器下载总量 | 2,096,638 B | 2,142,547 B（+2.2%） |
| CPU（10 秒窗口） | 1.15 核 / 媒体推进 8.0 s | 1.09 核 / 媒体推进 8.4 s |
| seek | 通过 | 通过 |
| 缓存二次加载 | 0 字节 | 0 字节 |

B/C 档首帧与 A 档缓存轮同量级（3.2 s / 2.3 s，机器为 1.5 核共享
规格、抖动明显），无系统性退化。

## 许可

见 `user_frontend/public/wasm/THIRD-PARTY-LICENSES.txt` 与同目录
`COPYING.LGPLv3`。挂点建议（批 2 与播放器衔接一并做）：站内
「开源许可」页（可与设置/关于页合并）链到该文本与 LGPL 全文，
并注明解码器对应源码与本目录构建脚本的位置。
