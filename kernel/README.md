# Standalone ALSA module

The driver is built as **`snd-octa-capture.ko`**, using an isolated copy of ALSA's USB audio implementation. Debian's kernel and its source tree remain untouched. The module matches only `0582:0120`; its ALSA implementation exports no symbols, allowing the standard `snd-usb-audio` module to coexist for other devices. It uses the kernel's existing MIDI helper.

The module builds against Debian 6.12 and WSL 6.18 with `W=1` and `-Werror`. It exposes descriptor-based audio formats and both MIDI cables, with vendor clock control recovered from the macOS driver. Native Debian 6.12 passes clock/status reads and PCM meter/lifecycle checks at 44.1/48/96/192 kHz. Mixer A digital-return PRBS streams pass on all ten playback sources at 44.1/48/96 kHz, using ten-second streams, 1024-frame periods and an 8192-frame buffer. The 192 kHz mode has four capture/playback channels and no return 11/12; its PCM checks do not establish signal integrity.

WSL 6.18 needs the host workaround below. Simultaneous capture/zero-playback passes ten minutes per rate with 1024/8192-frame period/buffer, but smaller periods produced underruns and digital-return tests found sample gaps without XRUNs. Physical analog response, external synchronization and broader native latency/stability testing remain required.

Use source matching the target kernel and its prepared build files, including `Module.symvers`:

```bash
# Prepare our module sources only; no kernel compilation or installation:
./tools/prepare-alsa.sh /path/to/linux-source build/module

# Alternatively, prepare and compile the module into a fresh directory:
KDIR=/path/to/kernel-build KERNEL_CC=gcc-13 \
  ./tools/build-module.sh /path/to/linux-source build/module-build

# Install for the running kernel and transfer only OCTA-CAPTURE interfaces:
sudo ./tools/install-module.sh build/module-build/snd-octa-capture.ko
```

On native Debian, build files normally come from `linux-headers-$(uname -r)`; source comes from the corresponding `linux-source` package. The full kernel build in the WSL setup supplies its missing build files; normal Debian installation needs only the module build. The installer configures module loading order so the dedicated driver takes precedence while `snd-usb-audio` remains available for other devices.

`kernel/*.patch` files modify only the private module copy. Imported Linux files retain their original copyright and license notices; original additions use GPL-2.0-only.

`tools/check-audio-soak.py --duration 600 --period-size 1024 --rate 192000`
repeats an isolated quiet duplex run; `--rates` instead cycles all four rates.
It checks timing, XRUN diagnostics and clock state, stops its streams on errors
or normal signals, then restores the original rate and complete raw state.
A private raw recovery file is saved before any rate change. A forced kill or
disconnect can prevent automatic restoration; retain the reported snapshot.

## DKMS package

Build a source package for one supported kernel series using its Linux source
tree. The resulting package contains the prepared ALSA sources and our changes;
end users need installed kernel headers, DKMS and the kernel compiler.

```bash
make release
cpack --config build/release/CPackConfig.cmake -B build/packages/runtime
cpack --config build/release/CPackSourceConfig.cmake -B build/packages/source
python3 tools/package-dkms.py /path/to/linux-6.12-source build/packages/driver

# On the native Debian target with a matching-series kernel:
sudo apt install "linux-headers-$(uname -r)"
sudo apt install ./build/packages/runtime/*.deb ./build/packages/driver/*.deb
sudo octa-capture-activate
```

Close audio/control applications before activation, then run `octa-capture` or
`octa-capture-http --open` as a regular user. The module package installs its
loading priority and device-specific audio-group/uaccess rules. DKMS rebuilds
only the external module when matching-series headers change. Each source package
is restricted to its prepared kernel series; `SOURCE-KERNEL` records its ALSA
baseline and `BUILD-SERIES` defines the supported headers. Other series are skipped.
Source generation and module builds also support the WSL 6.18 adapter.
`octa-capture-activate --check` reports binding without root or device writes.
Activation loads the installed module and rebinds only `0582:0120` if generic
`snd-usb-audio` already owns it. Other USB audio devices stay attached. The loading
priority handles subsequent boots; installing a modprobe soft dependency alone
does not replace an already bound generic driver.
On systemd systems, the package also requests a serialized activation service
when a newly added OCTA-CAPTURE control node belongs to the generic driver.
This covers hotplug after generic USB audio was already loaded for another unit.
Already dedicated and unrelated devices do not request rebinding. The service
survives replacement of its triggering generic sound card; it is not tied to
that card's lifetime. Systems without systemd use the manual activation command.
`python3 test/ModuleActivation.py` checks activation, other-device isolation and
failure paths in user/mount namespaces using bubblewrap and fake sysfs. It cannot
write host sysfs or load a real module. Physical binding lifecycle tests remain
separate.
`python3 test/ModuleHotplug.py` executes the actual udev matcher against synthetic
USB/sound hierarchies using umockdev and bubblewrap, with no host device nodes.
Install the optional test tools with `sudo apt install umockdev bubblewrap udev`.
Native Debian service/hotplug integration still requires a physical test.

The Debian 6.12.113 DKMS build and signing passed in an unprivileged development
tree with matching headers. The WSL 6.18 DKMS artifact also passed actual module
loading and browser/device checks at all four rates with complete raw restoration.
The userspace Debian package passed installed-layout,
actual browser/device and GTK/native-Save checks after extraction. Native Debian
package installation/removal and hardware checks remain required. Original ALSA
copyright/license notices and full module sources are retained in the DKMS package.

## Device status

The private module provides an eight-byte, read-only, volatile ALSA CARD
control. Regular users with ALSA access can read it without libusb interface
detachment or disrupting audio/CTRL MIDI:

```bash
amixer -c OCTACAPTURE cget 'iface=CARD,name=OCTA-CAPTURE Status'
python3 tools/check-status.py
python3 tools/check-status.py --stress  # four concurrent readers for two seconds
./build/strict/octa-status             # C++ backend, no amixer dependency
python3 tools/check-status.py --binary build/strict/octa-status --stress
```

The checker requires `amixer` from `alsa-utils`, validates packet boundaries
and verifies that ALSA rejects a write. Byte 0 is phase 1; bytes 3–5 contain
the little-endian sample rate. Byte 6 bit 0 selects the reference clock
source and byte 7 activates its display; other bits remain raw. These
interpretations follow original driver/GUI code; external-clock hardware
transitions remain unverified. The driver seeks phase 0 followed by phase 1 with a
nonzero rate, using at most four interrupt reads with 250 ms timeouts each.
A private mutex serializes readers; the standard USB-audio power/shutdown
guard and heap-backed buffers cover the transfer lifetime.

Status reads passed at all four rates under WSL, including concurrent
48 kHz duplex audio and CTRL/meter traffic. Module builds pass for both
supported kernel series. After installing `sparse`, the new source can be
checked separately with the matching compiler/build directory:

```bash
make -C "$KDIR" M="$PWD/build/module-build" CC="$KERNEL_CC" \
  W=1 KCFLAGS=-Werror C=2 CF=-Wsparse-error octa-status.o
```

This static check passes for 6.12 and 6.18. Native hardware, suspend/unplug,
KASAN/UBSAN and malformed USB/fault-injection tests remain required.

The `clock` CTest compiles the actual `kernel/octa.c` as host C against test-only
USB/sleep adapters. It checks all rate pairs, same-rate negotiation, clock flags,
readiness, bounded polling and USB errors/short/oversized replies at each stage.
`tools/fuzz-clock.sh 120` fuzzes that same C state machine with ASan/UBSan,
including invalid rates and status bytes. No hardware is opened and no malformed
traffic reaches USB. Host flags never enter Kbuild; these checks do not cover
kernel locking, URB lifetime or real kernel sanitizer behavior.

The `kernel-status` CTest and `tools/fuzz-kernel-status.sh 120` likewise execute
the actual `octa-status.c` with test-only USB/ALSA/allocator adapters. They check
phase ordering, malformed/short replies, descriptor validation, interrupted
guards, allocation/control-registration failures, heap DMA buffers, unchanged
caller output on failure and balanced resource ownership. Simulated guards check
cleanup paths; real locking, power management and USB fault behavior still need
kernel tests. All malformed traffic stays offline.

The portable `kernel/pcm-peaks.h` scanner is also compiled as kernel C with
W=1/-Werror and Sparse, and exercised offline by strict userspace tests,
original-driver CPU comparisons and `tools/fuzz-pcm-peaks.sh`. Streaming callbacks
now publish actual capture/playback peaks; a leased zero-output idle engine
yields to normal PCM opens. See [PCM meter controls](../doc/pcm-meters.md) for
the ABI and hardware checks. The new device sources and PCM/card hooks pass
Sparse for both kernels. A whole ALSA-tree Sparse run still reports existing
upstream issues (6.18 endpoint scoped-lock contexts, 6.12 Scarlett Boolean
expressions). No diagnostics are suppressed. Host fuzzing does not replace
kernel sanitizer/URB fault testing.

## WSL USB/IP timing investigation

`tools/observe-usb-prbs.py --device 1-1 --seconds 360` passively observes the
PRBS11 test from `tools/check-mixer-audio.py` through usbmon. It keeps audio and
bounded URB associations in RAM. Each detected gap includes host-observed queue
depth, submission/completion gaps and latency in the preceding 50 ms. These are
host timestamps, not measurements of the device FIFO or proof of causation.
The summary includes submit failures (`E` events), incomplete associations and
monitor drops; kernel addresses and audio payloads are not logged. Exit 2 means
the required outgoing/return patterns were not verified, even if queue timing
was observed. The observer itself does not configure or silence any output.

An independent libusb test with this module unloaded reproduces the 1/8
input rate at 48 kHz. The host-side workaround is verified on the tested setup:
usbipd-win `aa3db8b` does not issue VBoxUSB `GET_DEVICE`; in VirtualBox
7.2.2 that request initializes the cached high-speed flag used for
isochronous scheduling. Without it, eight high-speed packets are scheduled eight Windows frames
apart instead of one. Issuing GET_DEVICE restored correct transfer timing:
capture and playback passed at 44.1/48/96/192 kHz, each transferring one
second of frames in approximately 1002..1005 ms without ALSA errors.
This validates the workaround for usbipd-win 5.3.0-54 (`aa3db8b`) with
VBoxUSB 7.2.2.170484; it does not establish signal integrity or stability.

With audio stopped and the device still attached to WSL, run in 64-bit
Windows PowerShell from the repository directory:

```powershell
.\tools\initialize-usbipd-high-speed.ps1
```

The helper selects only an attached `0582:0120` device through `usbipd state`
and sends the metadata query on its VBoxUSB interface. It does not install
a driver or reset/claim the device. `-BusId 2-3` optionally selects a bus.
Repeat after a fresh attach if the host still omits this initialization.
The helper passed PSScriptAnalyzer and Windows execution on this setup.
If Windows execution policy blocks the UNC path, use a process-only option:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Debian\home\cosmo\Git\octa-capture\tools\initialize-usbipd-high-speed.ps1"
```

Adjust the distribution/repository path as needed. Afterward rerun both
Linux streaming checks at 48000 Hz. No Windows helper is needed on native
Debian.

References: [usbipd-win host revision](https://github.com/dorssel/usbipd-win/blob/aa3db8b82c4cb5071fd31bc54211606c70886912/Usbipd/VBoxUsb.cs)
and the official [VirtualBox 7.2.2 source archive](https://download.virtualbox.org/virtualbox/7.2.2/VirtualBox-7.2.2.tar.bz2),
`VBoxUsbRt.cpp` (`vboxUsbRtDispatchGetDevice`, isochronous scheduling),
`include/VBox/usblib-win.h` (`USBSUP_GETDEV`, `SUPUSB_IOCTL_GET_DEVICE`).
