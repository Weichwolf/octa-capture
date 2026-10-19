# Standalone ALSA module

The driver is built as **`snd-octa-capture.ko`**, using isolated copies of ALSA's USB audio and MIDI implementations. Debian's kernel and its source tree remain untouched. The module matches only `0582:0120`; its ALSA implementation exports no symbols, allowing the standard `snd-usb-audio` and `snd-usbmidi-lib` modules to coexist for other devices. The embedded MIDI helper closes interrupted SysEx and filters input events like the Windows/macOS references.

The embedded-MIDI module builds against Debian 6.12 with `W=1` and `-Werror`; its MIDI source passes Sparse. The earlier shared-MIDI baseline also built against WSL 6.18; the updated module still needs that target's build files and runtime verification. Both supported private MIDI source copies pass the offline reference comparison. The module exposes descriptor-based audio formats and both MIDI cables, with vendor clock control recovered from the macOS driver. Native Debian 6.12 passes clock/status reads and PCM meter/lifecycle checks at 44.1/48/96/192 kHz. Mixer A digital-return PRBS streams pass on all ten playback sources at 44.1/48/96 kHz, using ten-second streams, 1024-frame periods and an 8192-frame buffer. The 192 kHz mode has four capture/playback channels and no return 11/12; its PCM checks do not establish signal integrity.

WSL 6.18 needs the host workaround below. Simultaneous capture/zero-playback passes ten minutes per rate with 1024/8192-frame period/buffer, but smaller periods produced underruns and digital-return tests found sample gaps without XRUNs. Physical analog response, external synchronization and broader native latency/stability testing remain required.

Native Debian also passes ten seconds of simultaneous capture/zero-playback at
each rate with actual 256-frame periods and a 1024-frame buffer. Both running
ALSA streams' parameters and ownership are checked through `/proc/asound`;
silently negotiated alternatives fail the test. These short silent tests check
timing and XRUN diagnostics, not signal integrity, round-trip latency or sustained
performance at these smaller buffers.

The aperiodic digital-return checker removes the short PRBS11 alignment ambiguity.
It passes all ten sources at 44.1 kHz with verified 256/1024 frames, but
normal-priority 48 kHz tests under concurrent CPU load encounter XRUNs with
128/512 and 256/1024 frames. Sustained small-buffer stability remains unproven;
see the [protocol findings](../doc/usb-midi-protocol.txt) for verification scope.
With the checker's optional `--realtime` mode, all ten sources pass at
44.1/48/96 kHz using verified 128/512 frames and bounded RTKit SCHED_RR
priority 5. The ten-second streams compare nine-second windows; they do not
establish long-duration stability or absolute latency. The audio utilities
run as the regular user; no additional sudo permission is needed.

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

`kernel/linux-*.patch` and the shared `kernel/midi.patch` modify only the private module copy. Imported Linux files retain their original copyright and license notices; original additions use GPL-2.0-only.

The `midi-framing` CTest covers the portable input filter and SysEx-ending helper.
The optional [MIDI reference check](../doc/usb-midi-protocol.txt)
also compiles the actual prepared ALSA encoder, decoder and output scheduler with
ASan/UBSan. While a SysEx is pending, the output scheduler reserves two complete
USB events before consuming a byte, preventing a synthesized F7 plus F6 from
overrunning an URB. These are host tests; kernel concurrency and fault-injection
coverage remain separate requirements. No additional MIDI port is exposed.

`tools/check-audio-soak.py --duration 600 --period-size 1024 --rate 192000`
repeats an isolated quiet duplex run; `--rates` instead cycles all four rates.
It checks actual ALSA parameters, timing, XRUN diagnostics and clock state,
stops its streams on errors or normal signals, then restores the original rate
and verifies the complete raw state. Raw recovery is attempted independently
even if clock restoration fails; such a failure still makes the test fail.
A private raw recovery file is saved before any rate change. A forced kill or
disconnect can prevent automatic restoration; retain the reported snapshot.

Capture and playback share implicit-feedback endpoints. Their rate, format,
period size and period count must be compatible. Fixed-geometry diagnostics such
as `octa-audio-check` require an unused PCM device; an active sound server with
different periods can make their parameter negotiation fail with `EINVAL`.
Do not stop another application's stream to run these checks.

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
USB/sleep adapters. It checks all rate pairs, busy same-rate completion, clock flags,
readiness, bounded polling and USB errors/short/oversized replies at each stage.
Both original macOS rate setters use payload prefix `0x40`, also when completing
a busy same-rate request. The optional `tools/verify-usb-control.py` comparison
executes both original architectures with memory-only host APIs; see the
[protocol reference](../doc/usb-midi-protocol.txt). Busy/error states are simulated
offline, not forced on the physical device.
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

## USB runtime suspend/resume check

`tools/check-runtime-pm.py --rates` prepares a regular-user check of all four
rates. Close the GUI and competing ALSA/MIDI/USB clients first. It waits for two
consecutive `runtime_status=suspended` observations before each status, CTRL MIDI
and quiet PCM/idle-meter probe, then checks measured active time, full raw state
and clock. A durable recovery file is retained under `build/hardware/`.
The original state, clock and USB power policy are independently checked after
cleanup. A failed probe still fails even when restoration succeeds.

The optional development helper needs its new, fixed `runtime-pm` action:

```sh
sudo tools/grant-dev-access.sh "$USER" "$PWD/build/driver-native/snd-octa-capture.ko"
python3 tools/check-runtime-pm.py --rates
```

This updates the helper/sudoers rule without reloading the module. Only the
root-owned isolated Python policy helper runs as root; it accepts no path/device
arguments and requires exactly one `0582:0120` using `snd-octa-capture`. It leases
`power/control=auto` and a zero autosuspend delay for up to 120 seconds. EOF,
client pipe closure, handled signals or the deadline restore/read back the
original policy. A root-owned lock excludes concurrent leases; device identity
is rechecked before restoration. Pinned attribute descriptors prevent activation
writes from following a replaced bus path; backend recovery also refuses a
replacement unit.

The `runtime-pm` CTest uses fake sysfs files and fixed failure cases for binding,
policy restoration, partial writes, signals, client loss, timeout and replacement.
It never invokes sudo, touches real sysfs or runs a device test. Native/WSL
runtime-PM hardware results are not yet established. This checker also does not
prove system sleep, reset-resume, physical hotplug or USB/URB fault handling.

## Isolated sanitizer kernel build

The optional development fixture builds an x86_64 Linux 6.12 kernel with
KASAN, UBSAN, fault-injection support, dummy HCD and raw gadget support, then
builds `snd-octa-capture.ko` against its matching `Module.symvers`. It installs
and loads nothing on the development host. It is separate from standard tests
and does not run fuzzing. The `kernel-vm-build` CTest checks only patch application
and build-lock behavior; it never compiles or boots a kernel.

Use a private, extracted Linux source tree; do not use the source or build tree
of a running host build. The fixture applies documentation fixes from
`vm-linux-6.12.patch` to that private source, retaining `W=1` and `-Werror`.
LLVM 19 and the usual kernel build dependencies are required.

```bash
mkdir -p build/kernel-vm/source
tar -xf /usr/src/linux-source-6.12.tar.xz --strip-components=1 \
  -C build/kernel-vm/source
python3 tools/build-kernel-vm.py build/kernel-vm/source build/kernel-vm --jobs 2
```

Reuse the extracted source and the same command to resume a failed build.
A lock prevents concurrent fixture builds in the same output directory.
The kernel image is `build/kernel-vm/kernel-build/arch/x86/boot/bzImage`;
`build/kernel-vm/module-path` records the completed standalone module directory.
These are VM-only artifacts. Building them does not establish runtime sanitizer,
USB fault, suspend/resume or physical device coverage; those checks remain open.

For a bounded module lifecycle check, use QEMU, a statically linked x86_64
BusyBox (Debian package `busybox-static`) and KVM access:

```bash
python3 tools/check-kernel-vm.py build/kernel-vm --busybox /bin/busybox
```

The checker constructs a private initramfs, verifies the running guest's
sanitizer configuration and loads/unloads both ALSA modules together three
times. Kernel warnings, sanitizer reports, missing completion markers and a
120-second timeout fail the check. Console output stays in a fresh
`build/kernel-vm/check-*/console.log`. `--accel tcg` supports hosts without KVM;
`--timeout` can increase the bound to at most 600 seconds. `--qemu`, `--bios` and
`--firmware-dir` accept local tool/firmware paths. No host USB device or network is passed to
the guest. This checks module initialization/exit, not device-specific USB,
audio streaming, fault injection or suspend/resume. It does not run fuzzing.

The Linux 6.12.113 fixture builds successfully, including instrumented clock,
status and PCM meter objects. Its strict VM boot currently fails on UBSAN
alignment reports in the base kernel before loading our module (BIOS reads,
page-table list traversal, ACPI and runtime-constant text patching). Alignment
checking stays enabled and the checker rejects these reports. A clean kernel
sanitizer lifecycle result is not established; the base-kernel findings need
resolution before device-specific sanitizer coverage can be claimed.

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
