/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef OCTA_MIDI_H
#define OCTA_MIDI_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdbool.h>
#endif

/* Caller supplies one complete four-byte USB event. Match both references. */
static inline int octa_midi_event_length(const unsigned char *event)
{
	unsigned int cin = event[0] & 0x0f;
	unsigned int status = event[1];

	if (cin >= 8 && cin <= 14) {
		if ((status >> 4) != cin || event[2] >= 0x80)
			return 0;
		if (cin == 12 || cin == 13)
			return 2;
		return event[3] < 0x80 ? 3 : 0;
	}
	switch (cin) {
	case 2:
		return (status == 0xf1 || status == 0xf3) && event[2] < 0x80 ? 2 : 0;
	case 3:
		return status == 0xf2 && event[2] < 0x80 && event[3] < 0x80 ? 3 : 0;
	case 4:
	case 7:
		return 3;
	case 5:
		return 1;
	case 6:
		return 2;
	case 15:
		return status == 0xf6 || status >= 0xf8 ? 1 : 0;
	default:
		return 0;
	}
}

/* Close an interrupted SysEx before consuming the next non-realtime status. */
static inline bool octa_midi_sysex_end(unsigned char cable, unsigned int pending,
				       const unsigned char *data, unsigned char *event)
{
	static const unsigned char endings[] = { 5, 6, 7 };

	if (pending > 2)
		return false;
	event[0] = cable | endings[pending];
	event[1] = pending ? data[0] : 0xf7;
	event[2] = pending > 1 ? data[1] : pending ? 0xf7 : 0;
	event[3] = pending > 1 ? 0xf7 : 0;
	return true;
}

#endif /* OCTA_MIDI_H */
