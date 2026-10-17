# octa-capture

Linux support for the **Roland OCTA-CAPTURE / UA-1010** (`0582:0120`), with device controls modeled on the Windows interface.

The target platform is native Debian. Development and local functional testing also take place on Debian 13 under WSL2 using USB/IP.

## Status

Protocol analysis and implementation are in progress. Available: a SysEx codec, an experimental USB clock probe, and a bulk-state reader for the CTRL MIDI port. The standalone module builds for Debian and WSL and loads under WSL; device communication, audio streaming, and the GUI still need completion and hardware verification.

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
./build/strict/octa-probe   # read-only clock status, requires USB device access
./build/strict/octa-state   # query CTRL port 1; print validated bulk payload as JSON
```

Warnings and clang-tidy findings are errors. Sanitizer failures stop the tests. The separate `thread` preset is available for concurrent code. Userspace flags are not applied to kernel builds.

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
