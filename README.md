# octa-capture

Linux support for the **Roland OCTA-CAPTURE / UA-1010** (`0582:0120`), with device controls modeled on the Windows interface.

The target platform is native Debian. Development and local functional testing also take place on Debian 13 under WSL2 using USB/IP.

## Status

Protocol analysis and implementation are in progress. Available: a SysEx codec, an experimental USB clock probe, a bulk-state reader for preamps, four direct mixers, reverb and output routing, and validated preamp/compressor and DSP writes with readback through the CTRL MIDI port. The standalone module builds for Debian and WSL and loads under WSL. Timed audio capture/playback checks pass at all four rates under WSL after a [USB/IP host workaround](kernel/README.md#wsl-usbip-timing-investigation). Signal integrity and the remaining controls still need completion and hardware verification; driver work takes priority over the GUI.

The driver will be a [standalone kernel module](kernel/README.md); Debian users will not need to patch or rebuild their kernel.

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

The native userspace code uses C++23, CMake/Ninja, LLVM 19, libusb, and ALSA. Debian packages are `clang-19`, `clang-tidy-19`, `clang-format-19`, `libclang-rt-19-dev`, `build-essential`, `cmake`, `ninja-build`, `pkg-config`, `libusb-1.0-0-dev`, `libasound2-dev`, and `shellcheck`.

```bash
make test       # strict warnings, clang-tidy, ASan/UBSan, and protocol tests
make lint       # format and full native static analysis
make release    # optimized build and tests
./tools/fuzz.sh 120  # offline libFuzzer campaign, seconds after build
./build/strict/octa-probe   # read-only clock status, requires USB device access
./build/strict/octa-state   # query CTRL port 1; print validated bulk payload as JSON
./build/strict/octa-meters 2000  # observe eight input levels for 2000 ms as JSON lines
python3 tools/check-meters.py   # hardware checks for meter shutdown and exclusive CTRL access
./build/strict/octa-control 1 attack 8  # set channel 1 attack index, verify fresh bulk reply
./build/strict/octa-dsp --dry-run mixer input A 3 pan 16384  # encode without MIDI access
python3 tools/check-dsp.py            # read-only check; --write changes/restores DSP controls
python3 tools/check-preamps.py        # read-only check; --write tests and restores 80 values
./build/strict/octa-audio-check 48000 prepare-capture  # configure/prepare without streaming
./build/strict/octa-audio-check 48000 capture          # transfer one second and check timing
```

Warnings and clang-tidy findings are errors. Sanitizer failures stop the tests. The separate `thread` preset is available for concurrent code. Userspace flags are not applied to kernel builds.

`octa-meters` holds one CTRL connection while observing input levels, with a duration of 1..60000 milliseconds (default 5000), a three-second inactivity timeout and an optional hardware MIDI port. It emits raw 14-bit values and reference dB display values as JSON lines. The backend attempts to disable meter output after normal completion, callback cancellation and errors. SIGINT/SIGTERM and a closed output pipe shut down through that path. Forced termination or disconnection can prevent the stop command from reaching the device. CTRL access is exclusive while this standalone observer runs. `tools/check-meters.py` reproduces normal, signal, broken-pipe and competing-open checks, validates JSON values, checks for a quiet CTRL port after each case, and compares the complete bulk state before/after. It requires `amidi`; `--bin-dir` and `--port` select a build and device.

`octa-dsp` controls the direct mixers, masters, reverb and output routing; invoke it without arguments for syntax. Mixer buses are A..D, channels and output pairs start at 1, and values are decimal wire coefficients/indices. `--dry-run` prints the encoded frame without opening a MIDI device. Writes validate reference GUI bounds before device access and require a fresh bulk reply containing the requested value. Stereo-pair changes use the first channel of the pair. This is a protocol interface; GUI gestures that coordinate several controls still need implementation.

The `fuzz` preset instruments the protocol library with libFuzzer and ASan/UBSan. Synthetic seeds cover framing, stream recovery, size limits, bulk state, and parameter notifications; invariants check round trips and rejection without state changes. The corpus and crash artifacts remain in `build/fuzz/`. No fuzz input is sent to hardware. These campaigns do not establish kernel robustness; KASAN/UBSAN, fault injection, and malformed USB descriptor/URB tests on a test kernel remain required.

The Python 3 preamp hardware checker tests ten parameters on all eight unlinked channels, changes each by one index, independently checks the complete preamp bank, and restores each value even if readback fails. Phantom power, Hi-Z, and stereo links are excluded from automated changes. A recovery snapshot is saved in `build/hardware/preamp-before.json`; interruption or device disconnection can prevent restoration, so check the reported result. All CLI values are wire indices; see the [protocol bounds](doc/usb-midi-protocol.txt).

`tools/check-dsp.py --write` tests mute, solo, pan and volume on all mixer channels, master volumes, reverb sends/return/type and parameter banks, and output routes. It checks all decoded fields for unexpected changes and the complete raw payload after each restoration. All 361 checks passed under WSL, including expected stereo coupling. A snapshot is saved in `build/hardware/dsp-before.json`. Link/mono switches and persistent/reset operations are excluded. Parameter readback does not prove the physical DSP effect. As with the preamp checker, disconnection or interruption can prevent restoration.

`octa-audio-check` accepts 44100/48000/96000/192000 Hz, `capture`, `playback`, `prepare-capture`, or `prepare-playback`, and an optional `hw:` PCM device. It uses packed 24-bit samples and descriptor channel counts. Streaming checks discard input or play zeros, reject ALSA errors, and require one second of frames to complete in 800..2000 ms, with a four-second transfer deadline. Preparation success is reported separately. This smoke test checks gross timing and transfer completion; channel identity, signal integrity, latency, and long-duration stability need separate tests. The WSL timing checks pass after the host workaround; native Debian hardware checks are still required.

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
