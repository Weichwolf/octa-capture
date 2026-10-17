# Project context

- Repository language is English: documentation, code comments, commit messages, and UI text. Continue speaking German with the user unless asked otherwise.
- Roland OCTA-CAPTURE / UA-1010, USB `0582:0120`. Linux support must work on native Debian **and** in this WSL2 development environment using USB/IP.
- Investigate and extend existing ALSA support in `snd-usb-audio` first. Device detection does not prove working audio streaming.
- Reproduce the Windows GUI's appearance and behavior as closely as possible, including secondary dialogs. Start with HTML/CSS/JavaScript served over local HTTP; later embed the same interface in GTK4/WebKitGTK.
- Separate the GUI, device backend, and time-critical audio streaming. Audio belongs in the driver; validate state changes in the backend. Run the GUI/backend as a regular user.
- Bind HTTP to loopback; protect control requests with a token and Host/Origin checks. Load only local web content. A GTK message bridge can later replace HTTP.

# Environment and references

- Development: Debian 13, x86_64, WSL kernel `6.18.54.1-microsoft-standard-WSL2`. The connected device is already bound to `snd-usb-audio`.
- `doc/`: official Windows 1.5.3/macOS 1.5.9 downloads, sources/hashes in `drivers-manifest.json`, checksums in `SHA256SUMS`, findings in `environment.txt`.
- Findings are dated snapshots: recheck tools, permissions, and running builds before relying on them.
- `/tmp/setup-octa-capture.sh` installs tools/permissions and prepares matching WSL build files. The kernel build was running when recorded; check its current status. After successful completion, `source /etc/octa-capture-dev.env` provides `KDIR`, `KERNEL_CC`, and `KDIR_NATIVE`. `/tmp` is temporary.
- Ghidra 12.1.2 is installed as a Snap: `ghidra.analyzeHeadless`. Decompilation of a custom test function passed. LLVM 19, GNU Binutils, and GDB are available.
- Wine GUI observations: `doc/windows-gui-inspection.txt`; screenshots in ignored `doc/local/windows-gui/`. Main preamp/compressor view, Driver menu, and About dialog captured; Direct Mixer remains unobserved because the GUI reports no device.

# Working practices

- Apply strict quality gates modeled on `~/Git/outshine`: warnings are errors, clang-format, clang-tidy with zero findings and a 120-line function limit, strict compiler/linker flags, and ASan/UBSan. Run TSan separately for concurrent code. Never weaken checks or add baselines to hide findings. Kernel code uses Kbuild and kernel-specific analysis/sanitizers; do not pass userspace sanitizer/linker flags into Kbuild.
- Distinguish protocol observations, hypotheses, and verified behavior; record relevant findings in `doc/`.
- Keep USB/MIDI framing, addresses, encodings, and evidence in `doc/usb-midi-protocol.txt` as analysis advances.
- Commit and push each verified increment to `upstream`; exclude proprietary downloads, extracted assets, and decompilation output.
- Build kernel modules against matching build files, including `Module.symvers`. Do not load native Debian target modules into WSL.
- Verify audio/MIDI, sample rates, and device controls separately on WSL and native Debian. USB/IP may affect results.
- Manufacturer archives and extracted manufacturer files are local references and must not be committed. Use our own implementation and GUI assets.
- Original project code is licensed under `GPL-2.0-only` (see `LICENSE`). Add matching SPDX identifiers to new source files; preserve existing notices in imported files. Manufacturer files retain their own terms.
