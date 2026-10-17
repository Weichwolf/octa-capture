#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
seconds=${1:-120}
if [[ ! $seconds =~ ^[1-9][0-9]{0,5}$ ]]; then
    echo 'Usage: tools/fuzz-midi.sh [seconds: 1..999999]' >&2
    exit 2
fi
cmake --preset fuzz
cmake --build --preset fuzz --target midi_fuzz format-check
mkdir -p build/fuzz/midi-corpus build/fuzz/midi-artifacts
python3 - <<'PY'
from pathlib import Path

corpus = Path('build/fuzz/midi-corpus')

def frame(address, payload):
    body = bytes(address) + bytes(payload)
    return bytes.fromhex('f0 41 10 00 00 4d 12') + body + bytes([-sum(body) % 128, 0xf7])

bulk = frame([1, 0, 0, 0], [0] * 1146)
meters = frame([0, 10, 0, 1], [0, 0, 127, 127] * 4)
seeds = {
    'bulk': bulk,
    'meters': meters,
    'interleaved': meters + bulk + meters,
    'typed-route': b'\0' + bulk + b'\0',
    'typed-route-mismatch': b'\0' + bulk + b'\1',
    'partial-bulk': bulk[:16],
    'maximum': frame([127] * 4, [127] * 65535),
    'unterminated': b'\xf0' + bytes(65549),
    'interrupted': bytes.fromhex('f0 00 90 f0 f8 f7'),
    'auto-sense': frame([0, 2, 1, 4], [1, 9]),
}
for name, content in seeds.items():
    (corpus / name).write_bytes(content)
PY
export ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
exec build/fuzz/midi_fuzz build/fuzz/midi-corpus \
    -artifact_prefix=build/fuzz/midi-artifacts/ -max_len=131100 \
    -timeout=5 -rss_limit_mb=1024 -max_total_time="$seconds" -print_final_stats=1
