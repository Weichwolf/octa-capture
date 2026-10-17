#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target protocol_fuzz format-check
mkdir -p build/fuzz/corpus build/fuzz/artifacts
# Synthetic seeds only; no manufacturer assets or captured device state.
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/corpus')

def frame(command, address, payload):
    body = bytes(address) + bytes(payload)
    return bytes.fromhex('f0 41 10 00 00 4d') + bytes([command]) + body + bytes([-sum(body) % 128, 0xf7])

seeds = {
    'request': frame(0x11, [1, 0, 0, 0], [0, 0, 8, 0x7a]),
    'bulk': frame(0x12, [1, 0, 0, 0], [0] * 1146),
    'bulk-message': bytes([0x12, 1, 0, 0, 0]) + bytes(1146),
    'invalid-bulk-message': bytes([0x12, 1, 0, 0, 0]) + bytes([0xff] * 1146),
    'maximum': frame(0x12, [0x7f] * 4, [0x7f] * 65535),
    'unterminated': b'\xf0' + bytes(65549),
    'interrupted': bytes.fromhex('f0 00 90 f0 f8 f7'),
}
for parameter in range(13):
    seeds[f'write-{parameter}'] = frame(0x12, [0, 5, 0, parameter], [1])
    seeds[f'parameter-{parameter}'] = bytes([0x12, 0, 5, 7, parameter, 1])
    seeds[f'write-limit-{parameter}'] = bytes([0, 0, 5, 7, parameter, 0])
dsp = [
    ([0, 6, 0x39, 4], [8, 0, 0, 0]),
    ([0, 7, 0x28, 8], [2, 0, 0, 0, 0, 0]),
    ([0, 8, 0x31, 1], [2, 0, 0, 0, 0, 0]),
    ([0, 9, 0x20, 0], [1]),
    ([0, 8, 0x30, 0x0d], [1]),
    ([0, 6, 9, 0x0e], [0, 0, 0, 0, 0, 0]),
    ([0, 8, 0, 7], [2, 0, 0, 0, 0, 0]),
    ([0, 4, 4, 2], [44]),
    ([0, 3, 0, 4], [8]),
]
for index, (address, payload) in enumerate(dsp):
    seeds[f'dsp-frame-{index}'] = frame(0x12, address, payload)
    seeds[f'dsp-message-{index}'] = bytes([0x12] + address + payload)
typed = [
    [0, 0, 3, 9, 4, 0, 0, 0x80, 0],
    [0, 1, 2, 8, 8, 0, 0x20, 0, 0],
    [0, 0, 0, 0, 14, 0, 0x7f, 0xff, 0xff],
    [0, 1, 3, 1, 1, 0, 0x20, 0, 0],
    [0, 2, 4, 49, 0, 0, 0, 0, 0],
    [0, 4, 8, 0, 0, 0, 0, 0, 0],
]
for index, content in enumerate(typed):
    seeds[f'dsp-typed-{index}'] = bytes(content)
for name, content in seeds.items():
    (corpus / name).write_bytes(content)
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/protocol_fuzz build/fuzz/corpus \
    -artifact_prefix=build/fuzz/artifacts/ -max_len=131100 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
