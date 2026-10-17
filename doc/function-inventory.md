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
  offline reference execution. The separate Driver Setup application and remaining
  platform/property paths still require analysis.
- Windows x86 and macOS arm64 have not received an independent behavior comparison.
  Shared artwork does not establish shared control logic.
- `tools/ghidra/ExportReferenceInventory.java` exports function and call-site TSVs
  from an analyzed program. Indirect calls remain explicitly unresolved. Outputs,
  extracted resources and decompilation stay under ignored `doc/local/`.

## Functional comparison

“WSL tested” below means device communication/readback or gross stream timing;
it does not imply calibrated audio response or native Debian validation.

| Function | Original evidence | Current implementation / remaining work |
| --- | --- | --- |
| Four sample rates, channel packing, capture/playback | macOS audio driver; Windows settings/readme | Standalone module; WSL timing tests at all four rates. Native Debian, signal/channel integrity and long-run stress remain. |
| Clock status and rate changes | macOS USB/status/property code; Windows settings handlers | Kernel rate negotiation and read-only status API. External clock transitions and lock-loss behavior need hardware tests. |
| DIGITAL AUTO/OFF | macOS `Fsc_SetDIN`; device manual | Setting values and state transitions unresolved; no live write API. |
| MIDI DIN, CTRL isolation and endpoint routing | macOS MIDI plugin; Windows package/INF | ALSA MIDI endpoints and exclusive CTRL sessions. Physical DIN loopback, scheduling and transport fault coverage remain. |
| Preamp sensitivity, phase, low-cut | macOS setters/tables; Windows controls | Scalar writes/readback WSL tested on all eight channels. Typed sensitivity parsing matches reference code and browser/hardware tests; physical gain remains. |
| Phantom power and Hi-Z | macOS setters; Windows controls; manual | Encoders and selector bounds implemented; power switching not hardware-tested. |
| Compressor enable and six parameters | macOS transmitters/table lookups; Windows eight-channel view | Raw-index writes/readback tested. Nonlinear tables, inverse faders and graph math match reference binaries/original CPU execution; GUI controls tested in Chromium. Physical dynamics response remains. |
| Compressor stereo link | macOS `TxPreLink`, manual | All four link cycles and 160 scalar coupling cases per build WSL tested; ADC controls stay independent. |
| Compressor presets and knee | Windows menu 132 has five preset labels; macOS has preset/knee data | Inspected compressor context handlers dispatch only Bypass Off/On All Ch., not the five preset labels. Other call paths and knee applicability remain unresolved. |
| Auto-Sens | macOS start/stop and dialog selection code; Windows dialog 289/handlers | Selection, notification flow, finish/cancel, bounds and signal recovery implemented/tested. Interactive browser start/finish/cancel and complete restoration also WSL tested; early signals, client loss and shutdown have offline lifecycle tests. Calibrated sensitivity and every original dialog state remain. |
| Four direct mixers, input/playback strips | macOS state/write/control code; Windows panels | Typed controls and bounds; scalar and stereo/mono/master-coupling WSL tests. Physical mixing, solo precedence and automation remain. |
| Mixer A–D selection on device display | macOS `TxDMix`, mixer button logic | Typed API, CLI and browser tab commands implemented; all four frames match original CPU execution. WSL sends preserve audio state; physical LCD confirmation remains. |
| Reverb, sends and return | Both dialog resources; macOS DSP code | Scalar controls/readback implemented/tested. Original dialog defaults, formatting and effect response still need comparison. |
| Patchbay, Multi-Monitor, Direct Out | Both menus/dialogs; macOS sequences | Original routing/206-command Direct Out sequences implemented and WSL tested with full recovery. Header switch on/off matches both original handlers; routing-derived lamp/tabs and browser recovery tested. |
| Initialize All/MIC PRE/DIRECT MIX/REVERB/PATCHBAY | Both menus; macOS dispatch/`TxInit` | All sequences implemented; three DSP groups WSL tested. All/preamp power changes and 192 kHz behavior remain hardware-unverified. |
| Runtime snapshots and `.octa` files | Both original preset writers/loaders | Ver100 reference goldens, import/export and raw recovery implemented. Browser download/upload, GTK native Save and hardware application tested with complete recovery. Unit-selection behavior remains. |
| Store/load internal device memory | macOS `TxSaveLoad`; Windows dispatch/confirmation strings | Commands and recovery implemented/fault-tested; persistent device memory not hardware-tested. |
| Input/driver meters, clip and peak hold | Both panel classes; macOS meter managers | Eight SysEx input meters and actual capture/playback PCM meters feed the GUI. Windows-compatible peak calculation, nonlinear meter scale, fall/hold/clip math match original CPU execution. Leased idle/normal PCM handover, rate transitions and module reload tested on WSL. Physical level calibration remains. |
| Device selection and identification blink | macOS device manager/`TxBlink`; Windows unit handling | sysfs enumeration, serial-bound device menu, reselection and automatic reconnect implemented. Real module unload/reload preserves state and restores MIDI/PCM meters in WSL. Renumbering/absent-unit guards have offline tests. Identify frame matches original execution; CLI/browser sends preserve state. Physical blink/unplug, multiple physical units and native Debian remain. |
| Context menus and bulk actions | Both menu resources/builders | Nested Initialize/All Ch., original ten-strip volume/send/pan scope and compressor bypass semantics implemented. Pointer/keyboard and ten-channel hardware changes/readback/recovery tested. Remaining interaction parity still requires tracing. |
| Windows driver settings | Dialog 187 and `CDrvPropDlg` handlers | All four rates applied through actual browser controls with kernel/bulk agreement, channel restrictions and real PCM meter recovery. Buffer slider, performance indications, complete Apply/Cancel/Default and persistence need Linux equivalents and validation. |
| ASIO direct monitoring and low latency | Windows settings handlers/readme; `RDAS1110.DLL` | Driver-side behavior and interactions with mixer state not fully traced. Linux integration remains. |
| Windows sound/ASIO rate matching | Windows checkbox 1147 and handler | Host policy, not established as a device command. Trace and define the corresponding Linux behavior. |
| VS EXPAND/two-unit synchronization | Windows checkbox 1173/handler and readme | Detection, validation, stream synchronization and routing incomplete; second-unit hardware validation unavailable here. |
| Disconnect, suspend/resume and errors | Both native driver lifecycles | Session/rollback fault tests and actual GUI recovery after module unload/reload pass. Selected serial identities are resolved before live operations. Physical hotplug, USB/URB faults, power management and kernel sanitizer tests remain. |
| Signal-flow/About/warnings/preferences | Both menus, dialogs and local resources | Local HTTP panel and independently authored assets implemented with secondary dialogs and a local usage README. GTK4/WebKitGTK embedding, native Save, external-navigation blocking and Exit also tested under WSL/Xvfb. Complete visual/behavior parity and preferences remain. |

## Specific unresolved distinctions

Windows dialog 266 contains a placeholder 192 kHz warning saying 2-in/2-out,
but its localized string uses runtime channel-count arguments. The Linux USB
transport currently exposes four capture/playback channels at 192 kHz. Determine
the actual Windows port map and physical signal assignments; neither the template
nor a successful four-channel transfer proves them.

The Windows resource set also contains generic compressor-preset/16-user-preset
strings and controls with hidden styles. They require command/call-path evidence
before being classified as OCTA-CAPTURE features. The macOS knee table likewise
has no identified scalar transmitter in the current command inventory.

USB interface 4, some status bits, external-clock policy and remaining platform
properties are still unresolved. Complete the kernel/property and GUI dispatch
audits before claiming that every original function has been recovered.
