#!/bin/bash
# Soft-decode batch-1 orchestrator: builds the self-hosted LGPL decoder subset
# with the patched libmedia v1.3.1 build scripts in tools/softdecode/upstream-build/.
#
# Expected build-area layout (all siblings under $SOFTDECODE_BUILD_ROOT):
#   libmedia   libmedia v1.3.1 checkout (zhaohappy/libmedia @ v1.3.1);
#              its build/ directory overlaid with this repo's
#              tools/softdecode/upstream-build/* (the patched scripts win)
#   FFmpeg     FFmpeg release/7.1 @ f9db090b14c255a8f1c5f8f41a4c92b723228763
#              (7.1 is the newest stock FFmpeg whose AVFrame layout matches
#              libmedia 1.3.1's struct definitions; 8.0 removed AVFrame
#              fields and is ABI-incompatible with the player glue)
#   emsdk      emsdk 3.1.74 (emsdk_env.sh is sourced by the upstream scripts)
#
# Usage:
#   SOFTDECODE_BUILD_ROOT=<dir> tools/softdecode/build-all.sh
#   SKIP_UTIL=1 SOFTDECODE_BUILD_ROOT=<dir> tools/softdecode/build-all.sh
#
# Outputs:
#   $BUILD_ROOT/libmedia/dist/{decode,resample,stretchpitch}/*-simd.wasm
#   tools/softdecode/configure-summaries/<target>-simd/{config.h,config.mak}
#     (archived FFmpeg configure results, consumed by tools/softdecode/audit.sh)
set -euo pipefail

BUILD_ROOT=${SOFTDECODE_BUILD_ROOT:?set SOFTDECODE_BUILD_ROOT}
REPO_ROOT=$(cd "$(dirname "$0")/../.."; pwd)
LM=$BUILD_ROOT/libmedia
SUMDIR=$REPO_ROOT/tools/softdecode/configure-summaries
export MAKEFLAGS=${MAKEFLAGS:--j3}

archive_summary() { # $1 = label
  mkdir -p "$SUMDIR/$1"
  cp "$BUILD_ROOT/FFmpeg/config.h" "$SUMDIR/$1/config.h"
  cp "$BUILD_ROOT/FFmpeg/ffbuild/config.mak" "$SUMDIR/$1/config.mak"
}

cd "$LM"

# The upstream link scripts end with a `wasm-opt --bss` pass that trims the
# trailing BSS zeros out of each module's data segment (the cheap loader
# re-zero-fills it from the dylink.0 memory size). In libmedia v1.3.1 that
# pass silently never runs: packages/cheap/build/wasm-opt.cjs checks
# `command.i`/`command.o`, but commander exposes the options as
# `command.input`/`command.output`, so the optimizer exits without writing
# anything (symptom: audio decoders ship ~17 MB of zero padding from
# libavutil's runtime-filled ff_tx_tab_* buffers). We therefore regenerate
# a copy of the tool with the two property names fixed and run it after
# every link step. The tool's logic is otherwise byte-identical upstream.
WASM_OPT_FIXED=$BUILD_ROOT/wasm-opt-fixed.cjs
sed 's/command\.i\b/command.input/g; s/command\.o\b/command.output/g' \
  "$LM/packages/cheap/build/wasm-opt.cjs" > "$WASM_OPT_FIXED"
export NODE_PATH=$LM/node_modules

bss_trim() { # $1 = wasm file
  node "$WASM_OPT_FIXED" -i "$1" --bss -o "$1"
}

if [ "${SKIP_UTIL:-0}" != "1" ]; then
  bash build/build-ffmpeg-util.sh 1 0 0
  archive_summary util-simd
fi

# Decoder subset (video: hevc, h264; audio: aac, ac3, eac3, dca [= DTS]).
# Names are libmedia's dist names; "dca" is FFmpeg's DTS decoder name.
for codec in hevc h264 aac ac3 eac3 dca; do
  bash build/build-ffmpeg-decoder.sh "$codec" 1 0 0
  archive_summary "decoder-$codec-simd"
  bash build/build-decoder.sh "$codec" 1 0 0
  bss_trim "$LM/dist/decode/$codec-simd.wasm"
done

bash build/build-resample.sh 1 0 0
bss_trim "$LM/dist/resample/resample-simd.wasm"
bash build/build-stretchpitch.sh 1 0 0
bss_trim "$LM/dist/stretchpitch/stretchpitch-simd.wasm"

echo "== build-all done =="
ls -la "$LM/dist/decode" "$LM/dist/resample" "$LM/dist/stretchpitch"
