# Reference functionality and verification gaps

Completeness is **not established**. This inventory separates behavior traced in
the Windows 1.5.3 package/macOS 1.5.9 references from implemented and hardware-tested
behavior. Resource names and function counts alone do not prove functionality.
The [protocol reference](usb-midi-protocol.txt) contains wire details and test limits.

## Reference coverage

- Windows x64: package imports/exports and resources inspected for `RDDP1110.EXE`,
  `RDCP1110.CPL`, `RDAS1110.DLL`, `RDAW1110.DLL`, `RDAH1110.EXE`, and `RDWM1110.SYS`.
  The control-panel RTTI/vtables and MFC message maps identify dialog handlers.
  The kernel binary has been imported and its functions/call sites inventoried;
  selected PortCls power callbacks, status polling/cache and status/availability
  IOCTL paths now execute offline. Its scalar dispatcher also matches 2,948
  preamp/compressor and 4,012 DSP encoding cases against C++ dry runs, with
  112 transport-gate combinations. Original state helpers and MIDI/checksum
  builders execute; only the final sender is stubbed. These valid-input checks
  do not verify complete cache/coupling or outer IOCTL validation. Complete IOCTL/property,
  USB and power-management behavior is not yet traced.
- Windows x86: the package's legacy branch is version 1.5.2 according to its INF
  and English README, whereas x64 is 1.5.3. The x86 kernel inventory contains
  1,120 functions and 3,037 call sites, including unresolved indirect calls.
  Its complete 92-byte descriptor result matches x64 for the cached native
  device, including all four rates.
  The monitor codec matches 910 C++ frames; 2,400 fixed requests execute through
  both its conversion function and model-specific IOCTL dispatcher, with final
  transport and memory imports stubbed. Its individual-input limit is 0..9,
  versus x64's 0..11; broadcast is common. Four transport readiness combinations
  also pass per path. This does not establish x86 streaming, ASIO DLL behavior,
  complete IOCTL validation or a complete legacy-driver audit.
- macOS x86_64: control-panel transmitters, state decoders, preset operations,
  audio USB/clock/stream paths and MIDI routing have selected decompilation and
  offline reference execution. The separate MIDI plugin's own port selector and
  open routines also pass 25 fixed cases and five opening sequences with modeled
  flags/interface objects and a final OpenIf stub. This does not verify USB
  transfers or establish the purpose of interface 4.
  The separate Driver Setup parameter get/set/default
  paths are traced: buffer level, recording margin and timing use CoreAudio host
  properties. Remaining platform/property paths still require analysis.
- Both macOS audio architectures execute 108 fixed USB-control cases each through
  the original request wrappers with memory-only host APIs. Both rate setters
  use payload prefix `0x40`; this corrects the Linux busy same-rate path and an
  earlier decompiler-derived `0x01` claim. Clock polling/error/transfer boundaries
  and DIN helper bytes are covered; DIN values, call paths and physical external
  synchronization remain unresolved.
- Windows stream construction/channel strides and the channel counts derived
  from macOS nominal USB-frame geometry agree in isolated CPU execution at all four rates, also matching the
  cached device descriptors. Windows short-capture-packet accounting is exercised
  separately. Original macOS packet allocation also executes at each rate in both
  directions: high-speed default index 0 uses 32 ms / 256 microframes per transfer,
  with rate-dependent channel strides and buffer sizes. Host allocation/setup
  dependencies are stubbed; this is not audio
  signal or kernel fault validation.
- Windows host-buffer policy is traced through the actual Light/VS EXPAND
  callback and all ten levels at each supported rate. Isolated execution checks
  160 kernel combinations and 482 public ASIO getter cases, including output
  clearing on failure. These host settings do not define Linux buffer policy
  or prove streaming latency.
- macOS arm64: isolated execution checks all 2,948 legal preamp/compressor
  transmitter cases and 3,932 DSP cases across 14 mixer/master/reverb/routing
  transmitters against the C++ encoders. The same DSP matrix passes on x86_64;
  large scalar ranges use boundary and mixed-nibble samples. Another 2,049
  nearest-fader cases and 243 compressor graph points match the web implementation.
  Fader and attack/release/ratio/pre-delay/dB tables also match. Cached USB
  descriptor parsing, interface selection and audio-open sequences also match
  x86_64/Windows at all four rates, using architecture-specific structure offsets
  and host-open stubs; interface 4 remains unresolved. Other ARM64 GUI, audio and
  property paths still need independent comparison.
  Shared artwork does not establish shared control logic.
- `tools/ghidra/ExportReferenceInventory.java` exports function and call-site TSVs
  from an analyzed program. Indirect calls remain explicitly unresolved. Outputs,
  extracted resources and decompilation stay under ignored `doc/local/`.
- `ExportSelectedReferences.java` exports analyzed references into bounded address
  ranges, including their source functions/instructions. Missing references do
  not prove that an indirect target is unreachable.

## Functional comparison

| Function | Original evidence | Current implementation / remaining work |
| --- | --- | --- |
| Four sample rates, channel packing, capture/playback | macOS audio driver; Windows settings/readme | Standalone module; native Debian 6.12 passes all-rate PCM lifecycle checks and ten-source digital-return PRBS streams at 44.1/48/96 kHz (ten seconds per source; earlier requested 1024/8192, and independently verified 256/1024-frame period/buffer). Ten sources also pass at 48 kHz with verified 128/512 frames. All four native rates pass ten-second silent duplex with actual 256/1024 frames and kernel parameter/owner checks. Analog response, 192 kHz signal routing and sustained small-buffer integrity/stress remain. |
| Clock status and rate changes | macOS USB/status/property code; Windows settings handlers and connected footer | Kernel rate negotiation and read-only status API; native Debian phase/coherence, read-only access and concurrent reads pass. Connected footer and settings share rate/clock readback and busy guards; native Playwright verifies all four internal-clock footer rates, PCM recovery and complete restoration. Original status polling and Windows status/availability IOCTLs execute offline, including phase, cache fallback and timeout recovery. Backend/web availability matches 64 original Windows/macOS cases; missing status disables selection, and every rate request rechecks fresh status. Playwright covers both selectors and pending-error recovery with synthetic replies. Byte 2 normalization differs between references and remains raw in Linux. Physical external-clock transitions, rate-availability parity and lock loss still need hardware tests. |
| DIGITAL AUTO/OFF | Device manual's front-panel utility; generic macOS `Fsc_SetDIN/GetDIN` helpers | Both macOS architectures match request-05 setup and opaque-byte encoding offline; no direct caller or AUTO/OFF wire values established. Getter times out on native direct USB. No live write API; external-clock transitions remain hardware-unverified. |
| MIDI DIN, CTRL isolation and endpoint routing | macOS MIDI plugin; Windows kernel encoder/decoder and queues | Private ALSA MIDI helper and exclusive CTRL sessions. Valid framing, interrupted SysEx and selected receive filters match both references offline. Physical DIN loopback, scheduling and transport faults remain. |
| Preamp sensitivity, phase, low-cut | macOS setters/tables; Windows controls | All legal safe preamp/compressor indices pass writes, independent full-state readback and restoration on all eight native inputs at 48 kHz (2,924 cases including Hi-Z on 1/2). Typed sensitivity parsing matches reference code and browser/hardware tests; physical gain remains. |
| Phantom power and Hi-Z | macOS setters; Windows controls; manual | Encoders and selector bounds implemented. Hi-Z changes/readback/restoration pass on inputs 1/2 at 48/192 kHz with phantom already off. Physical impedance response and phantom switching remain unverified. |
| Compressor enable and six parameters | macOS transmitters/table lookups; Windows eight-channel view | Every legal index on all eight channels passes native Debian write/readback at 48 kHz, including full raw isolation. Nonlinear tables and inverse faders match both references; 1,440 filled-curve/line cases match original CPU execution. Both drawing palettes/pen widths match. GUI controls and rendered pixels tested in Chromium/GTK. Physical dynamics response remains. |
| Compressor stereo link | macOS `TxPreLink`, manual | All four link cycles and 160 scalar coupling cases pass on native Debian (strict, 48 kHz); complete raw state is restored and ADC controls stay independent. |
| Generic compressor presets and knee data | Windows MENU 132 submenu 0; macOS data tables | Windows explicitly displays submenu 1 (Bypass), and macOS builds only the two bypass commands. Full macOS preset/knee tables have no analyzed references. No established preset/knee feature; unresolved indirect paths remain. |
| Auto-Sens | macOS start/stop and dialog selection code; Windows dialog 289/handlers | Selection, notification flow, finish/cancel, bounds and signal recovery implemented/tested. Idle selection, empty-input Start guard, Select/Clear All and 192 kHz limits match 3,072 original CPU cases per binary. Native Playwright verifies all-rate selection, real start/finish/cancel and confirmed automatic dialog close. Active checks follow the backend job; failed jobs remain visible. Early signals, client loss and shutdown have offline lifecycle tests. Observed operations now support same-connection verified Finish/Cancel with no invented pre-start backup; native 48 kHz/input-1 Playwright checks pass with silent physical outputs and full recovery. Twenty-two offline transport cases cover failures, bounds and reentrancy. Calibrated sensitivity, physical front-panel initiation, pre-connect activity and every original dialog state remain. |
| Four direct mixers, input/playback strips | macOS state/write/control code; Windows panels | Typed controls and scalar/stereo/mono/master-coupling tests. Mixer A digital return verifies ten playback sources, linear pan points, gain, mute and solo precedence; native Debian passes ten-source PRBS sample-order checks at 44.1/48/96 kHz. Analog mixing, B–D signal response and automation remain. |
| Mixer A–D selection on device display | macOS `TxDMix`, mixer button logic | Typed API, CLI and browser tab commands implemented; all four frames match original CPU execution. Physical LCD confirmation remains. |
| Reverb, sends and return | Both dialog resources; macOS DSP code | Scalar controls/readback implemented/tested. Read-only arrow controls and all pre-delay/time strings match both references; actual browser steps, bounds and all four banks pass with full recovery. Physical effect response remains. |
| Patchbay, Multi-Monitor, Direct Out | Both menus/dialogs; macOS sequences | Original routing/206-command Direct Out sequences implemented; native hardware verification remains. Header switch on/off matches both original handlers; routing-derived lamp/tabs and browser recovery tested. |
| Initialize All/MIC PRE/DIRECT MIX/REVERB/PATCHBAY | Both menus; macOS dispatch/`TxInit` | All sequences implemented. All/preamp power changes and 192 kHz behavior remain hardware-unverified. |
| Runtime snapshots and `.octa` files | Both original preset writers/loaders | Ver100 reference goldens, import/export and raw recovery implemented. Browser download/upload and GTK native Save/Open with actual hardware application pass at all four rates, including retained 192 kHz metadata, with complete raw recovery. Native browser coverage includes all four rates; installed native GTK Save/Open also passes at the current rate through desktop-portal dialogs. Original Windows loading also follows all eight selected base/EXP instance paths in memory; physical multi-unit loading remains unverified. |
| Store/load internal device memory | macOS `TxSaveLoad`; Windows dispatch/confirmation strings | Commands and recovery implemented/fault-tested; persistent device memory not hardware-tested. |
| Input/driver meters, clip and peak hold | Both panel classes; macOS meter managers | Eight SysEx input meters and actual capture/playback PCM meters feed the GUI. Windows-compatible peak calculation, nonlinear meter scale, fall/hold/clip math match original CPU execution. Leased idle/normal PCM handover, all-rate transitions, exact peak retention and client-death lease recovery pass on native Debian. Physical level calibration remains. |
| Device selection and identification blink | macOS device manager/`TxBlink`; Windows unit handling | sysfs enumeration, serial-bound device menu, reselection and automatic reconnect implemented. Real module unload/reload preserves state and restores MIDI/PCM meters on native Debian with exclusive access. Renumbering/absent-unit guards have offline tests. Base/EXP model and serial labels follow the successful state snapshot; offline Chromium and native WebKit verify changes and missing-identity fallback. Identify frame matches original execution; CLI/browser sends preserve state. Physical blink/unplug and multiple physical units remain. |
| Context menus and bulk actions | Both menu resources/builders | Nested Initialize/All Ch., original ten-strip volume/send/pan scope and compressor bypass semantics implemented. Pointer/keyboard and ten-channel hardware changes/readback/recovery tested. Remaining interaction parity still requires tracing. |
| Driver settings / Linux audio information | Dialog 187 and `CDrvPropDlg` handlers; kernel IOCTL `0x222328` | Immediate rate selection, all four kernel/bulk rates, channel restrictions and PCM meter recovery pass through actual browser controls. The Linux dialog shows ALSA format/channel information and explains application/server buffer ownership. Close/Escape end the dialog without rate rollback. Native Debian browser checks pass; Windows host-buffer controls are omitted. |
| ASIO direct monitoring and low latency | Windows settings handlers/readme; `RDAS1110.DLL` | Direct-monitor gate, logical-input mapping, IOCTL 0x222144 and kernel sender traced. Codec matches 910 original frames; 2,080 DLL/kernel requests execute offline with host I/O stubs. Typed Linux monitor API passes 304 native hardware cases across all rates and link/mono modes with exact full-state readback/recovery; Playwright verifies displayed mute/pan/volume. Broadcast updates all ten stored inputs even at 192 kHz. Unmapped inputs 10/11 are rejected. Physical signal response, DAW hooks and broader low-latency behavior remain. |
| Windows sound/ASIO rate matching | Windows checkbox 1147, property collector/Apply, kernel `FsSyncOff` consumer | Inverse host preference traced through property offset 0x128 and WDM format-update gating; explicit GUI clock changes bypass this preference. Original CPU execution passes 768 full-property, 61 Apply and 128 rate-gate cases with host dependencies stubbed. Linux negotiates hardware rates through ALSA; application/server mixing/resampling policy remains external. No WDM checkbox is exposed. |
| VS EXPAND/two-unit synchronization | Windows checkbox 1173/handler, readiness function, both drivers' PID selection and manual pp. 65-66 | Staged host flag and mutual exclusion with ASIO in Light traced; property collector verified by original CPU execution. Another 280 original CPU cases cover base/expansion slot filtering, rate/controller/version mismatch precedence and query failures. Host dependencies are modeled; this gate alone does not enforce the physical two-unit/192 kHz limits. Initial `0582:0121` module matching, shared clock/status paths and EXP-labeled GUI discovery are implemented; the sanitizer VM verifies modeled EXP binding/status/teardown. Activation, permission and deferred-audio rules cover both IDs with namespace checks for target isolation. Experimental native ALSA 24-in/20-out preparation checks selected identities and stable clock reports; six isolated ALSA cases verify EXP poll selection and exact signed-24-bit sample/channel order. Actual EXP descriptors/controls/audio, coupled rate/server coordination, synchronization and routing remain incomplete/unverified. |
| Disconnect, suspend/resume and errors | Both native driver lifecycles | Session/rollback fault tests and actual GUI recovery after module unload/reload pass on native Debian. Native Playwright checks confirm absent-card UI, retained serial selection, full state/rate and fresh MIDI/duplex PCM meters. PulseAudio discovery must be isolated for clock-preservation checks. The private patched Linux 6.12.113 VM checks module lifecycles, synthetic USB probe/concurrent status reads, seven fixed status errors, usbmon-confirmed in-flight unplug, PCM prepare stalls with repeated free/reconfiguration, a task/cache-filtered status-buffer allocation failure with exact `ENOMEM` and same-handle recovery, PCM-meter/audio-stream probe allocation failures with clean rebind/status recovery, and subsequent status/re-enumeration with KASAN/UBSAN alignment checking enabled (see the kernel README). Physical hotplug, other USB/URB faults, streaming, broader allocation fault injection and power management remain. |
| Signal-flow/About/warnings/preferences | Both menus, dialogs and local resources | Local HTTP panel, secondary dialogs and local README implemented. Persistent port-warning preference, inverse About/warning checkboxes and all 160 original warning transitions tested; browser hardware rate tests exercise them. GTK4/WebKitGTK embedding, native Save, external-navigation blocking and Exit pass on native Debian, including portal-owned Save/Open. The desktop entry appears in Xfce Applications → Settings. Always on top is implemented on EWMH/X11, with actual WM confirmation, private persistence and offline WebKit/Xfce restart tests. Browser/unsupported hosts disable it. Complete visual/behavior parity remains. |

The Windows Always on top handler (`0x14002e510`) toggles its frame state and
writes `AlwaysTop` to the profile. Window creation (`0x14002e520`) reads it with
default zero; the menu update handler (`0x14002e650`) reflects that state. Linux
keeps this host preference separate from the device and audio-port warning.

Original Windows knob mouse handler `0x140003300`, key handler `0x140003150`
and parameter-specific movement/quantization callbacks execute offline against
2,304 fixed pointer events and 144 key cases. The web controls match dominant-axis
selection, Shift/Ctrl behavior, anchor rebasing, truncation and exact wire values.
Arrow keys use single indices (1-% pan); Home/End do not change a knob. This
comparison uses host-window/notification stubs and does not establish complete
mouse/keyboard behavior for every control or dialog.
The rotary display mapping additionally matches 1,566 executed Windows/macOS
cases: every legal index and clamped boundaries select one of 65 positions.
The sensitivity field shows the original bare number while its accessibility
text retains dB. Full text update/comparison additionally matches 1,648 cases
per original, including four-column numeric padding, all inputs/indices and
unchanged/invalid text. Eighty visibility cases verify the rate/view gate.
The HTML 192 kHz presentation retains bright captions/backgrounds while showing
fixed resting controls, empty values/meters and grid-only compressor plots.
Synthetic Playwright rate transitions cover control capture/edit cancellation
and cached-value restoration without device writes. Exact original bitmap/font
painting remains outside these checks; authored control artwork still differs.
The original knob/button right-release handlers also match 192 fixed hit/flag
cases and 96 unavailable-button cases with window/popup stubs. Wine child
bounds distinguish the actual skin from its painted label. HTML pointer actions
now use those regions, defer context menus to right release and end left dragging
across button chords. Chromium and native GTK/X11 input checks intercept
parameter replies and preserve the real raw state. The GTK host forwards
secondary buttons around the runtime's gesture reset; its DOM events remain
untrusted and are compared with actual GDK receipts. See the [GUI reference](gui-backend.md).
Both main views now match 302 original control rectangles at 96 DPI in browser
and GTK rendering. The read-only Windows inspector independently compares
128 Preamp and 174 Mixer rectangles against the numeric fixture, including
channel spacing, dials, switches, plots, meters, faders and both shared master
value displays. Original Windows/macOS selectors/getters and Windows refresh
code match 32 fixed master readout traces per reference. Browser response models
also verify unequal stereo values and thumb-release selection on all four buses.
Host visibility/painting and macOS notification scheduling are modeled.
Owner-painted artwork and native Windows fonts/DPI remain outside that comparison;
this is not complete visual parity. Patch Bay, Reverb, idle Auto-Sens and About also
match 44 original child rectangles and their client sizes at 96 DPI. The
read-only inspector checks the original process identity and dialog ownership;
Chromium/native WebKit verify the same numeric fixture and readable multiline
Auto-Sens failures. Original window decorations and font/bitmap rendering are
outside that comparison.
Active switch families now follow both references' resource construction:
pink/red for preamp, link, mute/solo, master/reverb and Auto-Sens; amber for
compressor bypass; blue for Multiple Direct Mixer. The matching Windows/macOS
resource bytes and face colors were compared; the shipped gradients are our
own CSS. Exact pressed/disabled frames and complete artwork parity remain open.
The output-jack display now projects physical routing readback to five colored
pair labels. Its assigned/other/unavailable decisions match 720 executed
Windows/macOS selector cases each, including all rates and routes and the
192 kHz Mixer-A override. Browser reply models check display/polling across all
four rates and mixers; GTK checks actual routing readback at the current rate.
This is display evidence, not physical output-signal validation.
Native Debian at 44.1/48 kHz also matches 144 actual Playwright pointer events across
the four knob families and all Shift/Ctrl combinations, with independent device
readback and complete raw recovery. This checks control values, not analog response.

Channel/master faders share the original Windows parameter, movement and click
handlers (`0x140021ed0`, `0x140019e00`, `0x14001f820`). The web controls match
288 fixed drag events, 504 thumb/track clicks and 18 key cases executed offline,
including 56-pixel travel, vertical-only movement, Shift/Ctrl, eight-index normal
steps, exact level coefficients and thumb hit-testing. Original geometry and
volume functions execute; host APIs, display interval getters and notifications
are stubbed. macOS range/scale and resource geometry agree. Numeric fixtures
are public; manufacturer binaries/assets remain private.
Native Debian at 44.1/48 kHz also passes 240 actual Playwright fader pointer events
across input/playback channels and both master groups, all Shift/Ctrl
combinations and thumb/track boundaries, with independent wire readback and
complete raw/clock preservation. Linked-master preview and delayed-reply checks
also pass. This does not establish physical gain response or native Windows
hardware behavior.

Windows wheel routing uses private message `0x80c8`, control handler
`0x140003610` and shared virtual handler `0x140022af0`. HTML dials/faders now
focus under the pointer and apply its fine-index steps independent of modifiers;
open popup menus retain ownership. Original code matches 3,060 fixed cases
across all control ranges, signed-short delta limits and all Shift/Ctrl masks.
Sub-notch deltas truncate separately. Focus/window/notification dependencies are
stubbed during reference execution. DOM native wheel units are preferred;
other-browser/touchpad normalization remains a separate compatibility limit.
Another 540 original wheel/drag events verify pointer-anchor continuity across
all control ranges. Native Debian at 48 kHz passes 90 actual wheel events across
nine knob/fader groups, with independent hardware readback, modifier/focus/bound
checks, popup ownership, zoom suppression and full raw/clock preservation.
Forty actual interleaved wheel/drag events pass on knob/fader adapters. The
installed native GTK checker verifies trusted X11 wheel input, focus transfer,
device change and restoration; arbitrary touchpad gestures remain unverified.

## Specific unresolved distinctions

MIDI framing matches 2,525 fixed Windows/macOS/ALSA transmit/receive cases with
host APIs stubbed. The private ALSA functions execute under ASan/UBSan; 30 input
guards and 33 Linux receive-completion cases also pass. The module now embeds its own MIDI helper:
twelve aborted-SysEx, eleven selected receive-filter and 194 status-recovery
cases match both references. Forty-eight actual output-scheduler boundary cases
verify two-event reservation and byte preservation across URBs with bounded
buffers. The system MIDI helper stays unchanged; the embedded variant has a
native 6.12 strict/Sparse build and source-package build. Windows private cable 2,
active-reader routing, URB-status guards
and FIFO boundaries also execute offline; cable 2 remains untested on hardware.
Its original public receive FIFO has an insufficient space guard, documented
as a reference limitation rather than behavior to reproduce.
Native Debian's quiet-DIN checker verifies independent
DIN input/output ownership during ten CTRL state queries and meter traffic,
without DIN writes and with full raw/clock preservation. Physical DIN loopback,
real USB fault recovery and scheduling remain open; see the
[protocol reference](usb-midi-protocol.txt).
The embedded helper passes native four-rate PCM meter/lifecycle checks,
including lease cleanup, with the complete original state/clock restored.

Windows preset loading now executes through its original GUI loader, state
copy and kernel bulk builder: 658 fixed cases match the Linux preparation,
including all file scalars, mono/opaque resets and all-rate cached prefixes.
Three modeled device/IOCTL/MIDI failures still produce a positive original Load
return; this is not proof of a successful Windows device write. Linux verifies
the complete reply. Native Debian also passes current/pan/attack CLI imports
and exact raw recovery at all four rates, with independent final clock/state
restoration. Persistent device memory and native interrupted-import recovery
remain unverified; see the [preset reference](preset-format.txt).

Windows menu resource 128 and the offline display model establish F10 focus,
Alt+D/V, root-menu arrow switching and first-match letter activation. The HTML
menu bar implements these with focus restoration, pointer switching and the
existing modal/connection/busy guards. Native Playwright checks open dialogs,
export settings and decline all five Initialize confirmations with full raw
state preserved. The installed GTK checker adds 21 trusted X11/WebKit menu/dialog key steps.
Other system-key/platform edge cases and complete interaction parity remain.

Secondary-dialog captions now support drag, cancel-aware Close and centered
reopening. Eight browser frame checks cover viewport bounds/resize and declined
nested initialization; trusted native X11/WebKit checks exercise caption
drag/close, reopening and Escape with raw state/clock preserved. Auto-Sens Close
uses the existing verified cancellation path and blocks duplicate pending closes;
the browser cancellation/failure test is explicitly simulated. The original
offline Patch Bay window verifies caption/system-menu flags and system-close
recreation, not full native Windows input/rendering parity.

Five original dialog resources independently match 27 numeric Tab stops and
their default pushbuttons. Wine/X11 observations additionally check initial
focus, cyclic Tab, closed/open-combo Enter and the original confirmation's
OK/Cancel behavior. The HTML dialogs implement these orders, default actions
and focus-dependent default-button styling. Native Debian's Playwright checks
exercise all five orders; confirmation acceptance and Auto-Sens Start/pending
responses are simulated and never sent to the hardware. The separate Linux
audio-settings and README dialogs remain platform-specific. The installed native
GTK checker verifies About's Tab wrap, Shift+Tab and default Enter with independent
raw/clock preservation; WebKit's unidentified reverse Tab retains physical code
`Tab`, which the shared dialog handler recognizes.

Windows power registration (`0x1400127e0`) selects the
[IAdapterPowerManagement interface](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/implementing-iadapterpowermanagement).
Allocation failure returns false; a negative PortCls registration result is not
propagated by the vendor wrapper.
Its original callback (`0x14000b730`) matches 1,848 fixed transitions, including
duplicate/invalid states, disconnected D0, stream/CTRL gates, worker fallback,
timer ordering, cached transport states and exact bulk-query/meter-switch frames.
The cached power state changes before dependent work; disconnected D0 still
caches D0, so a duplicate D0 call alone does not retry recovery. The port query
helper observes bit 1, the
[USB port-connected flag](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/usbioctl/ni-usbioctl-ioctl_internal_usb_get_port_status).
Eleven additional original query/capability cases accept D0–D3 and preserve other
capability bytes while setting
[SurpriseRemovalOK](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/content/wdm/ns-wdm-_device_capabilities).
This declared capability does not prove successful physical hotplug.
The macOS power-manager callbacks match 36 fixed events: duplicate notifications
are suppressed and their cached state changes after the optional callback.
`tools/verify-power-lifecycle.py` pins both binary hashes. USB/status, locks,
timers, worker exchange and ring operations are stubs; the actual callback and
Windows MIDI builders execute. Hardware suspend/reset recovery remains open.

```sh
python3 tools/verify-power-lifecycle.py doc/local/windows-kernel-analysis/RDWM1110.SYS \
  --macos doc/local/macos-analysis/audio-x86_64
```

The USB runtime-PM checker is prepared in `tools/check-runtime-pm.py`: regular-user
status/MIDI/quiet PCM probes with observed suspend, independent resume evidence
and full state/clock/policy recovery. Its fixed root-helper lease only changes
the target device's power policy, is bounded to 120 seconds and restores on
client loss. Offline policy tests are not hardware evidence; native runtime
suspend/resume remains unverified. See the kernel README for the helper setup.

Native small-buffer streaming is not established as stable under CPU load.
The aperiodic digital test passes all ten sources at 44.1 kHz with verified
256/1024-frame period/buffer. Normal-priority 48 kHz runs under concurrent load
also encounter a capture overrun at 128/512 and playback underrun at 256/1024;
full-state/rate recovery passes. These failures are not sample mismatches hidden
by the earlier PRBS11 period, and do not yet distinguish scheduling from transport.
With verified bounded RTKit SCHED_RR priority 5, all ten sources pass at
44.1/48/96 kHz with 128/512 frames: ten-second streams, 16,929,000 checked
samples and complete raw/rate recovery. Longer-duration performance remains open.

The offline Auto-Sens test peer used `threading.Event.set()` inside a signal
handler; a deterministic interrupted-lock probe demonstrates a deadlock. The
peer now uses flag-only handlers. Earlier peer exit-2 timeouts still lack captured
signal timing, so their cause remains unresolved; no production lifecycle defect
is established. Failure diagnostics retain child exit status, readiness,
requested/sent signal and stderr.

One native control-stress run failed while restoring a raw snapshot. Independent
outer recovery restored and verified the complete original state and rate.
An identical-fixture repeat and the full 304-case monitor sweep then passed;
the original failure's cause remains unknown. The checker now retains command
errors and raw-state differences on restoration failure. Successful reruns do
not establish that this intermittent issue is fixed.

Windows dialog 266 contains a placeholder 192 kHz warning saying 2-in/2-out,
but handler `0x14002c840` passes literal 4/4 to the localized formatter. Its
original argument instructions are checked by CPU execution. Isolated CPU
execution of Windows stream builder `0x1400269c0` establishes four capture and
four playback channels, matching macOS `CPortProps::BytesPerUsbFrame`
(`0x13856`), `CuWarnDlg::OnInitDialog` (`0x100028b16`) and the USB descriptors.
The physical signal assignments still need verification; matching channel counts
do not prove signal routing or integrity.

The Windows resource set also contains generic compressor-preset/16-user-preset
strings and controls with hidden styles. They require command/call-path evidence
before being classified as OCTA-CAPTURE features; the active compressor popup
selects the separate Bypass submenu. The unreferenced macOS knee table has no
identified scalar transmitter in the current command inventory.

USB interface 4, some status bits, external-clock policy and remaining platform
properties are still unresolved. Complete the kernel/property and GUI dispatch
audits before claiming that every original function has been recovered.
