# Standalone ALSA module

The driver is built as **`snd-octa-capture.ko`**, using an isolated copy of ALSA's USB audio implementation. Debian's kernel and its source tree remain untouched. The module matches only `0582:0120`; its ALSA implementation exports no symbols, allowing the standard `snd-usb-audio` module to coexist for other devices. It uses the kernel's existing MIDI helper.

The module builds against Debian 6.12 and WSL 6.18 with `W=1` and `-Werror`. It exposes descriptor-based audio formats and both MIDI cables, with vendor clock control recovered from the macOS driver. CTRL MIDI communication and ALSA stream preparation with independent clock readback have passed at 44.1/48/96/192 kHz under WSL. Timed single-direction streaming checks pass at all four rates under WSL after the host workaround below. Native Debian hardware tests, signal/channel verification, long-duration streaming and DKMS packaging remain pending.

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

## Device status

The private module provides an eight-byte, read-only, volatile ALSA CARD
control. Regular users with ALSA access can read it without libusb interface
detachment or disrupting audio/CTRL MIDI:

```bash
amixer -c OCTACAPTURE cget 'iface=CARD,name=OCTA-CAPTURE Status'
python3 tools/check-status.py
python3 tools/check-status.py --stress  # four concurrent readers for two seconds
```

The checker requires `amixer` from `alsa-utils`, validates packet boundaries
and verifies that ALSA rejects a write. Byte 0 is phase 1; bytes 3–5 contain
the little-endian sample rate. Other bytes remain raw until their meanings
are established. The driver seeks phase 0 followed by phase 1 with a
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

## WSL USB/IP timing investigation

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
