#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz-clock.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target clock_fuzz format-check
mkdir -p build/fuzz/clock-corpus build/fuzz/clock-artifacts
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/clock-corpus')
for initial in range(4):
    for requested in range(4):
        for flags in (0, 1):
            packet = bytes([initial, requested, flags, 0, 0, 0, 0, 0, 0, 0, 0, 1])
            (corpus / f'rates-{initial}-{requested}-{flags}').write_bytes(packet)
for stage in range(1, 5):
    for result in range(13):
        (corpus / f'fault-{stage}-{result}').write_bytes(bytes([0, 1, 0, 0, 0, stage, result, 0, 0, 0, 0, 1]))
for count in (0, 39, 40, 41):
    (corpus / f'busy-ready-{count}').write_bytes(bytes([0, 1, 0, count, count, 0, 0, 0, 0, 0, 0, 1]))
for ready in (0, 1, 2, 255):
    (corpus / f'ready-{ready}').write_bytes(bytes([0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, ready]))
for rate in (0, 44101, 0xffffff):
    (corpus / f'unknown-{rate}').write_bytes(bytes([128, 1, 0, 0, 0, 0, 0, 2]) + rate.to_bytes(3, 'little') + bytes([1]))
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/clock_fuzz build/fuzz/clock-corpus \
    -artifact_prefix=build/fuzz/clock-artifacts/ -max_len=12 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
