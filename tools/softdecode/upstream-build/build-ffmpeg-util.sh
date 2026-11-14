#!/bin/bash
#
# Modified by Aetrix (2026-11-14, softdecode batch-1) from libmedia v1.3.1 upstream:
#   1) removed "--enable-gpl" from the FFmpeg configure line (kept --enable-version3)
#      so the resulting FFmpeg build is LGPLv3 instead of GPLv3;
#   2) dropped the upstream-only tokens --enable-websimd128 /
#      --disable-websimd128 / --disable-wasmatomic: no stock FFmpeg release
#      knows them (the upstream author builds against a private FFmpeg fork).
#      Our pinned base is FFmpeg release/7.1, whose AVFrame layout matches
#      libmedia 1.3.1's struct definitions (FFmpeg 8.0 removed AVFrame fields
#      such as key_frame and is ABI-incompatible with the player glue).
#      release/7.1 accepts --arch=webassembly but has no simd128 configure
#      feature; the -msimd128/-fvectorize flags in EXTRA_CFLAGS stay, so
#      autovectorization still applies where the compiler can prove it.
# No other lines differ from upstream (verified by diff at build time).

echo "===== start build ffmpeg-emcc ====="

simd=$1
atomic=$2
wasm64=$3

NOW_PATH=$(cd $(dirname $0); pwd)

PROJECT_ROOT_PATH=$(cd $NOW_PATH/../; pwd)

FFMPEG_PATH=$(cd $PROJECT_ROOT_PATH/../FFmpeg; pwd)
EMSDK_PATH=$(cd $PROJECT_ROOT_PATH/../emsdk; pwd)

source $EMSDK_PATH/emsdk_env.sh

dir=$PROJECT_ROOT_PATH/lib/ffmpeg

EXTRA_CFLAGS="-I$PROJECT_ROOT_PATH/packages/cheap/include -O3"

COMPONENTS=""

if [[ $wasm64 == "1" ]]; then
  COMPONENTS="$COMPONENTS"
  EXTRA_CFLAGS="$EXTRA_CFLAGS -msimd128 -fvectorize -fslp-vectorize -pthread -mbulk-memory"
  dir="$dir-64"
else
  if [[ $simd == "1" ]]; then
    COMPONENTS="$COMPONENTS"
    EXTRA_CFLAGS="$EXTRA_CFLAGS -msimd128 -fvectorize -fslp-vectorize -pthread -mbulk-memory"
    dir="$dir-simd"
  else
    COMPONENTS="$COMPONENTS"
    if [[ $atomic == "1" ]]; then
      COMPONENTS="$COMPONENTS"
      EXTRA_CFLAGS="$EXTRA_CFLAGS -pthread -mbulk-memory"
      dir="$dir-atomic"
    else
      COMPONENTS="$COMPONENTS"
      EXTRA_CFLAGS="$EXTRA_CFLAGS -mno-bulk-memory -no-pthread -mno-sign-ext"
    fi
  fi
fi

rm -rf $dir

mkdir $dir

cd $FFMPEG_PATH

emmake make clean

emconfigure ./configure \
  --prefix=$dir \
  --cc="emcc" \
  --cxx="em++" \
  --ar="emar" \
  --ranlib="emranlib" \
  --cpu=generic \
  --target-os=none \
  --arch=webassembly \
  --enable-version3 \
  --enable-cross-compile \
  --disable-logging \
  --disable-programs \
  --disable-ffmpeg \
  --disable-ffplay \
  --disable-ffprobe \
  --disable-doc \
  --disable-postproc  \
  --disable-avfilter \
  --enable-pthreads \
  --disable-w32threads \
  --disable-os2threads \
  --disable-runtime-cpudetect \
  --disable-network \
  --disable-everything \
  --disable-protocols \
  --disable-devices \
  --disable-filters \
  --enable-asm \
  --disable-debug \
  --disable-avdevice \
  --enable-pic \
  --extra-cflags="$EXTRA_CFLAGS" \
  $COMPONENTS

emmake make

emmake make install

emmake make clean

echo "===== build ffmpeg-emcc finished  ====="