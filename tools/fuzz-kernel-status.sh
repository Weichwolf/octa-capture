#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz-kernel-status.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target kernel_status_fuzz format-check
mkdir -p build/fuzz/kernel-status-corpus build/fuzz/kernel-status-artifacts
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/kernel-status-corpus')
base = bytearray([0, 1, 0x82, 3, 8, 0, 0, 0])
base += bytes([0, 0, 0, 0x80, 0xbb, 0, 0, 0, 0, 0]) * 4
for mask in range(16):
    case = base.copy()
    for index in range(4):
        case[8 + index * 10] = (mask >> index) & 1
    (corpus / f'phases-{mask}').write_bytes(case)
base[38] = 1
for stage in range(4):
    for result in range(8):
        case = base.copy()
        case[8 + stage * 10 + 9] = result
        (corpus / f'error-{stage}-{result}').write_bytes(case)
    for length in range(13):
        case = base.copy()
        case[8 + stage * 10 + 8] = length
        (corpus / f'length-{stage}-{length}').write_bytes(case)
for flags in (0, 1, 2, 4, 8, 16, 32, 64, 128):
    case = base.copy()
    case[0] = flags
    (corpus / f'lifecycle-{flags}').write_bytes(case)
for mode in range(4):
    case = base.copy()
    case[6] = mode
    (corpus / f'registration-{mode}').write_bytes(case)
for position, value in ((1, 0), (1, 255), (2, 2), (2, 0x83), (3, 2), (4, 7), (4, 9), (5, 255)):
    case = base.copy()
    case[position] = value
    (corpus / f'descriptor-{position}-{value}').write_bytes(case)
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/kernel_status_fuzz build/fuzz/kernel-status-corpus \
    -artifact_prefix=build/fuzz/kernel-status-artifacts/ -max_len=48 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
