/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef OCTA_PEAKS_INTERNAL_H
#define OCTA_PEAKS_INTERNAL_H

#include <linux/mutex.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>

#include "pcm-peaks.h"

#define OCTA_PCM_METER_LEASE_MS 2000
#define OCTA_PCM_METER_PERIOD_MS 20
#define OCTA_PCM_METER_STALE_MS 500

struct snd_usb_audio;
struct snd_usb_endpoint;

struct octa_pcm_direction {
	struct octa_pcm_peak_values accumulated;
	struct octa_pcm_peak_values published;
	unsigned long window_start;
	unsigned long last_data;
	unsigned int rate;
	unsigned int channels;
	u32 sequence;
	bool seen;
};

struct octa_pcm_meter {
	struct snd_usb_audio *chip;
	spinlock_t lock; /* Peak windows and lease; also taken in URB callbacks. */
	struct octa_pcm_direction direction[2];
	unsigned long lease;
	bool enabled;
	bool suspended;
	bool closing;

	/* Never hold this mutex while dropping the final runtime PM reference. */
	struct mutex monitor_lock;
	struct delayed_work work;
	struct snd_usb_endpoint *endpoint[2];
	bool started[2];
	bool monitor_pm;
	unsigned int monitor_rate;
	unsigned long monitor_since;
	int last_error;
};

void octa_pcm_monitor_init(struct octa_pcm_meter *meter);
void octa_pcm_monitor_queue(struct octa_pcm_meter *meter, unsigned long delay);

#endif /* OCTA_PEAKS_INTERNAL_H */
