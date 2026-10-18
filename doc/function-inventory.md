# Reference functionality and verification gaps

Completeness is **not established**. This inventory separates behavior traced in
the Windows 1.5.3/macOS 1.5.9 references from implemented and hardware-tested
behavior. Resource names and function counts alone do not prove functionality.
The [protocol reference](usb-midi-protocol.txt) contains wire details and test limits.

## Reference coverage

- Windows x64: package imports/exports and resources inspected for `RDDP1110.EXE`,
  `RDCP1110.CPL`, `RDAS1110.DLL`, `RDAW1110.DLL`, `RDAH1110.EXE`, and `RDWM1110.SYS`.
  The control-panel RTTI/vtables and MFC message maps identify dialog handlers.
  The kernel binary has been imported and its functions/call sites inventoried;
  its complete IOCTL/property, USB and power-management behavior is not yet traced.
- macOS x86_64: control-panel transmitters, state decoders, preset operations,
  audio USB/clock/stream paths and MIDI routing have selected decompilation and
  offline reference execution. The separate Driver Setup parameter get/set/default
  paths are traced: buffer level, recording margin and timing use CoreAudio host
  properties. Remaining platform/property paths still require analysis.
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
- Windows x86 and macOS arm64 have not received an independent behavior comparison.
  Shared artwork does not establish shared control logic.
- `tools/ghidra/ExportReferenceInventory.java` exports function and call-site TSVs
  from an analyzed program. Indirect calls remain explicitly unresolved. Outputs,
  extracted resources and decompilation stay under ignored `doc/local/`.
- `ExportSelectedReferences.java` exports analyzed references into bounded address
  ranges, including their source functions/instructions. Missing references do
  not prove that an indirect target is unreachable.

## Functional comparison

“WSL tested” below means device communication/readback or gross stream timing;
it does not imply calibrated audio response or native Debian validation.

| Function | Original evidence | Current implementation / remaining work |
| --- | --- | --- |
| Four sample rates, channel packing, capture/playback | macOS audio driver; Windows settings/readme | Standalone module; WSL timing and ten-minute quiet duplex checks at each rate with 1024/8192-frame period/buffer. Digital-return tests detect intermittent discontinuities without XRUNs; smaller periods also produced underruns. Native Debian 6.12 passes all-rate PCM lifecycle checks and ten-source digital-return PRBS streams at 44.1/48/96 kHz (ten seconds per source; earlier requested 1024/8192, and independently verified 256/1024-frame period/buffer). Ten sources also pass at 48 kHz with verified 128/512 frames. All four native rates pass ten-second silent duplex with actual 256/1024 frames and kernel parameter/owner checks. Analog response, 192 kHz signal routing and sustained small-buffer integrity/stress remain. |
| Clock status and rate changes | macOS USB/status/property code; Windows settings handlers and connected footer | Kernel rate negotiation and read-only status API; native Debian phase/coherence, read-only access and concurrent reads pass. Connected footer and settings share rate/clock readback and busy guards; native Playwright verifies all four footer rates, PCM recovery and complete restoration. Missing status displays UNKNOWN (browser response simulation). External clock transitions and lock-loss behavior need hardware tests. |
| DIGITAL AUTO/OFF | macOS `Fsc_SetDIN`; device manual | Setting values and state transitions unresolved; no live write API. |
| MIDI DIN, CTRL isolation and endpoint routing | macOS MIDI plugin; Windows package/INF | ALSA MIDI endpoints and exclusive CTRL sessions. Physical DIN loopback, scheduling and transport fault coverage remain. |
| Preamp sensitivity, phase, low-cut | macOS setters/tables; Windows controls | All legal safe preamp/compressor indices pass writes, independent full-state readback and restoration on all eight native inputs at 48 kHz (2,924 cases including Hi-Z on 1/2). WSL representative changes also pass. Typed sensitivity parsing matches reference code and browser/hardware tests; physical gain remains. |
| Phantom power and Hi-Z | macOS setters; Windows controls; manual | Encoders and selector bounds implemented. Hi-Z changes/readback/restoration pass on inputs 1/2 at 48/192 kHz with phantom already off. Physical impedance response and phantom switching remain unverified. |
| Compressor enable and six parameters | macOS transmitters/table lookups; Windows eight-channel view | Every legal index on all eight channels passes native Debian write/readback at 48 kHz, including full raw isolation. WSL writes/readback also pass. Nonlinear tables and inverse faders match both references; 1,440 filled-curve/line cases match original CPU execution. Both drawing palettes/pen widths match. GUI controls and rendered pixels tested in Chromium/GTK. Physical dynamics response remains. |
| Compressor stereo link | macOS `TxPreLink`, manual | All four link cycles and 160 scalar coupling cases pass on WSL (strict/release) and native Debian (strict, 48 kHz); complete raw state is restored and ADC controls stay independent. |
| Generic compressor presets and knee data | Windows MENU 132 submenu 0; macOS data tables | Windows explicitly displays submenu 1 (Bypass), and macOS builds only the two bypass commands. Full macOS preset/knee tables have no analyzed references. No established preset/knee feature; unresolved indirect paths remain. |
| Auto-Sens | macOS start/stop and dialog selection code; Windows dialog 289/handlers | Selection, notification flow, finish/cancel, bounds and signal recovery implemented/tested. Idle selection, empty-input Start guard, Select/Clear All and 192 kHz limits match 3,072 original CPU cases per binary. Native Playwright verifies all-rate selection, real start/finish/cancel and confirmed automatic dialog close. Active checks follow the backend job; failed jobs remain visible. Early signals, client loss and shutdown have offline lifecycle tests. Observed operations now support same-connection verified Finish/Cancel with no invented pre-start backup; native 48 kHz/input-1 Playwright checks pass with silent physical outputs and full recovery. Twenty-two offline transport cases cover failures, bounds and reentrancy. Calibrated sensitivity, physical front-panel initiation, pre-connect activity and every original dialog state remain. |
| Four direct mixers, input/playback strips | macOS state/write/control code; Windows panels | Typed controls and scalar/stereo/mono/master-coupling tests. Mixer A digital return verifies ten playback sources, linear pan points, gain, mute and solo precedence; native Debian passes ten-source PRBS sample-order checks at 44.1/48/96 kHz, while intermittent WSL stream errors remain. Analog mixing, B–D signal response and automation remain. |
| Mixer A–D selection on device display | macOS `TxDMix`, mixer button logic | Typed API, CLI and browser tab commands implemented; all four frames match original CPU execution. WSL sends preserve audio state; physical LCD confirmation remains. |
| Reverb, sends and return | Both dialog resources; macOS DSP code | Scalar controls/readback implemented/tested. Read-only arrow controls and all pre-delay/time strings match both references; actual browser steps, bounds and all four banks pass with full recovery. Physical effect response remains. |
| Patchbay, Multi-Monitor, Direct Out | Both menus/dialogs; macOS sequences | Original routing/206-command Direct Out sequences implemented and WSL tested with full recovery. Header switch on/off matches both original handlers; routing-derived lamp/tabs and browser recovery tested. |
| Initialize All/MIC PRE/DIRECT MIX/REVERB/PATCHBAY | Both menus; macOS dispatch/`TxInit` | All sequences implemented; three DSP groups WSL tested. All/preamp power changes and 192 kHz behavior remain hardware-unverified. |
| Runtime snapshots and `.octa` files | Both original preset writers/loaders | Ver100 reference goldens, import/export and raw recovery implemented. Browser download/upload and GTK native Save/Open with actual hardware application pass at all four rates, including retained 192 kHz metadata, with complete raw recovery. Native browser coverage includes all four rates; installed native GTK Save/Open also passes at the current rate through desktop-portal dialogs. Unit-selection behavior remains. |
| Store/load internal device memory | macOS `TxSaveLoad`; Windows dispatch/confirmation strings | Commands and recovery implemented/fault-tested; persistent device memory not hardware-tested. |
| Input/driver meters, clip and peak hold | Both panel classes; macOS meter managers | Eight SysEx input meters and actual capture/playback PCM meters feed the GUI. Windows-compatible peak calculation, nonlinear meter scale, fall/hold/clip math match original CPU execution. Leased idle/normal PCM handover, all-rate transitions, exact peak retention and client-death lease recovery pass on native Debian and WSL; module reload also passes on WSL. Physical level calibration remains. |
| Device selection and identification blink | macOS device manager/`TxBlink`; Windows unit handling | sysfs enumeration, serial-bound device menu, reselection and automatic reconnect implemented. Real module unload/reload preserves state and restores MIDI/PCM meters on WSL and native Debian with exclusive access. Renumbering/absent-unit guards have offline tests. Identify frame matches original execution; CLI/browser sends preserve state. Physical blink/unplug and multiple physical units remain. |
| Context menus and bulk actions | Both menu resources/builders | Nested Initialize/All Ch., original ten-strip volume/send/pan scope and compressor bypass semantics implemented. Pointer/keyboard and ten-channel hardware changes/readback/recovery tested. Remaining interaction parity still requires tracing. |
| Driver settings / Linux audio information | Dialog 187 and `CDrvPropDlg` handlers; kernel IOCTL `0x222328` | Immediate rate selection, all four kernel/bulk rates, channel restrictions and PCM meter recovery pass through actual browser controls. The Linux dialog shows ALSA format/channel information and explains application/server buffer ownership. Close/Escape end the dialog without rate rollback. Native Debian and WSL browser checks pass; Windows host-buffer controls are omitted. |
| ASIO direct monitoring and low latency | Windows settings handlers/readme; `RDAS1110.DLL` | Direct-monitor gate, logical-input mapping, IOCTL 0x222144 and kernel sender traced. Codec matches 910 original frames; 2,080 DLL/kernel requests execute offline with host I/O stubs. Typed Linux monitor API passes 304 native hardware cases across all rates and link/mono modes with exact full-state readback/recovery; Playwright verifies displayed mute/pan/volume. Broadcast updates all ten stored inputs even at 192 kHz. Unmapped inputs 10/11 are rejected. Physical signal response, DAW hooks and broader low-latency behavior remain. |
| Windows sound/ASIO rate matching | Windows checkbox 1147, property collector/Apply, kernel `FsSyncOff` consumer | Inverse host preference traced through property offset 0x128 and WDM format-update gating; explicit GUI clock changes bypass this preference. Original CPU execution passes 768 full-property, 61 Apply and 128 rate-gate cases with host dependencies stubbed. Linux negotiates hardware rates through ALSA; application/server mixing/resampling policy remains external. No WDM checkbox is exposed. |
| VS EXPAND/two-unit synchronization | Windows checkbox 1173/handler and readme | Staged host flag and mutual exclusion with ASIO in Light traced; property collector verified by original CPU execution. Detection, validation, stream synchronization and routing incomplete; second-unit hardware validation unavailable here. |
| Disconnect, suspend/resume and errors | Both native driver lifecycles | Session/rollback fault tests and actual GUI recovery after module unload/reload pass on WSL and native Debian. Native Playwright checks confirm absent-card UI, retained serial selection, full state/rate and fresh MIDI/duplex PCM meters. PulseAudio discovery must be isolated for clock-preservation checks. Physical hotplug, USB/URB faults, power management and kernel sanitizer tests remain. |
| Signal-flow/About/warnings/preferences | Both menus, dialogs and local resources | Local HTTP panel, secondary dialogs and local README implemented. Persistent port-warning preference, inverse About/warning checkboxes and all 160 original warning transitions tested; browser hardware rate tests exercise them. GTK4/WebKitGTK embedding, native Save, external-navigation blocking and Exit pass under WSL/Xvfb. The same installed GTK checks also pass on native Debian, including portal-owned Save/Open. The desktop entry appears in Xfce Applications → Settings. Always on top is implemented on EWMH/X11, with actual WM confirmation, private persistence and offline WebKit/Xfce restart tests. Browser/unsupported hosts disable it. Complete visual/behavior parity remains. |

The Windows Always on top handler (`0x14002e510`) toggles its frame state and
writes `AlwaysTop` to the profile. Window creation (`0x14002e520`) reads it with
default zero; the menu update handler (`0x14002e650`) reflects that state. Linux
keeps this host preference separate from the device and audio-port warning.

## Specific unresolved distinctions

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
