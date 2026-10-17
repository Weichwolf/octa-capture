# Standalone ALSA module

The driver is built as **`snd-octa-capture.ko`**, using an isolated copy of ALSA's USB audio implementation. Debian's kernel and its source tree remain untouched. The module matches only `0582:0120`; its ALSA implementation exports no symbols, allowing the standard `snd-usb-audio` module to coexist for other devices. It uses the kernel's existing MIDI helper.

The module builds against Debian 6.12 and WSL 6.18 with `W=1` and `-Werror`. It exposes descriptor-based audio formats and both MIDI cables, with vendor clock control recovered from the macOS driver. CTRL MIDI communication and ALSA stream preparation with independent clock readback have passed at 44.1/48/96/192 kHz under WSL. Working audio streaming, native Debian hardware tests, and DKMS packaging remain pending.

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

## WSL USB/IP timing investigation

An independent libusb test with this module unloaded reproduces the 1/8
input rate at 48 kHz. Source analysis found a candidate host-side cause:
usbipd-win `aa3db8b` does not issue VBoxUSB `GET_DEVICE`; in VirtualBox
7.2.2 that request initializes the cached high-speed flag used for
isochronous scheduling. Without it, eight high-speed packets can be
scheduled eight Windows frames apart instead of one. This remains a
hypothesis until the Windows workaround and audio retest succeed.

With audio stopped and the device still attached to WSL, run in 64-bit
Windows PowerShell from the repository directory:

```powershell
.\tools\initialize-usbipd-high-speed.ps1
```

The helper selects only an attached `0582:0120` device through `usbipd state`
and sends the metadata query on its VBoxUSB interface. It does not install
a driver or reset/claim the device. `-BusId 2-3` optionally selects a bus.
It may need repeating after a fresh attach. Its PowerShell syntax and
embedded C# compile locally; Windows execution remains unverified.
Afterward rerun both Linux streaming checks at 48000 Hz.

References: [usbipd-win host revision](https://github.com/dorssel/usbipd-win/blob/aa3db8b82c4cb5071fd31bc54211606c70886912/Usbipd/VBoxUsb.cs)
and the official [VirtualBox 7.2.2 source archive](https://download.virtualbox.org/virtualbox/7.2.2/VirtualBox-7.2.2.tar.bz2),
`VBoxUsbRt.cpp` (`vboxUsbRtDispatchGetDevice`, isochronous scheduling),
`include/VBox/usblib-win.h` (`USBSUP_GETDEV`, `SUPUSB_IOCTL_GET_DEVICE`).
