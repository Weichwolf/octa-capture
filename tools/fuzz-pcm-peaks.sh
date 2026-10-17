#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz-pcm-peaks.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target pcm_peak_fuzz format-check
mkdir -p build/fuzz/pcm-peaks-corpus build/fuzz/pcm-peaks-artifacts
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/pcm-peaks-corpus')
for channels in (0, 1, 4, 10, 12, 13, 255):
    samples = [0, 1, 0x7fffff, 0x800000, 0xffffff]
    packet = b''.join(sample.to_bytes(3, 'little') * channels for sample in samples)
    (corpus / f'channels-{channels}').write_bytes(bytes([channels]) + packet)
    (corpus / f'partial-{channels}').write_bytes(bytes([channels]) + packet + b'\0')
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/pcm_peak_fuzz build/fuzz/pcm-peaks-corpus \
    -artifact_prefix=build/fuzz/pcm-peaks-artifacts/ -max_len=16384 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
