#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz-bridge.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target bridge_fuzz format-check
mkdir -p build/fuzz/bridge-corpus build/fuzz/bridge-artifacts
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/bridge-corpus')
seeds = ['1 state', '2 meters 1', '3 preamp 0 4 100', '4 preamp 6 5 1',
         '5 mixer 0 0 0 8 2097152', '6 master 1 3 1 1 8388607',
         '7 reverb 2 4 49', '8 route 4 8', '9 preamp 256 4 1',
         '11 display 0', '12 display 3', '13 identify', '14 display 4', '15 identify 0',
         '16 monitor 0 1 16384 2097152', '17 monitor 127 0 32768 8388607',
         '18 monitor 10 1 0 0', '19 monitor 0 2 0 0', '20 monitor 0 1 32769 0',
         '4294967295 state', '4294967296 state', '1 state\x00']
for index, seed in enumerate(seeds):
    (corpus / f'request-{index}').write_bytes(seed.encode('ascii'))
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/bridge_fuzz build/fuzz/bridge-corpus \
    -artifact_prefix=build/fuzz/bridge-artifacts/ -max_len=1024 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
