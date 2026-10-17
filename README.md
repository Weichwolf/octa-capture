# octa-capture

Linux support for the **Roland OCTA-CAPTURE / UA-1010** (`0582:0120`), with device controls modeled on the Windows interface.

The target platform is native Debian. Development and local functional testing also take place on Debian 13 under WSL2 using USB/IP.

## Status

Protocol analysis and implementation are in progress. Available: a SysEx codec, an experimental USB clock probe, a bulk-state reader, and validated preamp/compressor writes with readback through the CTRL MIDI port. The standalone module builds for Debian and WSL and loads under WSL. Audio streaming and the remaining controls need completion and hardware verification; driver work takes priority over the GUI.

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
./build/strict/octa-control 1 attack 8  # set channel 1 attack index, verify fresh bulk reply
python3 tools/check-preamps.py        # read-only check; --write tests and restores 80 values
```

Warnings and clang-tidy findings are errors. Sanitizer failures stop the tests. The separate `thread` preset is available for concurrent code. Userspace flags are not applied to kernel builds.

The `fuzz` preset instruments the protocol library with libFuzzer and ASan/UBSan. Synthetic seeds cover framing, stream recovery, size limits, bulk state, and parameter notifications; invariants check round trips and rejection without state changes. The corpus and crash artifacts remain in `build/fuzz/`. No fuzz input is sent to hardware. These campaigns do not establish kernel robustness; KASAN/UBSAN, fault injection, and malformed USB descriptor/URB tests on a test kernel remain required.

The Python 3 hardware checker tests ten parameters on all eight unlinked channels, changes each by one index, independently checks the complete preamp bank, and restores each value even if readback fails. Phantom power, Hi-Z, and stereo links are excluded from automated changes. A recovery snapshot is saved in `build/hardware/preamp-before.json`; interruption or device disconnection can prevent restoration, so check the reported result. All CLI values are wire indices; see the [protocol bounds](doc/usb-midi-protocol.txt).

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
