# GUI transport and device bridge

`octa-bridge [hw:CTRL-port]` owns one C++ `MidiSession` on the exclusive CTRL
port. Meter reception, bulk queries and verified scalar writes share that
session on one thread. Audio streaming remains in the kernel/ALSA path.
EOF, SIGINT and SIGTERM release the session and attempt meter shutdown.
An unresolved device operation ends the process so the caller can reconnect.

`gui/backend.py` starts this process as a regular user, serializes requests,
matches replies by serial number and keeps only the latest meter frame.
Concurrent callers cannot cross replies. A closed or invalid peer fails pending
work and is reopened on the next request. Meter values are actual device data;
absence/staleness is represented explicitly.

## Private process protocol

Input is ASCII decimal fields separated by spaces/tabs, one request per line,
at most 512 bytes before the newline. Serial numbers are 1..4294967295.
All channel, pair, source and bank selectors are zero-based. Values are wire
indices/coefficients. No filenames, shell commands or arbitrary SysEx are accepted.

| Request after serial | Meaning |
| --- | --- |
| `state` | Fresh full bulk query |
| `meters ENABLED` | Input meter stream on/off, 0 or 1 |
| `preamp CHANNEL PARAMETER VALUE` | Preamp parameter 0..12 |
| `mixer SOURCE BUS CHANNEL PARAMETER VALUE` | Input=0/playback=1; typed mixer update |
| `master SOURCE BUS CHANNEL PARAMETER VALUE` | Typed master update |
| `reverb PARAMETER BANK VALUE` | Type=0/pre-delay=1/time=2 |
| `route PAIR SELECTION` | Physical output pair routing |

The C++ parser applies the same validated selectors/bounds as the existing
control APIs before any command write. It rejects extra fields, signed/fractional
values, integer overflow, non-ASCII digits and oversized input. The Python side
also rejects unknown JSON fields and booleans masquerading as integer values.

Output is newline-delimited JSON:

```json
{"event":"ready"}
{"id":1,"ok":true,"state":{"payload_hex":"...","preamps":[],"mixers":[]}}
{"event":"meters","input_levels":[0,0,0,0,0,0,0,0],"input_db":[-96,-96,-96,-96,-96,-96,-96,-96]}
{"id":2,"ok":true}
{"id":3,"ok":false,"code":-5,"error":"Device command failed."}
```

The state example is abbreviated; its actual schema is the complete `octa-state`
document. Typed write success includes a fresh full bulk reply, not an optimistic
local update. Notifications may precede a reply and never split its JSON document.
Serial zero denotes invalid protocol/device events rather than a successful reply.

This is a local stdin/stdout interface, not a network listener. The HTTP layer
below owns authentication and file operations. Interactive Auto-Sens and GTK
embedding remain separate integration work.

## Local HTTP panel

```bash
python3 tools/serve-gui.py --bin-dir build/release --open
# Optional: --port 0 --data-dir /private/recovery/directory --midi-port hw:...
```

`gui/server.py` binds only to IPv4 loopback. A random per-process bearer token is
passed in the launch URL fragment, removed from the visible address and retained
in that tab's session storage. Do not share the private link or server log.
Exact Host/Origin checks, no CORS, a local-only CSP and fixed asset paths restrict
browser access. Requests have bounded headers, bodies, connection lifetimes and
concurrency; duplicate JSON fields, non-finite numbers and unknown controls fail
before device writes. A regular browser keeps its normal sandbox enabled.

| Endpoint | Operation |
| --- | --- |
| `GET /api/state` | Full state, clock status, connection/busy/capability flags |
| `GET /api/meters` | Latest actual input frame, including staleness |
| `POST /api/control` | Validated scalar control and fresh device readback |
| `POST /api/action` | Validated initialization, routing, memory or rate action |
| `GET /api/preset` | Current Ver100 `.octa` download |
| `POST /api/preset` | Exact-size validated upload, snapshot, apply and readback |
| `POST /api/disconnect` | Release the panel's CTRL session |

API requests require the bearer token; mutations also require the exact local
Origin. Actions are serialized. Concurrent short polls do not falsely mark the
panel busy; bulk operations expose a transaction flag and retain the last state.
No client-supplied path or shell command is accepted. Export/import temporarily
release the bridge so the existing recovery-aware CLI owns CTRL exclusively.

Recovery files are created privately under `$XDG_STATE_HOME/octa-capture`
(default `~/.local/state/octa-capture`), or `--data-dir`. Keep them after an
interrupted operation. The bridge releases CTRL after an explicit disconnect,
server shutdown or 15 seconds without panel activity. Audio continues separately.

The independently authored web assets use the original parameter values and
reference-derived behavior, not extracted manufacturer images. Unimplemented
capabilities stay disabled; absent output meter data is not synthesized.

## Checks

The strict/release CTest suites include parser bounds, invalid JSON-state output,
browser-control validation and subprocess concurrency/cleanup tests. The fake peer
under `test/` is used only for offline transport tests and is never selected by
production code.

```bash
tools/fuzz-bridge.sh 120
python3 tools/check-bridge.py          # state/meter sharing; no audio parameter changes
python3 tools/check-bridge.py --write  # ten safe preamp changes, typed DSP and raw recovery
```

The hardware checker excludes phantom power/Hi-Z mutations, saves a new private
raw recovery snapshot before changing audio parameters and verifies complete
restoration plus release of exclusive CTRL access. As with the other checkers,
forced termination or device disconnection can prevent recovery.

The CTest suites also check HTTP security/concurrent-state behavior, JavaScript
lint and nonlinear parameter math. To check the running server in an isolated
Chromium profile (Debian packages `chromium` and `node-undici`):

```bash
umask 077
python3 tools/serve-gui.py --bin-dir build/strict > build/gui/server.log 2>&1
# In another terminal with ALSA access:
tools/check-gui.sh
tools/check-gui.sh --write
```

`--write` uses actual browser keyboard/pointer/file events, verifies hardware
readback and restores a fresh raw snapshot, then checks release of CTRL. The
checker saves private screenshots and recovery files under `build/gui/`.
`tools/verify-gui-parameters.py MAC-GUI WINDOWS-GUI [--cpu]` compares all fader
and compressor tables; `--cpu` additionally executes original macOS inverse-fader
and compressor-graph functions with Unicorn. Manufacturer inputs remain local.
