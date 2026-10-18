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

At 44.1/48/96 kHz, capture 11/12 carries Mixer A's digital stereo return
(also documented in the device manual). `tools/check-mixer-audio.py --write
--rates --keep-going` checks ten playback sources, five linear pan positions,
signed samples, mute/solo precedence, strip/master gain and PRBS11 sample order.
It first routes every physical output to verified-silent Mixer B, keeps test and
capture samples in RAM, and restores the original rate and complete raw state.
A durable private recovery frame is written before changing settings. At
192 kHz this return is unavailable; the checker refuses its write test there.
Digital return checks do not establish analog or compressor response.
`--write --integrity-seconds 30` instead checks a continuous PRBS11 stream
on each playback source, including sample order across the middle 29 seconds.
The bound is 2..60 seconds per source. Add `--rates` for the three return-capable
rates. As with short checks, any mismatch makes the final result fail.

Native Debian 6.12 direct USB passes ten-second PRBS streams on each of the ten
playback sources at 44.1/48/96 kHz, with 1024-frame periods and an 8192-frame
buffer. Each comparison checks the middle nine seconds; passive USB observation
also found no sequence gaps or packet/transfer errors. This verifies these
digital-return cases, not calibrated analog response or long-term stability.
Exclusive PCM access is required; pausing one user's sound server does not
reserve the device against other login sessions.

The separate silent duplex checker verifies both streams' actual kernel ALSA
parameters and owner PID, timing, XRUN diagnostics and clock state. Native Debian
passes ten seconds at each of 44.1/48/96/192 kHz with 256-frame periods and a
1024-frame buffer. This is separate from the PRBS results above; signal integrity
and sustained performance at these smaller buffers remain unverified.

```bash
pasuspender -- python3 tools/check-audio-soak.py --rates --duration 10 --period-size 256 --buffer-size 1024
```

Close the control panel before this exclusive test. It saves a private recovery
frame, restores the original rate and verifies complete raw state afterward.
Clock recovery failure cannot skip a needed raw recovery attempt.

The tested WSL USB/IP path has produced intermittent zero samples and a PRBS
sequence jump inside otherwise correct digital returns, even without an ALSA
XRUN. Successful timed transfers and quiet soak checks therefore do not establish
audio integrity. The checker reports these comparisons as failures; its
`--keep-going` option only collects sample mismatches, never suppresses I/O,
output-isolation or restoration failures.

A separate RAM-only USB observer found discontinuities in completed input
payloads while the submitted output PRBS remained continuous. The observation
had no monitor drops, malformed packet lengths or reported ISO packet errors.
This places those errors before ALSA capture processing; it does not identify
whether USB/IP scheduling, the host driver or the device caused them.

To compare that boundary, start `tools/observe-usb-prbs.py --device 1-1
--seconds 360` in one terminal, using the device's USB sysfs name. It requires
`usbmon` and access to that bus's `/dev/usbmonN`. In another terminal run the
continuous digital-return checker above. Ctrl+C or SIGTERM ends observation
with a JSON summary. Exit 1 reports discontinuities, transfer/packet errors,
invalid lengths or monitor drops; exit 2 means no PRBS was verified in both
directions. No control writes or audio files are produced by the observer.
Keep the device at 44.1/48/96 kHz. This diagnostic arms after 64 matching PRBS
samples and reports gaps only if the sequence resumes within 512 samples;
the audio checker's full comparison remains necessary for longer gaps.

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
