# Standalone ALSA module

The driver is built as **`snd-octa-capture.ko`**, using an isolated copy of ALSA's USB audio implementation. Debian's kernel and its source tree remain untouched. The module matches only `0582:0120`; its ALSA implementation exports no symbols, allowing the standard `snd-usb-audio` module to coexist for other devices. It uses the kernel's existing MIDI helper.

The module builds against Debian 6.12 and WSL 6.18 with `W=1` and `-Werror`. It exposes descriptor-based audio formats and both MIDI cables, with vendor clock control recovered from the macOS driver. Hardware validation and DKMS packaging remain pending.

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
