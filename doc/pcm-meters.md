# Host PCM meters

The standalone module exposes two ALSA CARD controls. They are independent of
the device's eight SysEx input meters. The GUI bridge uses them for compressor
OUT, Direct Mixer INPUT and WAVE OUT meters. Windows-compatible positive S24
peak calculation is described in [the protocol reference](usb-midi-protocol.txt).

`OCTA-CAPTURE PCM Meter Switch` is a volatile Boolean, initially off. Enabling
starts an idle capture/zero-playback engine at the existing hardware rate when
no normal PCM file is open. It never changes the clock. Opening a normal PCM
file stops and closes the idle endpoints before ALSA computes constraints;
normal applications choose their own period and buffer sizes. Meter callbacks
then observe those real streams. Closing the panel never stops an application's
audio. With no normal PCM file, the idle engine resumes while requested.

`OCTA-CAPTURE PCM Peaks` is a read-only, volatile 136-byte value. Reads renew
an enabled subscription's two-second lease. A crashed client therefore cannot
leave the idle streams running indefinitely. Reads alone do not enable meters.
The bridge disables its subscription on EOF/signals/destruction. One control
panel owns the subscription; the switch is device-wide.

All integers below are little-endian. A read atomically takes and clears both
directions' accumulated maxima, matching the original driver. Peaks survive
arbitrary gaps between reads; a fixed publication window must not discard them.
The bridge retains a bounded notification history so a short peak also survives
replacement of its latest HTTP snapshot. Use one meter reader per device.

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | 4 bytes | `OCPK` magic |
| 4 | u16 | Version, currently 1 |
| 6 | u16 | Length, 136 |
| 8 | u32 | Flags: enabled=1, idle engine=2, capture fresh=4, playback fresh=8 |
| 12 | u32 | Last idle-engine error, positive Linux errno; 0 on success |
| 16, 20 | u32 each | Capture/playback rate in Hz |
| 24, 28 | u32 each | Capture/playback transport channels |
| 32, 36 | u32 each | Capture/playback last-data age in ms; `UINT32_MAX` if unseen |
| 40, 44 | u32 each | Capture/playback progress sequence (about 20 ms), wrapping at u32 |
| 48 | 12 × u32 | Capture peaks, positive S24 shifted left eight bits |
| 96 | 10 × u32 | Playback peaks, same representation |

Rates are 44100/48000/96000 Hz with 12 capture and 10 playback channels, or
192000 Hz with 4/4. Unused peaks are zero. Fresh means age ≤500 ms; consumers
must also check their own receive age and the current device rate. Unseen
directions have zero rate/channels/peaks. Peak range is 0..`0x7fffff00`, in
multiples of 256. The C++ decoder and Python bridge validate this schema.
Transport capture channels 11/12 are preserved for diagnostics; the original
Direct Mixer displays only the first ten. Preamp OUT uses the first eight and
is visible when that channel's compressor is enabled.

```bash
./build/strict/octa-pcm-meters --read  # passive snapshot
./build/strict/octa-pcm-meters 2       # enable, observe, disable after two seconds
python3 tools/check-pcm-meters.py --rates
python3 tools/check-pcm-meters.py --rates --peaks
python3 tools/check-bridge.py --pause
```

The hardware checker requires exclusive control-panel access, records no audio
and plays zeros by default. `--peaks` additionally uses isolated channel tags of
one to ten S24 LSBs (at most -118 dBFS). It verifies exact playback peaks after
over 200 ms of subsequent zeros, read-clear behavior and negative rejection.
It checks both directions at all rates, different period
sizes, idle/normal PCM handover, close/reopen during capture, playback and
duplex, continued meter progress in each active direction, client death,
and restoration of the original rate and complete raw state. These checks pass
on the connected WSL device with strict and release builds. Active idle-stream
module unload/reload also passes. Native Debian, physical signal calibration,
system suspend/unplug and kernel sanitizer/fault-injection coverage remain.
The bridge pause check suspends its child process beyond the subscription lease,
checks that idle audio stops, then resumes it and requires fresh meters without
changing device settings. This models a stalled client, not system suspend.

The panel uses the original 290 nonlinear meter thresholds, a 30 ms tick,
0.72 dB fall per tick, 50-tick peak hold and 133-tick clip hold. Preamp input
warnings start at -2 dB; capture/playback clipping starts at 0 dB. Stale,
bypassed and rate-disabled views clear their held state. These numerical tables
match both reference panels; `tools/verify-gui-meters.py MAC-GUI WINDOWS-GUI`
also executes the original Windows scale/level/hold/clip routines on synthetic
inputs (Python Unicorn and Node required). This does not measure physical audio.
