/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef OCTA_PCM_PEAKS_H
#define OCTA_PCM_PEAKS_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdbool.h>
#include <stddef.h>
#endif

#define OCTA_PCM_PEAK_CHANNELS 12
#define OCTA_PCM_SAMPLE_BYTES 3

struct octa_pcm_peak_values {
	unsigned int values[OCTA_PCM_PEAK_CHANNELS];
};

/* Windows RDWM1110.SYS takes positive peaks, rather than absolute values. */
static inline unsigned int octa_pcm_positive_peak24(const unsigned char *sample)
{
	unsigned int low = sample[0], middle = sample[1], high = sample[2];
	unsigned int value = (low << 8) | (middle << 16) | (high << 24);

	return value & 0x80000000U ? 0 : value;
}

/* Validate the complete packet before changing any accumulated channel. */
static inline bool octa_pcm_scan_peaks24(struct octa_pcm_peak_values *peaks,
					 const unsigned char *data, size_t size,
					 unsigned int channels)
{
	size_t stride;
	unsigned int channel, value;

	if (!peaks || !data || !channels || channels > OCTA_PCM_PEAK_CHANNELS)
		return false;
	stride = channels;
	stride *= OCTA_PCM_SAMPLE_BYTES;
	if (size % stride)
		return false;
	while (size) {
		for (channel = 0; channel < channels; channel++) {
			value = octa_pcm_positive_peak24(data);
			if (peaks->values[channel] < value)
				peaks->values[channel] = value;
			data += OCTA_PCM_SAMPLE_BYTES;
		}
		size -= stride;
	}
	return true;
}

#endif /* OCTA_PCM_PEAKS_H */
