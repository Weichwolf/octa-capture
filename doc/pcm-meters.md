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

All integers below are little-endian. Reads are non-destructive; each direction
publishes a roughly 20 ms peak window with a sequence and last-data age.

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
| 40, 44 | u32 each | Capture/playback publication sequence, wrapping at u32 |
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
```

The hardware checker requires exclusive control-panel access, records no audio
and plays only zeros. It checks both directions at all rates, different period
sizes, idle/normal PCM handover, close while capture continues, client death,
and restoration of the original rate and complete raw state. These checks pass
on the connected WSL device with strict and release builds. Active idle-stream
module unload/reload also passes. Native Debian, physical signal calibration,
system suspend/unplug and kernel sanitizer/fault-injection coverage remain.
