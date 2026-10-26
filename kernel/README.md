# Standalone ALSA module

The driver is built as **`snd-octa-capture.ko`**, using isolated copies of ALSA's USB audio and MIDI implementations. Debian's kernel and its source tree remain untouched. The module matches `0582:0120` and its VS EXPAND personality `0582:0121`; its ALSA implementation exports no symbols, allowing the standard `snd-usb-audio` and `snd-usbmidi-lib` modules to coexist for other devices. The embedded MIDI helper closes interrupted SysEx and filters input events like the Windows/macOS references.

EXP matching, status registration and GUI discovery follow the shared reference
paths. Physical EXP descriptors/audio/CTRL/clock behavior and multi-unit
aggregation remain unverified. Native hardware results below cover `0120` only.
Activation, udev permissions and deferred desktop-audio probing match both IDs;
isolated namespace checks also reject neighboring products and other vendors.

The embedded-MIDI module builds against Debian 6.12 with `W=1` and `-Werror`; its MIDI source passes Sparse. The private MIDI source passes the offline reference comparison. The module exposes descriptor-based audio formats and both MIDI cables, with vendor clock control recovered from the macOS driver. Native Debian 6.12 passes clock/status reads and PCM meter/lifecycle checks at 44.1/48/96/192 kHz. Mixer A digital-return PRBS streams pass on all ten playback sources at 44.1/48/96 kHz, using ten-second streams, 1024-frame periods and an 8192-frame buffer. The 192 kHz mode has four capture/playback channels and no return 11/12; its PCM checks do not establish signal integrity.

Physical analog response, external synchronization and broader native latency/stability testing remain required.

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

On native Debian, build files normally come from `linux-headers-$(uname -r)`; source comes from the corresponding `linux-source` package. Installation needs only the module build. The installer configures module loading order so the dedicated driver takes precedence while `snd-usb-audio` remains available for other devices.

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
`octa-capture-activate --check` reports binding without root or device writes.
Activation loads the installed module and rebinds only `0582:0120/0121` if generic
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
tree with matching headers.
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

Status reads and concurrent-reader checks pass on native Debian 6.12.
Module builds use matching native headers. After installing `sparse`, the new
source can be checked separately with the matching compiler/build directory:

```bash
make -C "$KDIR" M="$PWD/build/module-build" CC="$KERNEL_CC" \
  W=1 KCFLAGS=-Werror C=2 CF=-Wsparse-error octa-status.o
```

This static check passes for 6.12. Suspend/unplug,
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
Sparse for 6.12. A whole ALSA-tree Sparse run still reports existing
upstream Scarlett Boolean-expression issues. No diagnostics are suppressed.
Host fuzzing does not replace kernel sanitizer/URB fault testing.

## Experimental two-unit ALSA configuration

`tools/aggregate-audio.py` prepares a private, opt-in 24-in/20-out ALSA
configuration for one base unit and one EXP unit at 44.1/48/96 kHz. Its installed
entry point is `octa-aggregate`. Without `--rate`, it changes no hardware setting or desktop
profile and does not automatically modify `~/.asoundrc`. Both identities and two
fresh clock snapshots must agree before it creates a new mode-0600 file;
existing files and symlinks are refused. Run as the regular desktop user:

```bash
python3 tools/aggregate-audio.py --base serial:BASE_SERIAL --expansion serial:EXP_SERIAL \
  --write-config /tmp/octa-pair.conf
pasuspender -- env ALSA_CONFIG_PATH=/tmp/octa-pair.conf \
  arecord -D octa_expand -f S24_3LE -r 48000 -c 24 recording.wav
```

Replace the identities and rate with the actual pair. The EXP coaxial output
must feed the base coaxial input with DIGITAL AUTO. The base must report an
active external-clock display; an externally clocked EXP must also report
activation. These flags do not measure clock quality or phase alignment.
Regenerate the file after clock changes, hotplug or ALSA card renumbering.
Other applications/audio servers must release both PCM devices first.

The configuration uses ALSA's standard [multi and route plugins](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html),
with base channels first and EXP as the polling master. Each raw hardware leaf
restricts the exact rate, S24_3LE format and channel count; no resampler is added.
The GUI continues to control individual units. Physical EXP/aggregate timing,
routing, streaming and recovery remain unverified; this is an experimental
developer path.

An explicit joint rate change coordinates both native PulseAudio cards:

```bash
octa-aggregate --base serial:BASE_SERIAL --expansion serial:EXP_SERIAL \
  --rate 96000 --experimental --write-config /tmp/octa-pair-96k.conf
```

EXP must be the internal master. Both cards' clients move to temporary endpoints
before either card unloads. After native clock preparation, both stereo profiles,
serial identities and coherent clocks are checked before restoring any clients.
Temporary capture monitors are muted before routing clients or defaults to them;
held playback must not enter recording streams during the interruption.
Routes, endpoint/client volumes and mutes, defaults, remap chains and monitor recordings are preserved;
remaps across stereo pairs, multichannel clients and untracked module-owned audio
are rejected before mutation. Internal streams of captured remap modules must
match their owner, driver and master endpoint. Shared ALSA configuration writes
are serialized across cards. On
failure, files unwind in reverse order; the old clock and both cards must recover
before clients resume. Incomplete recovery retains temporary endpoints. SIGINT/
SIGTERM request recovery; forced termination cannot guarantee it. A new aggregate
file is written before audio changes and removed on failure if still owned.
Direct ALSA clients must release both devices. PipeWire coordination remains
unfinished. Headless use adds no audio-server graph. GTK/HTTP can explicitly
enable the same pair with `--pair-base serial:BASE --pair-expansion serial:EXP
--experimental-pair`; the existing device menu selects individual panels and
starts on EXP. The base retains its external rate; EXP offers only 44.1/48/96 kHz.
Both units' Auto-Sens activity is checked before a GUI rate change. Missing,
changed or incoherent companions disable it. Host shutdown cancels pending lock/
helper waits and allows recovery to finish. This GUI mode does not create the
combined PCM; the private file above is still required for direct aggregation.
Fixed graph/process/filesystem tests and an actual Python/native-helper rejection
in an empty device namespace pass. `python3 tools/check-pulse-pair.py` also uses
a private real PulseAudio server, chained remaps and persistent playback/recording
clients to check routes, levels, defaults, cancellation, client-gain failures, partial-resume recovery
and holding-node retention with automatic rescue enabled. A real PCM positive
control and one-second stereo recordings verify silence at the temporary capture
monitor while playback continues. Only USB/clock and
ALSA card creation are replaced with modeled stereo null endpoints; physical
joint server/hardware operation remains unverified. See `octa-aggregate(1)` for limits.

The C++ `PairPcmReservation` component provides nonblocking ownership of both
native capture/playback PCMs before a paired clock transaction. It checks
the opened handles' card/device/subdevice metadata, rejects card aliases and
releases partial acquisitions on failure. Fake-ALSA tests cover ownership moves,
busy/disconnect/allocation errors and inconsistent metadata under ASan/UBSan.
`PairClock` adds the transaction policy: two stable preflight snapshots, an
internally clocked EXP master, active external base, supported equal rates,
EXP-only preparation and two coherent completion snapshots. Settling is bounded
by forty polls and an eight-second deadline; interruption or uncertain prepare
failure triggers restoration of the previous rate. A failed restoration remains
distinct from the original failure. Scripted transport tests cover all supported
rate pairs, read/prepare failures, changing roles, deadlines and cancellation;
these are not physical clock tests.

An experimental native transaction is available separately after releasing both
units from all audio clients/servers:

```bash
octa-pair-clock --experimental 48000 BASE_CARD BASE_SERIAL EXP_CARD EXP_SERIAL
```

Replace the card IDs and serials with the selected physical pair. This path
requires an internally clocked EXP and prepares its capture PCM with exact
S24_3LE/12-channel/1024-period/8192-buffer settings, without starting or
transferring samples. It retains read-only status handles and checks both held
PCM directions around reads; an open PCM alone does not prevent ALSA from
reusing a disconnected card number. USB vendor/product/serial and the EXP master
setting are checked again before preparation, including recovery. JSON reports
whether a failed change recovered; SIGINT/SIGTERM request recovery after a
change attempt. Forced process termination cannot guarantee restoration.
Filesystem/fake-ALSA tests verify identity changes, fixed settings, error cleanup
and held-handle disconnect guards. Real paired transitions, physical timing and
streaming remain unverified. `octa-aggregate --rate HZ --experimental` invokes this
transaction with joint native PulseAudio coordination. Explicit paired GUI
launches use the same coordinator. See `octa-pair-clock(1)` for direct-use limits.

Reproduce the isolated sample-layout check with the real ALSA plugins:

```bash
cmake --build build/strict --target aggregate_pcm_fixture
python3 tools/check-aggregate-pcm.py
```

The non-installed fixture and its host retain strict warnings, clang-tidy,
ASan/UBSan and `-z defs`; both use the same shared sanitizer runtime. Bubblewrap
removes host USB/audio nodes, sysfs and networking. Six cases check EXP poll
selection and 4096 exact samples per channel in each direction at all three
rates, including ring wraps and playback start at a full buffer. Its PCM sources
use synthetic period advances and fixed capture samples, not a physical clock.

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
arguments and requires exactly one `0582:0120/0121` using `snd-octa-capture`. It leases
`power/control=auto` and a zero autosuspend delay for up to 120 seconds. EOF,
client pipe closure, handled signals or the deadline restore/read back the
original policy. A root-owned lock excludes concurrent leases; device identity
is rechecked before restoration. Pinned attribute descriptors prevent activation
writes from following a replaced bus path; backend recovery also refuses a
replacement unit.

The `runtime-pm` CTest uses fake sysfs files and fixed failure cases for binding,
policy restoration, partial writes, signals, client loss, timeout and replacement.
It never invokes sudo, touches real sysfs or runs a device test. Native
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
`vm-linux-6.12.patch` and defined-access fixes from
`vm-alignment-linux-6.12.patch` to that private source, retaining `W=1`,
`-Werror` and alignment checking. The latter uses unaligned-access helpers,
ACPICA's portable byte accesses, aligned no-fault copy operands and actual
list/RCU/SMP node alignment. Neither
patch changes the host kernel or the shipped standalone module sources.
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

The default minimal VM disables PM. A separate profile enables runtime PM,
system-suspend support, PM usage diagnostics and module function tracing,
with its own `-pm-octa-kasan-vm` release and matching module:

```bash
python3 tools/build-kernel-vm.py build/kernel-vm/source build/kernel-vm-pm \
  --power-management --jobs 2
python3 tools/check-kernel-vm.py build/kernel-vm-pm --busybox /bin/busybox \
  --usb-fixture build/release/kernel_usb_fixture --runtime-pm
```

The PM checker requires three suspended/status-wake cycles for each modeled
base/EXP unit, exact status readback, module-specific suspend/resume traces and
suspended-device teardown.
Missing, duplicate or reordered evidence fails verification. This tests neither
physical power management nor running audio/MIDI; system sleep remains separate.
The current module fails this check: the modeled base device stays active,
interface 2 retains a PM usage count of one, and no driver PM callbacks run.
The imported legacy MIDI code takes a lifetime
`usb_autopm_get_interface_no_resume()` reference in `__snd_usbmidi_create`.
Idle USB autosuspend is therefore not established; changing this policy requires
balanced MIDI-open/close, pending-URB and disconnect handling. Do not remove the
reference merely to make the status-only test pass.
Use a different output directory to preserve the minimal VM and its calibrated
allocation-failure cases. Do not overlap builds using the same private source.
`vm-pm-linux-6.12.patch` teaches the private kernel-doc parser to recognize sparse
lock annotations; the C parameters and lock annotations remain intact.
`vm-pm-alignment-linux-6.12.patch` uses a byte copy for the intentionally
unaligned ftrace trampoline pointer. `vm-pm-ring-alignment-linux-6.12.patch` selects
the trace ring buffer's existing eight-byte record layout when alignment
instrumentation is enabled. UBSAN alignment checking remains enabled.

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
the guest. This invocation checks module initialization/exit. It does not run fuzzing.

For additional deterministic USB coverage, build the static, optional guest helper
and pass it to the same checker:

```bash
cmake --preset release
cmake --build --preset release --target kernel_usb_fixture
python3 tools/check-kernel-vm.py build/kernel-vm --busybox /bin/busybox \
  --usb-fixture build/release/kernel_usb_fixture
```

The helper refuses to operate outside the private VM kernel and binds only
`dummy_udc.0`. Its synthetic descriptors select the real standalone driver's
composite quirk and expose a modeled phase-0/phase-1 status pipe at 48 kHz.
Three enumeration/disconnect cycles each run two simultaneous ALSA status
readers, checking 120 complete raw replies and read-only control metadata.
The generic ALSA module is unloaded before each simulated device appears.
Helper workers terminate with their gadget parent, so disconnect closes all
raw-gadget handles. Missing USB/read markers also fail the check.
An additional model changes only idProduct to `0121`: the checker verifies that
exact sysfs product ID, standalone binding, 20 complete status replies and
teardown. This tests EXP ID handling with modeled legal descriptors; it does not
validate the physical expansion unit's descriptors or streaming.

Seven fixed status-error scenarios check empty/7-byte replies (`EIO`), an
invalid phase (`EPROTO`), a phase-1 zero rate (`ENODATA`), four packets without
the required phase transition (`EAGAIN`, both missing-phase directions), and
an unanswered interrupt request (`ETIMEDOUT`). Every failed ALSA read must leave
the caller's complete byte array untouched; disconnect/unload must still finish.
The in-flight unplug case waits for usbmon to observe a pending status URB before
terminating the virtual gadget. It checks the corresponding completion and
ALSA error, then a fresh module/device lifecycle must pass another 20 good reads.
Dummy HCD can complete a removed endpoint with `EPROTO`; the test compares the
actual USB and ALSA errors instead of assuming `ESHUTDOWN`. A timeout cannot
substitute for this disconnect result.

One deterministic `failslab` case restricts failure to the current reader and
the `kmalloc-8` cache. Its kernel trace must contain an actual
`octa_status_get` frame; exactly one forced failure is required. The ALSA read
must return `ENOMEM` without changing any caller bytes, then recover on the same
open handle after injection is disabled. This covers the status DMA-buffer
allocation, not arbitrary allocation sites or runtime-PM recovery.

Two more fixed failures target PCM-meter allocation and the later audio-stream
allocation during probe. The helper first unbinds every claimed interface of
the modeled device, then uses per-task `/proc/self/fail-nth` with only the
`kmalloc-512` cache selected. Global failure probability remains zero. Each
probe must return `ENOMEM`, leave no registered card, then rebind and pass 20
status reads. The checker requires the corresponding confirmed kernel frames
and exactly three forced failures across the suite. Allocation ordinals are
specific to this VM kernel; shifted paths fail verification. These checks cover
probe cleanup before streaming, not arbitrary allocation sites or memory leaks.

At 48 kHz, the fixture also accepts legal S24_3LE hardware parameters with
256/1024-frame period/buffer sizes, then stalls audio alternate activation.
Playback and capture each repeat prepare, `HW_FREE` and reconfiguration twice
on the same open PCM handle. All four prepare calls must report `EPIPE`; the
gadget must observe exactly two alternate-2 rejections on each audio interface.
Another 20 valid status reads and device/module teardown must then succeed.
This covers setup failure and cleanup before audio transfer, not isochronous
data, running-stream faults or physical-device streaming.

This is a legal descriptor/status model, not a recording of the device's full
descriptors or a firmware emulator. MIDI packet sizes and strings are fixture
values. The model rejects audio streaming alternates and does not service MIDI,
physical audio, DSP controls or external clocks. Fixed malformed replies exist
only on the guest's dummy USB bus. This is deterministic testing, not fuzzing;
no malformed traffic is sent to physical hardware.

The patched Linux 6.12.113 fixture passes strict VM startup, three paired
generic/standalone module load/unload cycles and the synthetic USB/status
cycles without kernel warnings or KASAN/UBSAN reports.
Clock, status, monitor and PCM meter objects contain
KASAN and UBSAN alignment instrumentation. Streaming, other transport failures,
physical-controller unplug, broader allocation fault injection and power management
still need separate sanitizer checks.

## Passive USB timing observation

`tools/observe-usb-prbs.py --device 1-1 --seconds 360` passively observes the
PRBS11 test from `tools/check-mixer-audio.py` through usbmon. It keeps audio and
bounded URB associations in RAM. Each detected gap includes host-observed queue
depth, submission/completion gaps and latency in the preceding 50 ms. These are
host timestamps, not measurements of the device FIFO or proof of causation.
The summary includes submit failures (`E` events), incomplete associations and
monitor drops; kernel addresses and audio payloads are not logged. Exit 2 means
the required outgoing/return patterns were not verified, even if queue timing
was observed. The observer itself does not configure or silence any output.
