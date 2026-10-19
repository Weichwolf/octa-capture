# Project context

- Repository language is English: documentation, code comments, commit messages, and UI text. Continue speaking German with the user unless asked otherwise.
- Roland OCTA-CAPTURE / UA-1010, USB `0582:0120`. Linux support must work on native Debian **and** in this WSL2 development environment using USB/IP.
- Investigate and extend existing ALSA support in `snd-usb-audio` first. Device detection does not prove working audio streaming.
- Deliver a standalone `snd-octa-capture` module for Debian; users must not patch or rebuild their kernel. ALSA source changes belong only in the private module copy.
- Current user priority: deliver the working HTML GUI on the verified device backend while continuing the remaining driver/reference audit. Keep unverified functions explicit; do not postpone all GUI work until every hardware limitation is resolved.
- Reproduce the Windows GUI's appearance and behavior as closely as possible, including secondary dialogs. Start with HTML/CSS/JavaScript served over local HTTP; later embed the same interface in GTK4/WebKitGTK.
- Separate the GUI, device backend, and time-critical audio streaming. Audio belongs in the driver; validate state changes in the backend. Run the GUI/backend as a regular user.
- Bind HTTP to loopback; protect control requests with a token and Host/Origin checks. Load only local web content. A GTK message bridge can later replace HTTP.

# Environment and references

- Development now runs on native Debian 13, kernel `6.12.113+deb13-amd64`, with direct USB. Earlier WSL development used `6.18.54.1-microsoft-standard-WSL2`. Recheck the active module binding; copied WSL build artifacts and process IDs are not valid native runtime evidence.
- `doc/`: official Windows 1.5.3/macOS 1.5.9 downloads, sources/hashes in `drivers-manifest.json`, checksums in `SHA256SUMS`, findings in `environment.txt`.
- With usbipd-win `aa3db8b` / VBoxUSB 7.2.2, run the Windows metadata helper after attachment to initialize its high-speed cache (see `kernel/README.md`). This fixed the 1/8 streaming rate; WSL timed checks pass at all four rates but intermittent digital sample gaps remain. Native Debian has passed all-rate PCM meter/lifecycle checks and ten-source digital-return PRBS checks at 44.1/48/96 kHz with 1024/8192-frame period/buffer. These do not establish physical analog response or broader latency/stability.
- The module exposes the read-only ALSA CARD control `OCTA-CAPTURE Status` (8 bytes). `octa-status` reads it through the C++ backend; `tools/check-status.py --stress --binary build/strict/octa-status` validates concurrent reads. Keep unmapped status bits raw.
- Findings are dated snapshots: recheck tools, permissions, and running builds before relying on them.
- `tools/setup-native-dev.sh` installs native tools and development permissions after `build/driver-native/snd-octa-capture.ko` is built for the running kernel. It writes `/etc/octa-capture-dev.env` and configures the root helper with that native module path. Never install a WSL module on native Debian.
- LLVM 19, GNU Binutils, GDB and Ghidra 12.1.2 are available natively. `ghidra.analyzeHeadless` resolves to the user-local installation under `~/.local/opt/ghidra_12.1.2_PUBLIC`; existing projects open read-only. Manufacturer analysis remains under `doc/local/`.
- Wine GUI observations: `doc/windows-gui-inspection.txt`; screenshots in ignored `doc/local/windows-gui/`. Disconnected preamp, Driver menu and About captured. A private offline device-interface model also reveals Direct Mixer, Device menu and the connected footer; this does not verify original Windows hardware control.

# Working practices

- Apply strict quality gates modeled on `~/Git/outshine`: warnings are errors, clang-format, clang-tidy with zero findings and a 120-line function limit, strict compiler/linker flags, and ASan/UBSan. Run TSan separately for concurrent code. Never weaken checks or add baselines to hide findings. Kernel code uses Kbuild and kernel-specific analysis/sanitizers; do not pass userspace sanitizer/linker flags into Kbuild.
- Run fuzz tests only when explicitly requested by the user; never include fuzzing in standard test runs or start it autonomously. When requested, fuzz untrusted protocol and parameter inputs with sanitizers and preserve regression cases for findings. Fuzz host parsers offline, never send malformed test traffic to the device. Kernel robustness needs separate kernel sanitizer/fault-injection testing.
- Distinguish protocol observations, hypotheses, and verified behavior; record relevant findings in `doc/`.
- Keep documentation concise: protocol facts, usage, and material limitations. Do not maintain a diary or record routine work and individual test runs.
- Keep USB/MIDI framing, addresses, encodings, and evidence in `doc/usb-midi-protocol.txt` as analysis advances.
- Commit and push each verified increment to `upstream`; exclude proprietary downloads, extracted assets, and decompilation output.
- `VERSION` drives CMake/CPack. Installed GUI entry points are `octa-capture`/`octa-capture-http`; `tools/package-dkms.py` bundles a supported ALSA snapshot as a standalone DKMS source package. Kernel/package instructions are in `kernel/README.md`.
- Build kernel modules against matching build files, including `Module.symvers`. Do not load native Debian target modules into WSL.
- Verify audio/MIDI, sample rates, and device controls separately on WSL and native Debian. USB/IP may affect results.
- Manufacturer archives and extracted manufacturer files are local references and must not be committed. Use our own implementation and GUI assets.
- Original project code is licensed under `GPL-2.0-only` (see `LICENSE`). Add matching SPDX identifiers to new source files; preserve existing notices in imported files. Manufacturer files retain their own terms.
