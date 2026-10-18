# octa-capture

Linux driver and control panel for the **Roland OCTA-CAPTURE / UA-1010**
(`0582:0120`). Native Debian is the target; development also uses Debian 13 in
WSL2 with USB/IP. Original project code is **GPL-2.0-only**.

The standalone ALSA module supports all four descriptor rates and separates MIDI
from CTRL. Debian users load `snd-octa-capture`; they do not patch their kernel.
The HTML panel runs against a real C++ backend and also embeds in GTK4/WebKitGTK.
It includes eight preamp/compressor strips, four mixers, routing, reverb,
Auto-Sens, settings import/export, device selection and live MIDI/PCM meters.
Nonlinear parameter tables and display math are checked against both original
Windows and macOS binaries.

**Complete parity is not established.** Device writes/readback, browser controls,
GTK/native Save/Open and timed audio transfers have passed on the connected WSL
unit. WSL audio has produced underruns and digital sample discontinuities even
without reported XRUNs. Native Debian hardware,
physical signal quality, external clock, DIN loopback, persistent memory and
two-unit operation still need verification. Linux audio settings show ALSA
information; applications or sound servers configure their own buffers and latency.
See the [function inventory](doc/function-inventory.md)
for implemented behavior and remaining gaps.

## Run the panel

```bash
make release
python3 tools/serve-gui.py --open
# Development binaries: add --bin-dir build/strict
```

Use an account with ALSA device access. The server binds only to `127.0.0.1`;
open its private launch link. Token authentication, Host/Origin checks and a
local-only content policy protect device operations. Audio stays in the kernel
and continues when the panel closes.

For the desktop window on Debian 13 (GTK 4.10 or later):

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
python3 tools/desktop-gui.py
```

The GTK host keeps the WebKit sandbox enabled, blocks external navigation and
uses native file dialogs. Right-click the panel's device name, press Shift+F10
or use Driver Settings to select a discovered unit. Serial identities survive
ALSA card renumbering, with automatic same-unit reconnection.

## Build and install

Userspace uses C++23, CMake/Ninja, LLVM 19, ALSA and libusb; kernel additions use
C/Kbuild. Development packages include `clang-19`, `clang-tidy-19`,
`clang-format-19`, `libclang-rt-19-dev`, `build-essential`, `cmake`, `ninja-build`,
`pkg-config`, `libusb-1.0-0-dev`, `libasound2-dev`, `alsa-utils`, `shellcheck`,
`python3`, `nodejs`, `node-undici` and `eslint`. Browser checks use `chromium`.

```bash
make test       # strict warnings, clang-tidy, ASan/UBSan and offline tests
make lint       # formatting and full native static analysis
make release    # optimized build and tests
cmake --install build/release --prefix "$HOME/.local"
~/.local/bin/octa-capture
# HTTP-only launcher: ~/.local/bin/octa-capture-http --open
```

[Kernel and Debian/DKMS instructions](kernel/README.md) cover matching headers,
module activation and packages. `VERSION` controls CMake/CPack versions. The
runtime package includes both GUI launchers, device tools and a desktop entry.
The DKMS package builds only the external module for its supported kernel
series. [WSL requires the documented USB/IP host workaround](kernel/README.md#wsl-usbip-timing-investigation)
on the tested usbipd/VBoxUSB combination.

For native Debian development, first build `build/driver-native/snd-octa-capture.ko`
against the running 6.12 kernel, then run `sudo ./tools/setup-native-dev.sh`.
This installs development tools, grants the invoking user device access and
configures the existing bounded module helper. USB monitor access is limited to
the connected device's bus and must be renewed after reboot.

## Verification

Hardware checkers are read-only by default unless their purpose explicitly
requires streaming. `--write` checks change device parameters and use recovery
snapshots. Power switching and persistent memory require separate attention;
a runtime backup cannot undo an overwritten device-memory preset.

```bash
./build/strict/octa-state             # complete CTRL state
./build/strict/octa-status            # kernel-owned clock status
python3 tools/check-status.py --stress --binary build/strict/octa-status
python3 tools/check-preamps.py --write       # active channels at the current rate
./build/strict/octa-audio-check 48000 capture  # quiet, timed transfer
python3 tools/check-pcm-meters.py --rates    # quiet meter/PCM lifecycle checks
python3 tools/check-mixer-audio.py --write --rates --keep-going  # digital DSP/signal checks
./tools/check-gui.sh                        # running-panel browser checks
./tools/fuzz.sh 120                         # offline protocol fuzzing
./tools/fuzz-midi.sh 120                    # simulated ALSA transport
./tools/fuzz-clock.sh 120                   # actual clock C with USB adapters
./tools/fuzz-kernel-status.sh 120           # actual status C with fault adapters
```

Fuzzing uses LLVM libFuzzer with ASan/UBSan and sends no malformed traffic to
hardware. Kernel code retains separate Kbuild/Sparse checks; host fuzzing does
not replace kernel sanitizers, USB fault injection or physical lifecycle tests.

## References

[USB/MIDI protocol](doc/usb-midi-protocol.txt), [GUI/backend](doc/gui-backend.md),
[PCM meters](doc/pcm-meters.md), [preset format](doc/preset-format.txt) and
[Windows GUI inspection](doc/windows-gui-inspection.txt) describe recovered
behavior, commands and evidence limits.

Official manufacturer downloads are listed in [the manifest](doc/drivers-manifest.json)
and [checksums](doc/SHA256SUMS). Downloads, extracted assets and decompilation
remain local references and are excluded from Git and releases. The interface
ships our own assets. Manufacturer files retain their original terms; see
[LICENSE](LICENSE) for project code.
