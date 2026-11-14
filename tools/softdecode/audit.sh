#!/bin/bash
# Soft-decode license/build audit — all three checks must pass.
#
#  1) Configure summaries: every archived FFmpeg build (util + each decoder)
#     must be LGPL: config.h carries CONFIG_GPL 0, CONFIG_NONFREE 0 and
#     FFMPEG_LICENSE "LGPL version 3 or later", and the recorded
#     FFMPEG_CONFIGURATION must not contain --enable-gpl.
#     (This is the primary license evidence: GPL-ness is decided by the
#     build configuration, not by what strings happen to survive linking.)
#  2) Strings audit (auxiliary evidence): the shipped wasm files must not
#     contain markers of GPL/nonfree codec components or a GPL license text.
#  3) Whitelist: the shipped /wasm tree contains exactly the approved files
#     (decoder subset + resample + stretchpitch + license texts). Anything
#     else — above all anything under encode/ — fails the audit.
#
# Usage:
#   tools/softdecode/audit.sh [wasm_root] [summaries_dir]
# Defaults resolve relative to this repo. Exit code 0 = all checks pass.
set -u

REPO_ROOT=$(cd "$(dirname "$0")/../.."; pwd)
WASM_ROOT=${1:-$REPO_ROOT/user_frontend/public/wasm}
SUMMARIES=${2:-$REPO_ROOT/tools/softdecode/configure-summaries}

FAIL=0
fail() { echo "FAIL: $*"; FAIL=1; }
pass() { echo "PASS: $*"; }

echo "== check 1: configure summaries (LGPL assertions) =="
EXPECTED_SUMMARIES="util-simd decoder-hevc-simd decoder-h264-simd decoder-aac-simd decoder-ac3-simd decoder-eac3-simd decoder-dca-simd"
for label in $EXPECTED_SUMMARIES; do
  dir=$SUMMARIES/$label
  [ -f "$dir/config.h" ] && [ -f "$dir/config.mak" ] || { fail "$label: summary missing (config.h/config.mak)"; continue; }
  grep -q '^#define CONFIG_GPL 0$' "$dir/config.h" || fail "$label: CONFIG_GPL != 0"
  grep -q '^#define CONFIG_NONFREE 0$' "$dir/config.h" || fail "$label: CONFIG_NONFREE != 0"
  grep -q '^#define CONFIG_GPLV3 0$' "$dir/config.h" || fail "$label: CONFIG_GPLV3 != 0"
  grep -q '^#define FFMPEG_LICENSE "LGPL version 3 or later"$' "$dir/config.h" \
    || fail "$label: FFMPEG_LICENSE is not 'LGPL version 3 or later'"
  if grep '^FFMPEG_CONFIGURATION=' "$dir/config.mak" | grep -q -- '--enable-gpl'; then
    fail "$label: FFMPEG_CONFIGURATION contains --enable-gpl"
  fi
  # This subset links zero external libraries: any --enable-lib<name> in the
  # recorded configuration means an outside codec library crept in.
  if grep '^FFMPEG_CONFIGURATION=' "$dir/config.mak" | grep -qE -- '--enable-lib[a-z0-9]'; then
    fail "$label: FFMPEG_CONFIGURATION enables an external library"
  fi
  pass "$label"
done

echo "== check 2: strings audit on shipped wasm =="
# GPL/nonfree component markers and GPL license text. Note: FFmpeg's own
# license string in these files is "LGPL version 3 or later", which contains
# neither "GNU GENERAL PUBLIC LICENSE" (the LGPL name has LESSER in between)
# nor any of the component markers below.
# Deliberately NOT in this list: the bare string "libfaac". FFmpeg's native
# AAC decoder contains the format string "libfaac %d.%d" (aacdec.c) used to
# parse encoder-version metadata in AAC streams; it is not the libfaac
# encoder library. External libraries are excluded by check 1 instead,
# which asserts the recorded configure line enables no --enable-lib* at all.
FORBIDDEN='libx264
libx265
libxvid
libpostproc
libfdk-aac
libmp3lame
libopencore
--enable-gpl
GNU GENERAL PUBLIC LICENSE'
WASM_FILES="decode/hevc-simd.wasm decode/h264-simd.wasm decode/aac-simd.wasm decode/ac3-simd.wasm decode/eac3-simd.wasm decode/dca-simd.wasm resample/resample-simd.wasm stretchpitch/stretchpitch-simd.wasm"
for rel in $WASM_FILES; do
  f=$WASM_ROOT/$rel
  [ -s "$f" ] || { fail "$rel: missing or empty"; continue; }
  hits=$(strings -n 6 "$f" | grep -F -f <(printf '%s\n' "$FORBIDDEN") | head -5)
  if [ -n "$hits" ]; then
    fail "$rel: forbidden markers found: $hits"
  else
    pass "$rel"
  fi
done

echo "== check 3: shipped file whitelist =="
EXPECTED_FILES='COPYING.LGPLv3
THIRD-PARTY-LICENSES.txt
decode/aac-simd.wasm
decode/ac3-simd.wasm
decode/dca-simd.wasm
decode/eac3-simd.wasm
decode/h264-simd.wasm
decode/hevc-simd.wasm
resample/resample-simd.wasm
stretchpitch/stretchpitch-simd.wasm'
actual=$(cd "$WASM_ROOT" 2>/dev/null && find . -type f | sed 's|^\./||' | sort)
expected=$(printf '%s\n' "$EXPECTED_FILES" | sort)
if [ "$actual" = "$expected" ]; then
  pass "whitelist exact ($(printf '%s' "$actual" | wc -l) files)"
else
  fail "whitelist mismatch"
  diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") | sed 's/^/  /'
fi

echo
if [ "$FAIL" = 0 ]; then
  echo "AUDIT RESULT: ALL PASS"
else
  echo "AUDIT RESULT: FAILED"
fi
exit $FAIL
