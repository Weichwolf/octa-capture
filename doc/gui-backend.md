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

Startup and reconnection read a fresh bulk state before showing device controls.
The device owns preamp, mixer, routing and clock settings; the panel does not
restore a previous device-state cache. Automatic desktop audio setup follows the
current hardware sample rate. Only an explicit rate change requests a new clock.
The combined Debian package defers PulseAudio's initial PCM probing with a
device-specific inactive profile. The user service reads the hardware clock,
loads the matching stereo configuration, then explicitly activates its profile.

## Private process protocol

Input is ASCII decimal fields separated by spaces/tabs, one request per line,
at most 512 bytes before the newline. Serial numbers are 1..4294967295.
All channel, pair, source and bank selectors are zero-based. Values are wire
indices/coefficients. No filenames, shell commands or arbitrary SysEx are accepted.

| Request after serial | Meaning |
| --- | --- |
| `state` | Fresh full bulk query |
| `meters ENABLED` | Input meter stream on/off, 0 or 1 |
| `auto-sense-stop ACTION` | Finish=0/cancel=1 an observed calibration; confirm inactivity and fresh state |
| `preamp CHANNEL PARAMETER VALUE` | Preamp parameter 0..12 |
| `mixer SOURCE BUS CHANNEL PARAMETER VALUE` | Input=0/playback=1; typed mixer update |
| `master SOURCE BUS CHANNEL PARAMETER VALUE` | Typed master update |
| `reverb PARAMETER BANK VALUE` | Type=0/pre-delay=1/time=2 |
| `route PAIR SELECTION` | Physical output pair routing |
| `display BUS` | Select device LCD mixer A=0 through D=3; B–D unavailable at 192 kHz |
| `identify` | Send the original device identification/blink command |
| `monitor CHANNEL ENABLED PAN VOLUME` | Composite input mute/pan/volume; channel 0..9 or 127 for all ten stored inputs, enabled 0/1, pan 0..32768, volume 0..8388607 |

The C++ parser applies the same validated selectors/bounds as the existing
control APIs before any command write. It rejects extra fields, signed/fractional
values, integer overflow, non-ASCII digits and oversized input. The Python side
also rejects unknown JSON fields and booleans masquerading as integer values.

The monitor command uses one firmware DT1, independently of audio streaming.
It first reads the current bulk state and computes the entire expected reply,
including linked mute/volume changes while retaining the peer's pan. Success
requires exact complete raw readback. At 192 kHz an individual selector is limited
to 0..3; broadcast still updates all ten stored inputs. Reference-only inputs
10/11 have no mapped mixer state and are rejected before any write.
The equivalent authenticated HTTP control is:

```json
{"kind":"monitor","channel":0,"enabled":true,"pan":16384,"volume":2097152}
```

`channel: null` selects all ten stored inputs. These are native Linux control
semantics; no Windows ASIO settings are shown. The actual browser write suite
checks that external monitor changes appear in the GUI's fader, pan and mute
controls. The isolated hardware checker uses a private full-state recovery file:

```bash
pasuspender -- python3 tools/check-input-monitor.py --write --rates --links
```

Close the panel first. Outputs are verified silent before each fixture; the test
does not toggle phantom/Hi-Z and restores the original rate and complete raw state.

Output is newline-delimited JSON:

```json
{"event":"ready"}
{"id":1,"ok":true,"state":{"payload_hex":"...","preamps":[],"mixers":[]}}
{"event":"meters","input_levels":[0,0,0,0,0,0,0,0],"input_db":[-96,-96,-96,-96,-96,-96,-96,-96]}
{"event":"auto-sense","channels":1,"active":true}
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

F10 focuses the menu bar without opening a popup; Alt+D/V open Driver/Device.
Left/Right switch root menus, while Left/Escape first close an open submenu.
Pointer movement switches an already-open root menu. Underlined letters activate
the first enabled matching item, including Initialize's A/M/D/R/P submenu;
Device's S invokes Save despite the signal-flow entry sharing that letter.
Escape restores focus through the menu levels. Modal dialogs retain keyboard
ownership; busy/absent/rate guards also apply to mnemonic activation.
The bindings come from Windows menu resource 128 and offline Wine observations.
Playwright checks them against the native backend without parameter changes;
the installed GTK checker also uses 21 actual X11/WebKit menu/dialog key steps.

Ctrl+S/O/P and their menu items share Save/Load/Patch Bay availability. Modal
dialogs, busy/disconnected state, pending writes and active file operations
block them; Patch Bay also remains unavailable at 192 kHz. Extra Shift/Alt
modifiers do not invoke these commands, and key repeat cannot duplicate Save.
Windows Ctrl bindings and modal behavior are checked against the original GUI
using the offline device model; this is not Windows hardware verification.

Recovery files are created privately under `$XDG_STATE_HOME/octa-capture`
(default `~/.local/state/octa-capture`), or `--data-dir`. Keep them after an
interrupted operation. The bridge releases CTRL after an explicit disconnect,
server shutdown or 15 seconds without panel activity. Active Auto-Sens is
cancelled on disconnect, shutdown or client inactivity. Audio continues separately.

Auto-Sens runs in an exclusive background C++ process. Its prepared notification
acknowledges installed signal handlers before the server sends finish/cancel;
its running notification requires actual selected-channel device activity.
Start confirmation has a separate three-second deadline; the calibration limit
begins after that activity. The child watchdog allows 60 seconds plus six
bounded device stages (78 seconds), then requests cancellation and waits for
recovery. Early Finish still waits for confirmed activity.
SIGUSR1 finishes early, while SIGINT/SIGTERM cancel with recovery. Completion
still requires device notifications and verified state bounds. A cancellation is
reported as successful only when the CLI confirms unchanged state or verified
raw restoration. A running calibration keeps its CTRL session until the device
acknowledges cancellation, then restores and verifies the snapshot before closing
the session. The 60-second maximum prevents an abandoned calibration from
running indefinitely. Other controls stay disabled while calibration is active.

The idle Auto-Sens dialog starts with no channels selected; Start is disabled
until a usable input is checked. Select All clears unavailable inputs at 192 kHz.
Its two-row channel group, side selection buttons and 800/200 ms running blink
follow the Windows dialog. Successful finish/cancel closes it after the backend
confirms completion; a failed job stays visible. Active channel checks come from
the backend job. Native Playwright checks cover selection at all rates and actual
finish/cancel with full recovery. Browser-only response simulation verifies active
channel adoption and retention of the dialog on recovery failure; it does not
inject a hardware failure. `tools/verify-auto-sense-ui.py WINDOWS-GUI MAC-GUI`
executes 3,072 selection/gating cases per original binary with host controls
stubbed. Device activity notifications are separate from the bulk state.

`GET /api/state` exposes `auto_sense_activity`: either `null` (not observed on this
connection) or `{channels, active, observed_age_ms}`. Valid selection and completion
notifications are forwarded, including unchanged/inactive flags. Invalid subprocess
events fail the connection. Observation age measures time since a notification,
not a periodic freshness deadline. Connection loss discards the observation.

Observed calibration appears as `auto_sense.origin = "device"`. Its dialog and
Finish/Cancel remain accessible while competing writes are blocked. Stop uses the
existing CTRL connection: query/drain first, reject unknown activity, send only
while active, await inactivity and validate fresh state, within one three-second
budget. The bridge reply's `auto_sense_stop_sent` is false if the operation already
ended; a late Cancel is then reported as completed rather than cancelled. Pending
stops remain active until the verified reply. Failure or loss of previously active
status stays blocked and visible until the panel is reopened; cached or repeated
active flags cannot clear that failure. No completed/restored state is invented.

An observed operation has no host snapshot from before its start. Cancel uses the
firmware command and never restores a snapshot captured during calibration.
Native 48 kHz/input-1 Playwright tests verify both actions, view navigation, exact
unselected-state preservation, Cancel restoration against the actual pre-start
fixture and zero browser errors. Physical outputs are isolated on silent Mixer B,
and the complete user state is independently restored. These use a valid test-
program start, not a physical front-panel-button press. Activity predating the
connection remains unknown until a valid notification arrives.

`ctest --test-dir build/strict -R midi-observed-auto-sense` checks 22 offline
transport cases: missing/malformed end, invalid parameter bounds, write failure,
restart during readback, already-ended activity and reentrancy. An unresolved
stop prevents further queries on that session. Fuzz MIDI/bridge inputs offline;
never send malformed test traffic to hardware.

The independently authored web assets use the original parameter values and
reference-derived behavior, not extracted manufacturer images. Unimplemented
capabilities stay disabled; absent output meter data is not synthesized.

Sensitivity value fields support double-click editing (Enter/F2 for keyboard
access), Enter/focus-loss commit and Escape cancellation. Four-character decimal
values in 0..50 dB truncate to half-dB indices, matching Windows `0x140003940`
and macOS `CValPreSens::CheckValue`. Polling preserves active edits; invalid
decimal values restore the current display without a device write.

Pointer hit regions follow the original child controls: knob dials and button
skins are active; their painted labels do not drag, toggle or open control menus.
Keyboard activation remains on the labeled control. Context menus open on right
release inside the control, and are suppressed while left is held or the control
is disabled. Left release ends a knob/fader drag even when right remains held.
Shift+F10/ContextMenu opens the focused control's menu directly; this does not
depend on a browser-generated context event, which native WebKit omits.
Button-only pointer transitions do not quantize a stored value; pressing left
while right is already held can still begin a drag. Range controls suppress
their native mouse-down default separately from pointer delivery.
The fixed original-code comparison is optional:

```bash
python3 tools/verify-control-hit.py doc/local/windows-gui-analysis/RDDP1110.EXE
```

Chromium checks actual skin/label targets, both button orders, release timing,
capture cleanup and keyboard activation. Parameter replies are intercepted in
the browser, and full raw state is checked independently; these input checks
do not establish physical gain response.

GTK 4.18.6/WebKitGTK 2.54.0 loses a primary mouse sequence when another
button reaches its single-button gesture controller. The host intercepts
secondary button presses/releases during capture, reads their public GDK event
fields and forwards bounded mouse messages to the local page. Primary input
keeps WebKit's native path. Coordinates include the native surface translation,
widget offset and page zoom. These forwarded DOM events have `isTrusted=false`;
the X11 checker compares every such event with the actual GDK-derived host
receipt, including coordinates, buttons and modifiers. It also verifies primary
trusted events, menu timing, capture cleanup and raw-state preservation.
The event reader uses the public GTK C API because this PyGObject runtime
delivers the opaque `GdkEvent` signal argument as `None`.
See [GTK's gesture button guard](https://github.com/GNOME/gtk/blob/4.18.6/gtk/gtkgesturesingle.c)
and [WebKit's mouse gesture setup](https://github.com/WebKit/WebKit/blob/webkitgtk-2.54.0/Source/WebKit/UIProcess/API/gtk/WebKitWebViewBase.cpp).
Native Wayland input behavior remains unverified.

Main-panel control geometry matches 302 original Win32 child rectangles at
96 DPI: 128 in Preamp and 174 in Direct Mixer. This includes 25×25 dials,
23×19 switch skins, channel spacing, sensitivity editors, compressor plots,
meter/clip windows and 18×88 fader controls. The Linux view uses our own CSS
artwork at these positions. Both browser and GTK checks compare rendered
rectangles with `test/data/control-geometry.json` and retain the original view.
The native Windows menu occupies 19 pixels above its client coordinates;
the fixture includes that translation. Other DPI settings, native Windows font
rendering and owner-painted labels/textures are outside this rectangle comparison.
Channel value windows meet the fader's lower edge; the original shared master
value windows start one pixel lower. The browser check enforces these exact
vertical gaps rather than adding spacing absent from the original.
Each stereo master pair shares one 41×13 value window. It shows the last updated
side independently for input/playback; releasing an unchanged thumb also selects
that side without changing its level. Changed device values update the selection
in left/right order; unchanged polling retains it. Pending previews survive older
acknowledgments. Individual sliders retain their accessible values and tooltips.
`tools/verify-master-display.py WINDOWS-GUI --macos MAC-GUI` executes the original
selection/getter functions on both references and Windows master refresh code.
Thirty-two traces per reference match the web display and run from numeric
fixtures in standard tests. Visibility/painting and macOS notification scheduling
are modeled. The browser additionally checks unequal stereo values, release
timing and selection across views on all four buses with modeled responses.

The master section's five OUTPUT JACK labels follow the current patchbay and
selected mixer: green for assigned pairs, light gray for other routes and dark
gray for outputs unavailable at 192 kHz. At that rate, only pairs 1/2 and 3/4
are available and selection uses Mixer A, regardless of stored B–D metadata.
Accessible labels name the stored route or unavailability independently of color.
The multiple-mixer header uses the original blue active-lamp color family.
Active switch palettes also follow the shared reference resources: pink/red for
preamp, compressor link, mixer mute/solo/link, master/reverb and Auto-Sens;
amber/orange for compressor bypass; blue for Multiple Direct Mixer. Own CSS
gradients use the reference face colors `#ffa5b5`, `#ff9546` and `#65c4ff`.
Resource selection is traced in both constructors. Switches also track primary
press, leaving/reentering the skin, release, capture loss, host focus loss and
disabling. Disabled lamps use the original gray face instead of fading an active
color. `tools/verify-switch-frames.py WINDOWS-GUI --macos MAC-GUI` executes both
original clip selectors and Windows availability transitions; standard tests use
the eight numeric clip cases. Browser input checks cover all three palettes,
release outside, reentry and disabling during a press; host blur is modeled.
This does not establish exact bitmap painting or complete visual parity.
`tools/verify-output-jacks.py WINDOWS-GUI --macos MAC-GUI` executes both original
selectors over 720 fixed rate/mixer/pair/route combinations each. Standard tests
use the resulting numeric fixture; painting, notifications and device transport
are outside this comparison. Browser response models cover all four rates,
bus selection, polling, all nine stored routes and displayed colors. The GTK
checker compares labels against physical routing readback at the current rate.

The read-only inspector checks the running original executable's SHA256 and can
compare either visible view. Run it with Windows Python alongside the original
English x64 panel, then switch views and repeat:

```powershell
py tools/inspect-windows-layout.py --compare test/data/control-geometry.json
```

`--output PATH` saves a new private observation instead of the complete stdout
dump. The inspector also runs with Windows Python under the existing private
Wine model. This model checks window layout, not Windows hardware control.

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
tools/check-gui.sh --write --reload-module  # development helper required
# Actual Playwright runner, isolated from system Node packages:
npm install --prefix build/playwright-runner --no-save --ignore-scripts --no-audit --no-fund playwright@1.64.1
tools/check-gui.sh --playwright --write --rates --matrix
```

`--write` uses actual browser keyboard/pointer/file events, verifies hardware
readback, multiple-mixer routing, panel commands, ten-channel context actions and
Auto-Sens finish/cancel, and restores fresh raw snapshots, then checks release of CTRL. The
checker saves private screenshots and recovery files under `build/gui/`.
`--playwright` first runs `test/GuiShortcuts.mjs`: real Save/Open/Patch Bay keys,
menu eligibility, file-choice confirmation cancellation and repeated-key guards.
Busy, pending-write and missing-device cases use explicit response simulation;
the simulated write never reaches hardware. Deliberate missing-state HTTP 503
replies require exact endpoint/status/console correspondence. Native Debian
passes these checks at all four actual rates with complete raw/rate preservation.
The GTK checker also checks modal command suppression in WebKit, alongside its
actual X11 keyboard and native Save/Open checks.
`--rates` also checks immediate sample-rate selection, Close/Escape semantics, kernel/bulk rate agreement,
channel availability, 192 kHz menu/dialog restrictions and PCM meter recovery,
then restores the original rate. The Initialize submenu stays available at 192
kHz, as in both reference dispatchers. Native Debian device tests pass at all
four rates; physical signal quality remains a separate requirement.

`--matrix` additionally checks 427 actual browser changes: every preamp knob,
safe phase/low-cut/compressor switch, strip pan/volume/mute/solo on all four buses,
master volumes and Mixer A send/return controls. Each change requires the correct
acknowledged request, fresh hardware readback and exact-value restoration. Phantom
power, Hi-Z, link/mono changes and persistent memory are outside this matrix.

Sample-rate selection takes effect immediately, as in Windows handler
`0x14002c2f0` (IOCTL `0x222328`). Linux Audio Settings shows ALSA, the 24-bit
hardware format, the rate-dependent capture/playback channel counts and the
Mixer A capture return. Close and Escape close the dialog without undoing the
device operation. Audio applications and sound servers own their periods and
buffers; the dialog explains this instead of exposing Windows ASIO controls.
The Driver Settings unit selector uses the same discovered, serial-bound device
identities as the panel selector; it is unavailable during pending device writes.

The mixer uses original vector artwork for white channel and red master fader
caps, with 18×32 pixel ribbed handles and numeric dB readouts. Linked channel
thumbs and readouts update together on input and playback channels in all four
mixers, before the device replies. Master previews use the device's separate
stereo and input/output link rules. Only the operated fader sends a command;
the device applies its link. Pending values remain visible during state polling;
fresh device replies remain authoritative. The Playwright write check delays a
control request, checks immediate peer movement and rapid queued input, then
verifies hardware readback and complete raw restoration.
The read-only Playwright check models delayed channel responses to verify paired
keyboard/drag previews, stale acknowledgments, unlinked isolation and coalesced
commands without writing channel settings to the physical device.

Channel and master faders use the original Windows vertical movement and click
rules. The 88-pixel control and 32-pixel thumb give 56 movement intervals;
normal drag changes by -1024/56 indices per downward pixel, Shift by -1.
Normal movement rounds up to eight indices; Ctrl selects the same exact level
coefficients as send/return knobs. Clicking the thumb preserves its value;
clicking the track jumps to its rounded position before applying modifiers.
Arrow keys change one index; Home/End leave the value unchanged. Pending channel
and master readouts update immediately, and polling preserves the active drag.
The macOS `CCtrlLevelBase` range/scales and `CRsVrHV` geometry agree with these
Windows values. This is reference-code evidence, not original hardware control.

`python3 tools/verify-knob-drag.py RDDP1110.EXE --faders` executes 288 fixed
drag events, 504 thumb/track click cases and 18 key cases against the hash-pinned
Windows code, including its actual geometry and volume conversion functions.
Channel/master handler equivalence and resource dimensions are checked. Host
capture, display interval getters, `PtInRect`, painting and device notifications
are memory stubs. `--save-vectors PATH --save-clicks PATH` saves the compared
numeric traces; `gui-faders` CTest uses them without manufacturer code/assets
or a device. These are deterministic checks, not fuzz runs.

Knob pointer movement follows the original Windows `CCtrlWnd` handler: the
dominant axis determines the change (vertical on ties), integer changes truncate
toward zero, and the pointer anchor advances before parameter quantization.
The original 65-position resource defines 64 movement intervals. Sensitivity,
compressor, pan and send/return controls retain their separate normal/Shift
scales, including the negative Shift sensitivity coefficient. Normal pan rounds
up to 5-% steps, normal sends/return to eight fader indices; Shift bypasses
these steps. Ctrl snaps pan to left/center/right and sends/return to the original
level presets, preserving their exact wire coefficients. Arrow keys move one
index, including 1-% pan steps; Home/End leave knobs unchanged.

`python3 tools/verify-knob-drag.py RDDP1110.EXE` executes 2,304 fixed mouse
events and 144 key cases in the hash-pinned original binary. Window capture,
painting and backend notifications are memory stubs. `gui-knobs` CTest uses the
compared numeric traces in `test/data/knob-drag.json`, without manufacturer
code/assets or a device. `--save-vectors PATH` regenerates those traces only
after a successful comparison. The Playwright write suite also checks actual
pointer events against these traces and independent device readback.

Wheel input over a dial/fader focuses that control and changes by one index per
120 native wheel units, clamped to its range. Each event truncates independently;
partial deltas are not accumulated. The Windows private `0x80c8` message handler
and `0x140022af0` force fine steps regardless of Shift/Ctrl, so wheel input does
not use normal drag quantization or Ctrl presets. Horizontal scrolling and open
popup menus do not change background controls. Active sensitivity editing and
busy/disconnected/unavailable controls reject wheel changes.

Chromium/WebKit native `wheelDeltaY` takes precedence over scroll pixels:
an X11 upward notch produces +120 native units, while pixel deltas differ by
host/control (WebKit reported -44/-56 pixels). DOM-only fallback uses 120 pixels,
three lines or one page per notch; arbitrary touchpad/other-browser equivalence
is unproven.
`python3 tools/verify-control-wheel.py RDDP1110.EXE` executes 3,060 fixed original
message/virtual-handler cases, including signed-short bounds, all modifier
combinations and exact wire values. Focus, painting and notifications are stubbed.
`--save-vectors PATH` saves compared numeric cases; `gui-wheel` CTest runs these
offline without manufacturer code or hardware. No fuzzing is involved.
Another 540 fixed wheel/drag events verify that wheel input changes the current
value while retaining the pointer anchor; the next drag compares against the
updated value. `--save-interleaved PATH` saves these numeric traces for the same
CTest. The Playwright write suite checks actual wheel input on all nine
knob/fader groups and interleaved input on both control adapters. The installed
GTK checker also injects real X11 wheel events and verifies hardware recovery.

`--reload-module` uses the already provisioned `sudo -n octa-capture-dev` helper
at `/usr/local/sbin/octa-capture-dev` to unload and reinstall this module. It
checks the absent-card page/menu, automatic same-unit reconnection, real MIDI/PCM
meters and full raw preservation. It does not detach/reset USB or install the
helper. This passes on native Debian; physical unplug, suspend/resume
and USB faults remain separate. Module installation runs in a finally block
after successful unload. Playwright requires zero unexpected HTTP, console,
network, JavaScript or CSP errors. During deliberate absence it specifically
verifies `/api/state` HTTP 503 replies and their corresponding console records;
other endpoints/statuses or errors after reconnection fail the test.

Native module-reload clock checks require exclusive access across enumeration.
`pasuspender` alone did not prevent PulseAudio from opening a newly created
ALSA card at 44.1 kHz. The isolated native check temporarily stopped the user's
automatic ALSA discovery with no PulseAudio streams active, then restored its
original module arguments, complete device state and 48 kHz. This is a host
rate-selection interaction; ordinary reconnection displays the newly selected
rate. Stop competing discovery before a clock-preservation test, restore it
afterward and independently verify the original device rate/state again.

Driver → Show README opens an independently written local usage guide. It loads
no external content and is available in both the browser and GTK host.

The signal-flow dialog uses our own SVG drawing of the reference topology
(Windows bitmap 246 and macOS `PNL_DIAGRAM.bmp` agree). Dialog bodies scroll
independently of their footers; Chromium and WebKit checks verify visible,
clickable footer buttons at the desktop host's 978×429 viewport.

The connected panel's bottom status bar follows the Windows layout: device
selection/identification, direct sample-rate selection and actual clock status.
Its rate selector and Driver Settings share the same busy guard and authoritative
readback. Native Playwright checks exercise all four footer rates, kernel/DSP
agreement, PCM meter recovery and full-state restoration. Missing clock status
displays `UNKNOWN`; this fallback is checked with a browser-only response
simulation, without changing hardware status. GTK checks verify footer readback
and pointer access within the fixed window. A CSS-drawn arrow keeps the compact
rate label readable with WebKitGTK theme metrics; native screenshots verify
the actual 48 kHz display with independent state/clock preservation.
The device selector remains available
when disconnected to support reconnection; the original hides its bottom bar.

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
Patch Bay's top preset row and compact output selectors, and Reverb's compact
controls follow Windows dialog resources 178/188 and the offline Wine captures.
Secondary dialogs have draggable captions and a caption Close button. Reopening
returns to centered placement; viewport/content changes keep moved dialogs
reachable. The inactive panel retains its colors, matching reference captures.
Caption dragging preserves control focus. Close dispatches the same
cancel event as Escape, so nested confirmations decline their action and
Auto-Sens remains open until the backend verifies completion. Pending cancellation
disables both Close and Cancel and suppresses duplicate Escape requests.
Browser checks cover all eight frames, viewport bounds/resize and declined nested
initialization. Auto-Sens cancellation/pending/failure checks intercept browser
responses and never send calibration commands to hardware. The installed native
GTK checker additionally verifies trusted X11/WebKit caption drag/close,
centered reopening and Escape. Native WebKit snapshots need no ImageMagick
executable; device state and clock are checked independently afterward.

Dialog initial focus, Tab/Shift+Tab wrap and unmodified Enter follow the five
original resource orders in `test/data/dialog-keyboard.json`. The bottom buttons
keep their visual placement while participating first in the applicable Tab
orders. Reverb's read-only spinners stay outside that order but retain pointer
and focused arrow-key operation. Enter activates a focused pushbutton or the
dialog's default; an open native combo popup keeps its own Enter handling.
Empty Auto-Sens opens on Cancel, while enabled Start remains the default from
a checkbox. Confirmation opens on OK; focusing Cancel makes Enter decline it.
Browser checks cover the fixed orders, closed/open combos and disabled/pending
Start. Accepted confirmation/calibration responses are intercepted in the browser;
these checks send no such hardware commands. Trusted X11/WebKit input additionally
checks About's Tab wrap, reverse Tab and default Enter with raw state/clock
preserved. WebKit's `Unidentified` Shift+Tab is recognized through physical
`code=Tab`; other unidentified keys do not navigate. The optional reference command is:

```bash
python3 tools/verify-dialog-resources.py doc/local/windows-gui-analysis/RDDP1110.EXE
```

All 13 pre-delay strings and 50 time displays agree with both binaries. Keyboard,
pointer, endpoint bounds and all four effect-bank writes/readbacks are checked in
the actual browser. Offline checks verify autorepeat cancellation on release,
focus loss, hidden pages and disabling; pending readback cannot reverse local steps.

## GTK4/WebKitGTK host

`python3 tools/desktop-gui.py` starts a fixed-size desktop window and a private
loopback server on an automatically selected port. Optional arguments are
`--bin-dir`, `--midi-port` and `--data-dir`, as for the HTTP launcher.
Debian 13 packages are `python3-gi`, `python3-xlib`, `gir1.2-gtk-4.0` and `gir1.2-webkit-6.0`;
GTK 4.10 or later is required for the native asynchronous file dialog.

The WebKit network session is ephemeral, with no system proxy, external
navigation, pop-up windows or web permissions. The web-process sandbox stays
enabled. WebGL/media are disabled and software rendering keeps this small 2D
panel usable without a GPU. Only the panel's own blob downloads reach the native
Save dialog. WebKitGTK 6.0 receives a filesystem destination path after user
selection/overwrite confirmation. Exit and window/server shutdown release CTRL;
the same Auto-Sens recovery lifecycle applies.

Driver → Always on top uses EWMH on supported X11 desktops, including Xfce.
Its checkmark reads actual window-manager state; a successful change is confirmed
before its preference is saved privately in `window.json` and restored at startup.
The narrow `octaWindow` WebKit message handler accepts only a state query or a
boolean change for this host's own window. It checks the local document URI and
accepts no window IDs, paths, commands or device operations. The HTTP API is
unchanged. Ordinary browsers and unsupported window systems keep this item disabled.
The offline desktop check uses a private X server and no audio device:

```bash
xvfb-run -a dbus-run-session -- python3 tools/check-window.py --xfwm
xvfb-run -a dbus-run-session -- python3 tools/check-window.py
```

The first command also needs `xfwm4` and `x11-utils`. It verifies actual WebKit
requests, menu toggling, independent X11 property readback, invalid-message
rejection and restoration across process restarts; the second verifies the
unsupported-window-manager behavior. Add `--installed-prefix PREFIX` to check
the installed Python modules and web assets.

The native integration checker uses real X11 key events for controls and Ctrl+S,
validates the native Save download, checks live controls/meters, blocks an
external navigation and exercises Driver → Exit. It verifies independent full
state readback after releasing CTRL. It supports both in-process GTK choosers
and separate desktop-portal dialogs, verifying the exact returned file path.
Caption-close checks require a trusted click on the Close control and observe
the resulting closed state before reopening. Close/Escape completion uses the
existing overall deadline, with pointer-target traces retained on timeout.
Native keyboard input waits for WebKit focus and a display matching fresh
device state. For headless checks, install `xvfb`, `xauth`, `xdotool`, `dbus`,
`imagemagick` and `gir1.2-atspi-2.0`:

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
These desktop integration checks pass on native Debian; they do not establish
audio signal quality. The remaining feature gaps still apply.

`cmake --install build/release --prefix PREFIX` installs the runtime independently
of the checkout, with `PREFIX/bin/octa-capture` and `octa-capture-http`. Native
tools share that directory; Python/web assets live under `libexec/octa-capture`.
Both launchers resolve their paths relative to the installation. The offline
`gui-install` check uses a prefix with spaces and an unrelated working directory.
To run the GTK hardware checker against extracted package files, add
`--installed-prefix PATH/TO/usr` to `tools/check-gtk.py`.
