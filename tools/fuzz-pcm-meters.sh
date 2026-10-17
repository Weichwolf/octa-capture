#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz-pcm-meters.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target pcm_meter_fuzz format-check
mkdir -p build/fuzz/pcm-meter-corpus build/fuzz/pcm-meter-artifacts
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/pcm-meter-corpus')
for rate in (44100, 48000, 96000, 192000):
    packet = bytearray(b'OCPK\x01\x00\x88\x00' + bytes(128))
    for offset, value in ((8, 15), (16, rate), (20, rate),
                          (24, 4 if rate == 192000 else 12),
                          (28, 4 if rate == 192000 else 10), (48, 0x7fffff00), (96, 256)):
        packet[offset:offset + 4] = value.to_bytes(4, 'little')
    (corpus / f'rate-{rate}').write_bytes(packet)
    for length in (0, 4, 8, 135):
        (corpus / f'truncated-{rate}-{length}').write_bytes(packet[:length])
unseen = bytearray(b'OCPK\x01\x00\x88\x00' + bytes(128))
unseen[32:40] = bytes([255] * 8)
(corpus / 'unseen').write_bytes(unseen)
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/pcm_meter_fuzz build/fuzz/pcm-meter-corpus \
    -artifact_prefix=build/fuzz/pcm-meter-artifacts/ -max_len=256 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
