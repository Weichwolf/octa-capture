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
| `display BUS` | Select device LCD mixer A=0 through D=3; B–D unavailable at 192 kHz |
| `identify` | Send the original device identification/blink command |

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
below owns authentication, file operations and interactive Auto-Sens. The GTK host
uses the same server and web assets.

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
Input reception has a five-second absolute deadline and a cumulative 16 KiB cap;
slow partial headers/bodies cannot renew it. Completed headers are limited to
8 KiB and bodies to 4 KiB. A fully received request can still await its bounded
device operation without being cut off by the input deadline.

| Endpoint | Operation |
| --- | --- |
| `GET /api/state` | Full state, clock status, connection/busy/capability flags |
| `GET /api/meters` | Latest actual input frame, including staleness |
| `GET /api/devices` | Present OCTA-CAPTURE cards and selected identity; works while disconnected |
| `POST /api/device` | Select a discovered identity and reopen its CTRL session |
| `GET /api/preferences` | Read control-panel preferences without opening a device |
| `POST /api/preferences` | Store the validated audio-port warning preference |
| `POST /api/control` | Validated scalar control and fresh device readback |
| `POST /api/action` | Validated initialization, routing, memory or rate action |
| `POST /api/auto-sense/start` | Start selected-input calibration with a private recovery snapshot |
| `POST /api/auto-sense/stop` | Finish or cancel the active calibration |
| `GET /api/preset` | Current Ver100 `.octa` download |
| `POST /api/preset` | Exact-size validated upload, snapshot, apply and readback |
| `POST /api/disconnect` | Release the panel's CTRL session |

API requests require the bearer token; mutations also require the exact local
Origin. Actions are serialized. Concurrent short polls do not falsely mark the
panel busy; bulk operations expose a transaction flag and retain the last state.
No client-supplied path or shell command is accepted. Export/import temporarily
release the bridge so the existing recovery-aware CLI owns CTRL exclusively.

Panel commands use the same endpoint: `{"kind":"display","bus":1}` or
`{"kind":"identify"}`. Their fresh bulk reply verifies preservation of audio
settings, not physical LCD selection or blinking. Clicking the device name sends
identification; selecting a different visible mixer tab sends LCD selection.

Right-click the device name, or use its Context Menu key/Shift+F10, to select a
unit. While disconnected, the visible Select OCTA-CAPTURE button opens that menu.
Selection accepts only `{"id":"serial:…"}` returned by discovery, never a
client-supplied ALSA port or path. Discovery checks USB `0582:0120` through sysfs
without claiming USB interfaces. Serial identities survive ALSA card renumbering;
devices without a serial use their USB bus path. Before subsequent live operations,
the backend resolves the selected identity again and refuses an absent unit rather
than following another unit at its previous card number. Auto-Sens must finish or
be cancelled before changing units. Concurrent controls use the same operation lock.

The Multiple Direct Mixer switch follows both original handlers: enable assigns
output routes `[0,1,2,3,0]`; disable initializes Patch Bay and selects Mixer A.
Its lamp and B–D tab visibility derive from actual B–D output routes. It is hidden
in the preamp view and at 192 kHz. Both directions ask for confirmation.
Initialize and context menus have keyboard-accessible submenus. Mixer level/send
All Ch. affects ten strips; pan offers alternating Left/Right or Center. Compressor
Bypass Off All Ch. enables compression; Bypass On All Ch. disables it.

Recovery files are created privately under `$XDG_STATE_HOME/octa-capture`
(default `~/.local/state/octa-capture`), or `--data-dir`. Keep them after an
interrupted operation. The bridge releases CTRL after an explicit disconnect,
server shutdown or 15 seconds without panel activity. Active Auto-Sens is
cancelled on disconnect, shutdown or client inactivity. Audio continues separately.

Auto-Sens runs in an exclusive background C++ process. Its prepared notification
acknowledges installed signal handlers before the server sends finish/cancel;
its running notification requires actual selected-channel device activity.
SIGUSR1 finishes early, while SIGINT/SIGTERM cancel with recovery. Completion
still requires device notifications and verified state bounds. A cancellation is
reported as successful only when the CLI confirms unchanged state or verified
raw restoration. The 60-second maximum prevents an abandoned calibration from
running indefinitely. Other controls stay disabled while calibration is active.

The independently authored web assets use the original parameter values and
reference-derived behavior, not extracted manufacturer images. Unimplemented
capabilities stay disabled; absent output meter data is not synthesized.

Sensitivity value fields support double-click editing (Enter/F2 for keyboard
access), Enter/focus-loss commit and Escape cancellation. Four-character decimal
values in 0..50 dB truncate to half-dB indices, matching Windows `0x140003940`
and macOS `CValPreSens::CheckValue`. Polling preserves active edits; invalid
decimal values restore the current display without a device write.

The bridge additionally emits `pcm-meters` events with validated ALSA capture
and playback peaks, rate/channel counts, sequences and ages. The Python cache
retains independent MIDI/PCM receive times. The web panel uses the sources and
compressor enable gating described in [PCM meters](pcm-meters.md), and blanks
stale or rate-mismatched data. Older modules without these controls retain MIDI
operation; their PCM meters remain unavailable.

The meter response also contains up to 64 validated notifications with monotonic
IDs and individual ages. Pages consume each notification once; latest-value
replacement cannot discard a short peak. The local display advances reference
fall, peak-hold and clip-hold at 30 ms, independently of state queries.

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
tools/check-gui.sh --write --rates  # additionally change all four hardware rates
tools/check-gui.sh --write --matrix  # safe preamp/mixer scalars through browser events
tools/check-gui.sh --write --reload-module  # WSL development helper required
```

`--write` uses actual browser keyboard/pointer/file events, verifies hardware
readback, multiple-mixer routing, panel commands, ten-channel context actions and
Auto-Sens finish/cancel, and restores fresh raw snapshots, then checks release of CTRL. The
checker saves private screenshots and recovery files under `build/gui/`.
`--rates` also checks immediate sample-rate selection, Cancel/Close semantics, kernel/bulk rate agreement,
channel availability, 192 kHz menu/dialog restrictions and PCM meter recovery,
then restores the original rate. The Initialize submenu stays available at 192
kHz, as in both reference dispatchers. WSL device tests pass at all four rates;
native Debian and physical signal quality remain separate requirements.

`--matrix` additionally checks 427 actual browser changes: every preamp knob,
safe phase/low-cut/compressor switch, strip pan/volume/mute/solo on all four buses,
master volumes and Mixer A send/return controls. Each change requires the correct
acknowledged request, fresh hardware readback and exact-value restoration. Phantom
power, Hi-Z, link/mono changes and persistent memory are outside this matrix.

Sample-rate selection takes effect immediately, as in Windows handler
`0x14002c2f0` (IOCTL `0x222328`); Cancel does not undo that device operation.
Cancel/Escape keep Driver Settings open and reset staged host fields; Close ends
the dialog. Linux audio applications own their buffers, so the Windows host-policy
Apply/Default buttons stay disabled. No inactive slider silently controls another
application's buffer.
The Driver Settings unit selector uses the same discovered, serial-bound device
identities as the panel selector; it is unavailable during pending device writes.

`--reload-module` uses the already provisioned `sudo -n octa-capture-dev` helper
at `/usr/local/sbin/octa-capture-dev` to unload and reinstall this module. It
checks the absent-card page/menu, automatic same-unit reconnection, real MIDI/PCM
meters and full raw preservation. It does not detach/reset USB or install the
helper. This option is specific to the development setup; physical unplug and
native Debian lifecycle tests remain separate. Module installation runs in a
finally block after successful unload.

Driver → Show README opens an independently written local usage guide. It loads
no external content and is available in both the browser and GTK host.

The signal-flow dialog uses our own SVG drawing of the reference topology
(Windows bitmap 246 and macOS `PNL_DIAGRAM.bmp` agree). Dialog bodies scroll
independently of their footers; Chromium and WebKit checks verify visible,
clickable footer buttons at the desktop host's 978×408 viewport.

The original 192 kHz audio-port warning appears once on entering that rate,
including an initial connection at 192 kHz. Other open dialogs defer it. The
warning uses the original macOS counts, four inputs/four outputs. About's Show
warning checkbox and the warning's Do not show again checkbox are inverse views
of one preference. Reenabling it at the same rate does not repeat the warning.
All 160 transition cases match original `CDispFreq::CheckFreq` (`0x100032042`)
execution; `tools/verify-port-warning.py MAC-GUI` reproduces the reference check.
The browser hardware rate checker exercises the actual warning and both checkboxes.

`{"show_port_warning":true}` is the complete preference schema. It is stored
atomically as private `preferences.json` under the configured data directory;
invalid input is rejected before file creation. It works without a device and
does not change audio settings. Storage failure keeps the prior file and value.
`tools/verify-gui-parameters.py MAC-GUI WINDOWS-GUI [--cpu]` compares all fader
and compressor tables; `--cpu` additionally executes original macOS inverse-fader
and compressor-graph functions with Unicorn. Manufacturer inputs remain local.

Compressor graphs use the original filled five-vertex polygon and three/four-point
line, including the gate step, five grid intervals and separate enabled/bypassed
palettes. Both binaries agree on fill/line colors and the 1.8-pixel pen width;
1,440 geometry/style cases match original Mac drawing execution. Optional
`tools/verify-compressor-render.py MAC-GUI WINDOWS-GUI --resources MAC-APP-RESOURCES`
reproduces that check with Unicorn and ImageMagick. The web renderer draws its
own grid and shadow; no manufacturer artwork is shipped. Browser/GTK checks
also inspect actual rendered pixels.

Reverb pre-delay/time use read-only text and clamped arrow controls, matching
Windows edit style `0x50800882` and both original 0..12/0..49 index ranges.
All 13 pre-delay strings and 50 time displays agree with both binaries. Keyboard,
pointer, endpoint bounds and all four effect-bank writes/readbacks are checked in
the actual browser. Offline checks verify autorepeat cancellation on release,
focus loss, hidden pages and disabling; pending readback cannot reverse local steps.

## GTK4/WebKitGTK host

`python3 tools/desktop-gui.py` starts a fixed-size desktop window and a private
loopback server on an automatically selected port. Optional arguments are
`--bin-dir`, `--midi-port` and `--data-dir`, as for the HTTP launcher.
Debian 13 packages are `python3-gi`, `gir1.2-gtk-4.0` and `gir1.2-webkit-6.0`;
GTK 4.10 or later is required for the native asynchronous file dialog.

The WebKit network session is ephemeral, with no system proxy, external
navigation, pop-up windows or web permissions. The web-process sandbox stays
enabled. WebGL/media are disabled and software rendering keeps this small 2D
panel usable without a GPU. Only the panel's own blob downloads reach the native
Save dialog. WebKitGTK 6.0 receives a filesystem destination path after user
selection/overwrite confirmation. Exit and window/server shutdown release CTRL;
the same Auto-Sens recovery lifecycle applies.

The native integration checker uses real X11 key events for controls and Ctrl+S,
validates the native Save download, checks live controls/meters, blocks an
external navigation and exercises Driver → Exit. It verifies independent full
state readback after releasing CTRL. For headless checks, install `xvfb`, `xauth`,
`xdotool`, `dbus`, and `imagemagick`:

```bash
GSK_RENDERER=cairo GSETTINGS_BACKEND=memory xvfb-run -a dbus-run-session -- python3 tools/check-gtk.py
GSK_RENDERER=cairo GSETTINGS_BACKEND=memory xvfb-run -a dbus-run-session -- python3 tools/check-gtk.py --write
```

`--write` also changes/restores sensitivity and opens the newly saved file with
Ctrl+O and an actual native-chooser double-click. It checks the exact selected
path before import confirmation and compares hardware readback with the prepared
preset, including the documented 192 kHz header behavior. Preset import can reset
fields omitted by the original file format; the checker finally restores a fresh
complete raw snapshot. Screenshots and recovery files remain private under `build/gui/`.
These are WSL/Xvfb desktop integration checks, not native Debian audio or physical
signal-quality verification. The remaining feature gaps still apply to both hosts.

`cmake --install build/release --prefix PREFIX` installs the runtime independently
of the checkout, with `PREFIX/bin/octa-capture` and `octa-capture-http`. Native
tools share that directory; Python/web assets live under `libexec/octa-capture`.
Both launchers resolve their paths relative to the installation. The offline
`gui-install` check uses a prefix with spaces and an unrelated working directory.
To run the GTK hardware checker against extracted package files, add
`--installed-prefix PATH/TO/usr` to `tools/check-gtk.py`.
