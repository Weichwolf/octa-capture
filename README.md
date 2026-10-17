# octa-capture

Linux support for the **Roland OCTA-CAPTURE / UA-1010** (`0582:0120`), with device controls modeled on the Windows interface.

The target platform is native Debian. Development and local functional testing also take place on Debian 13 under WSL2 using USB/IP.

## Status

Protocol analysis and implementation are in progress. Available: a SysEx codec, an experimental USB clock probe, a bulk-state reader for preamps, four direct mixers, reverb and output routing, and validated preamp/compressor and DSP writes with readback through the CTRL MIDI port. The standalone module builds for Debian and WSL and loads under WSL. Timed audio capture/playback checks pass at all four rates under WSL after a [USB/IP host workaround](kernel/README.md#wsl-usbip-timing-investigation). Signal integrity and the remaining controls still need completion and hardware verification; driver work takes priority over the GUI.

The [standalone kernel module](kernel/README.md) also exposes a read-only ALSA device-status control. Debian users do not need to patch or rebuild their kernel.

## Planned interface

Reproduce the Windows GUI as closely as possible using HTML/CSS/JavaScript. Test it through a local HTTP server first, then embed it in GTK4/WebKitGTK. A separate backend controls the device; audio streaming remains in the driver, independent of the GUI.

## Development

The local setup script installs development/analysis tools, configures device permissions, and prepares WSL and native Debian build targets:

```bash
sudo bash /tmp/setup-octa-capture.sh --user "$USER"
# After successful completion:
source /etc/octa-capture-dev.env
```

The script currently exists only in `/tmp` and is not part of the repository. The WSL kernel build takes time and several GB of disk space. Modules must match the kernel version of each test system.

The native userspace code uses C++23, CMake/Ninja, LLVM 19, libusb, and ALSA. Debian packages are `clang-19`, `clang-tidy-19`, `clang-format-19`, `libclang-rt-19-dev`, `build-essential`, `cmake`, `ninja-build`, `pkg-config`, `libusb-1.0-0-dev`, `libasound2-dev`, `alsa-utils`, `shellcheck`, and `python3`.

```bash
make test       # strict warnings, clang-tidy, ASan/UBSan, and protocol tests
make lint       # format and full native static analysis
make release    # optimized build and tests
./tools/fuzz.sh 120  # offline protocol libFuzzer campaign, seconds after build
./tools/fuzz-midi.sh 120  # offline persistent CTRL session with simulated ALSA I/O
./build/strict/octa-probe   # read-only clock status, requires USB device access
./build/strict/octa-state   # query CTRL port 1; print validated bulk payload as JSON
./build/strict/octa-status  # kernel-owned rate/source/display status as JSON
./build/strict/octa-preset inspect settings.octa  # offline Ver100 file summary
./build/strict/octa-preset export > current.octa  # read current device state and export
./build/strict/octa-preset normalize settings.octa > normalized.octa
./build/strict/octa-preset prepare settings.octa current.syx > prepared.syx  # offline
./build/strict/octa-preset apply settings.octa recovery.syx  # recovery file must be new
./build/strict/octa-preset restore recovery.syx  # restore complete raw state
./build/strict/octa-auto-sense 1 1000 cancel auto-recovery.syx  # input 1, then restore
./build/strict/octa-preset restore-auto auto-recovery.syx  # cancel Auto-Sens before recovery
./build/strict/octa-preset routing multi-monitor routing-recovery.syx  # four monitor mixes
./build/strict/octa-preset routing playback playback-recovery.syx  # standard playback routes
./build/strict/octa-preset direct-out direct-recovery.syx  # original Directly Output the Inputs
./build/strict/octa-preset initialize reverb before-init.syx  # all/preamps/direct-mix/reverb/patchbay
./build/strict/octa-preset memory-store before-store.syx  # overwrite device memory
./build/strict/octa-preset memory-load before-load.syx  # load device memory
./build/strict/octa-meters 2000  # observe eight input levels for 2000 ms as JSON lines
python3 tools/check-meters.py   # hardware checks for meter shutdown and exclusive CTRL access
./build/strict/octa-session-check  # share meters, bulk queries and same-value DSP writes
./build/strict/octa-control 1 attack 8  # set channel 1 attack index, verify fresh bulk reply
./build/strict/octa-dsp --dry-run mixer input A 3 pan 16384  # encode without MIDI access
python3 tools/check-dsp.py            # read-only check; --write changes/restores DSP controls
python3 tools/check-preamps.py        # read-only check; --write tests and restores 80 values
python3 tools/check-auto-sense.py     # read-only; --write checks finish/cancel and signal recovery
python3 tools/check-routing-presets.py  # read-only; --write checks both presets and restores state
python3 tools/check-direct-out.py     # read-only; --write checks Direct Out and restores state
python3 tools/check-initialization.py  # read-only; --write checks three DSP reset groups and restores
python3 tools/check-status.py --stress --binary build/strict/octa-status  # kernel/C++ status
./build/strict/octa-audio-check 48000 prepare-capture  # configure/prepare without streaming
./build/strict/octa-audio-check 48000 capture          # transfer one second and check timing
```

Warnings and clang-tidy findings are errors. Sanitizer failures stop the tests. The separate `thread` preset is available for concurrent code. Userspace flags are not applied to kernel builds.

`octa-status` reads the kernel-owned ALSA status control through a validated, read-only C++ API. It reports rate, reference clock-source/display state and the unchanged raw packet; external-clock transitions remain hardware-unverified.

Device controls use the exclusive CTRL MIDI port. `MidiSession` shares it between meters, bulk queries, typed writes and Auto-Sens; it is confined to one thread and requires reopening after an unresolved query. `octa-meters` emits raw levels and reference dB values as JSON lines. `octa-session-check` checks shared meter/query traffic without changing the final bulk state.

`octa-dsp` controls mixer buses A–D, masters, reverb and individual output routes. Channels and output pairs start at 1; values are decimal wire indices/coefficients. `--dry-run` opens no device. Writes validate control bounds and verify readback; see the [protocol reference](doc/usb-midi-protocol.txt).

The [Ver100 preset tools](doc/preset-format.txt) support original `.octa` files, current-state export, offline preparation, live application and raw recovery. Live changes require a new private recovery file. `routing multi-monitor|playback` applies the original five-route presets, checks the entire state after every route and attempts recovery on failure/interruption. Patchbay presets are unavailable at 192 kHz. `memory-store/load` send persistent-memory commands, but persistence remains hardware-unverified; a runtime snapshot cannot undo overwritten device memory.

`direct-out` reproduces the original 206-command sequence, verifies each control and the complete final state, and attempts raw recovery on error/interruption. It assigns the four analog output pairs to their respective direct mixes and preserves the coaxial route, preamps and playback controls. It requires 44.1, 48 or 96 kHz and a new private recovery file.

`initialize all|preamps|direct-mix|reverb|patchbay` sends the original initialization sequence after saving a new private raw snapshot. All/Patch Bay also send the original Mixer A selection command; bulk readback does not confirm the LCD selection. The backend checks state bounds, preservation outside the selected group and repeated identical readback; failures/signals attempt complete recovery. Firmware supplies the defaults. This is the control-panel Initialize menu, distinct from the device's power-on Factory Reset procedure.

`octa-auto-sense` requires selected-input activity and completion notifications. Finish permits sensitivity changes on selected inputs and linked stereo peers; cancel restores the full raw snapshot. Errors/signals attempt cancellation and verified recovery on a fresh connection. `octa-preset restore-auto` provides manual cancel/recovery.

Hardware checkers are read-only by default; `--write` changes parameters and restores snapshots. Scalar checks exclude automatic phantom-power/Hi-Z/link/mono changes; Direct Out and Direct Mix initialization change mixer links. Check their reported recovery result and retain snapshots: disconnection or forced termination can prevent restoration. Current WSL evidence and remaining signal-quality/native-Debian requirements are in the protocol reference. Audio smoke checks discard input or play zeros and verify gross transfer timing at all four supported rates.

Fuzzing uses libFuzzer with ASan/UBSan, including test-only ALSA transport symbols; no malformed traffic reaches hardware. Corpora/artifacts stay in `build/fuzz/`. Kernel robustness still requires separate KASAN/UBSAN, fault-injection and descriptor/URB tests.

## Documentation

[Environment findings](doc/environment.txt) are dated snapshots. The [download manifest](doc/drivers-manifest.json) and [checksums](doc/SHA256SUMS) document the official Windows and macOS drivers downloaded locally. Verify the archives with:

```bash
(cd doc && sha256sum -c SHA256SUMS)
```

[Windows GUI inspection](doc/windows-gui-inspection.txt) records the original interface observed under Wine and the remaining gaps.

[USB/MIDI protocol](doc/usb-midi-protocol.txt) documents message framing, parameter addresses, and the distinction between static findings and hardware verification.

Manufacturer archives are local references and must not be included in project releases. Repository documentation, code comments, and UI text are written in English.

## License

Original project code is licensed under the **GNU General Public License, version 2 only** (`GPL-2.0-only`); see [LICENSE](LICENSE). Roland files are excluded and retain their own license terms.
