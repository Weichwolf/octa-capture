// SPDX-License-Identifier: GPL-2.0-only
/* Host PCM meters; MIDI/device input meters are a separate signal source. */
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/unaligned.h>
#include <linux/usb.h>

#include <sound/control.h>
#include <sound/core.h>
#include <sound/pcm.h>

#include "usbaudio.h"
#include "card.h"
#include "endpoint.h"
#include "octa.h"
#include "octa-peaks-internal.h"
#include "octa-peaks-wire.h"

static unsigned int octa_pcm_age(const struct octa_pcm_direction *direction)
{
	unsigned long elapsed;

	if (!direction->seen)
		return U32_MAX;
	elapsed = jiffies - direction->last_data;
	if (elapsed >= msecs_to_jiffies(U32_MAX - 1))
		return U32_MAX - 1;
	return jiffies_to_msecs(elapsed);
}

static void octa_pcm_accumulate(struct octa_pcm_meter *meter,
				struct snd_usb_endpoint *ep,
				const struct octa_pcm_peak_values *peaks)
{
	struct octa_pcm_direction *direction;
	unsigned long flags;
	unsigned int channel;
	int stream = usb_pipein(ep->pipe) ? 1 : 0;

	spin_lock_irqsave(&meter->lock, flags);
	if (!meter->enabled || meter->closing)
		goto unlock;
	direction = &meter->direction[stream];
	if (direction->rate != ep->cur_rate ||
	    direction->channels != ep->cur_channels) {
		memset(direction, 0, sizeof(*direction));
		direction->rate = ep->cur_rate;
		direction->channels = ep->cur_channels;
	}
	for (channel = 0; channel < ep->cur_channels; channel++)
		direction->accumulated.values[channel] =
			max(direction->accumulated.values[channel], peaks->values[channel]);
	if (!direction->seen ||
	    time_after_eq(jiffies, direction->window_start +
			  msecs_to_jiffies(OCTA_PCM_METER_PERIOD_MS))) {
		direction->window_start = jiffies;
		direction->sequence = direction->sequence == U32_MAX ?
			0 : direction->sequence + 1;
	}
	direction->last_data = jiffies;
	direction->seen = true;
unlock:
	spin_unlock_irqrestore(&meter->lock, flags);
}

void snd_usb_octa_capture_pcm_retire(struct snd_usb_endpoint *ep,
				     struct snd_urb_ctx *ctx)
{
	struct octa_pcm_meter *meter = READ_ONCE(ep->chip->octa_pcm_meter);
	struct octa_pcm_peak_values peaks = {};
	struct urb *urb = ctx->urb;
	unsigned int offset, length, limit;
	bool received = false;
	int packet;

	if (!meter || !READ_ONCE(meter->enabled) ||
	    ep->type != SND_USB_ENDPOINT_TYPE_DATA ||
	    ep->cur_format != SNDRV_PCM_FORMAT_S24_3LE ||
	    !ep->cur_channels || ep->cur_channels > OCTA_PCM_PEAK_CHANNELS ||
	    (usb_pipeout(ep->pipe) && ep->cur_channels > 10) ||
	    ep->stride != ep->cur_channels * OCTA_PCM_SAMPLE_BYTES ||
	    !urb->transfer_buffer || urb->transfer_buffer_length < 0 ||
	    urb->number_of_packets <= 0 || urb->number_of_packets > ctx->packets)
		return;
	limit = min_t(unsigned int, urb->transfer_buffer_length, ctx->buffer_size);
	for (packet = 0; packet < urb->number_of_packets; packet++) {
		if (urb->iso_frame_desc[packet].status)
			continue;
		offset = urb->iso_frame_desc[packet].offset;
		length = usb_pipein(ep->pipe) ?
			urb->iso_frame_desc[packet].actual_length :
			urb->iso_frame_desc[packet].length;
		if (!length || offset > limit || length > limit - offset)
			continue;
		if (octa_pcm_scan_peaks24(&peaks, urb->transfer_buffer + offset,
					  length, ep->cur_channels))
			received = true;
	}
	if (received)
		octa_pcm_accumulate(meter, ep, &peaks);
}

static int octa_pcm_switch_get(struct snd_kcontrol *control,
			       struct snd_ctl_elem_value *value)
{
	struct snd_usb_audio *chip = snd_kcontrol_chip(control);
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;

	if (!meter || atomic_read(&chip->shutdown))
		return -ENODEV;
	value->value.integer.value[0] = READ_ONCE(meter->enabled);
	return 0;
}

static int octa_pcm_switch_put(struct snd_kcontrol *control,
			       struct snd_ctl_elem_value *value)
{
	struct snd_usb_audio *chip = snd_kcontrol_chip(control);
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;
	long requested = value->value.integer.value[0];
	unsigned long flags;
	bool changed;

	if (!meter || atomic_read(&chip->shutdown))
		return -ENODEV;
	if (requested != 0 && requested != 1)
		return -EINVAL;
	spin_lock_irqsave(&meter->lock, flags);
	changed = meter->enabled != requested;
	if (changed && requested)
		memset(meter->direction, 0, sizeof(meter->direction));
	meter->enabled = requested;
	meter->lease = jiffies + msecs_to_jiffies(OCTA_PCM_METER_LEASE_MS);
	spin_unlock_irqrestore(&meter->lock, flags);
	octa_pcm_monitor_queue(meter, 0);
	return changed;
}

static int octa_pcm_peaks_info(struct snd_kcontrol *control,
			       struct snd_ctl_elem_info *info)
{
	info->type = SNDRV_CTL_ELEM_TYPE_BYTES;
	info->count = OCTA_PCM_METER_BYTES;
	return 0;
}

static void octa_pcm_write_direction(u8 *data,
				     const struct octa_pcm_direction *direction,
				     unsigned int age, int stream)
{
	unsigned int channel, maximum, offset;

	put_unaligned_le32(direction->rate, data + (stream ? 16 : 20));
	put_unaligned_le32(direction->channels, data + (stream ? 24 : 28));
	put_unaligned_le32(age, data + (stream ? 32 : 36));
	put_unaligned_le32(direction->sequence, data + (stream ? 40 : 44));
	maximum = stream ? OCTA_PCM_METER_CAPTURE_CHANNELS :
		OCTA_PCM_METER_PLAYBACK_CHANNELS;
	offset = stream ? OCTA_PCM_METER_CAPTURE_OFFSET :
		OCTA_PCM_METER_PLAYBACK_OFFSET;
	for (channel = 0; channel < maximum; channel++)
		put_unaligned_le32(direction->accumulated.values[channel],
				   data + offset + channel * sizeof(u32));
}

static int octa_pcm_peaks_get(struct snd_kcontrol *control,
			      struct snd_ctl_elem_value *value)
{
	struct snd_usb_audio *chip = snd_kcontrol_chip(control);
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;
	u8 *data = value->value.bytes.data;
	unsigned int age[2], state = 0;
	unsigned long flags;

	if (!meter || atomic_read(&chip->shutdown))
		return -ENODEV;
	memset(data, 0, OCTA_PCM_METER_BYTES);
	memcpy(data, "OCPK", 4);
	put_unaligned_le16(OCTA_PCM_METER_VERSION, data + 4);
	put_unaligned_le16(OCTA_PCM_METER_BYTES, data + 6);
	spin_lock_irqsave(&meter->lock, flags);
	if (meter->enabled) {
		meter->lease = jiffies + msecs_to_jiffies(OCTA_PCM_METER_LEASE_MS);
		state |= OCTA_PCM_METER_ENABLED;
	}
	age[0] = octa_pcm_age(&meter->direction[0]);
	age[1] = octa_pcm_age(&meter->direction[1]);
	if (age[0] <= OCTA_PCM_METER_STALE_MS)
		state |= OCTA_PCM_METER_PLAYBACK_FRESH;
	if (age[1] <= OCTA_PCM_METER_STALE_MS)
		state |= OCTA_PCM_METER_CAPTURE_FRESH;
	octa_pcm_write_direction(data, &meter->direction[0], age[0], 0);
	octa_pcm_write_direction(data, &meter->direction[1], age[1], 1);
	/* Match the original driver: retain every peak until its consumer reads.
	 * A fixed publication window could lose short peaks between two reads.
	 */
	memset(&meter->direction[0].accumulated, 0,
	       sizeof(meter->direction[0].accumulated));
	memset(&meter->direction[1].accumulated, 0,
	       sizeof(meter->direction[1].accumulated));
	spin_unlock_irqrestore(&meter->lock, flags);
	if (READ_ONCE(meter->started[0]) && READ_ONCE(meter->started[1]))
		state |= OCTA_PCM_METER_IDLE_STREAM;
	put_unaligned_le32(state, data + 8);
	put_unaligned_le32(READ_ONCE(meter->last_error), data + 12);
	return 0;
}

int snd_usb_octa_capture_add_pcm_controls(struct snd_usb_audio *chip)
{
	static const struct snd_kcontrol_new controls[] = {
		{
			.iface = SNDRV_CTL_ELEM_IFACE_CARD,
			.name = "OCTA-CAPTURE PCM Meter Switch",
			.access = SNDRV_CTL_ELEM_ACCESS_READWRITE |
				SNDRV_CTL_ELEM_ACCESS_VOLATILE,
			.info = snd_ctl_boolean_mono_info,
			.get = octa_pcm_switch_get,
			.put = octa_pcm_switch_put,
		},
		{
			.iface = SNDRV_CTL_ELEM_IFACE_CARD,
			.name = "OCTA-CAPTURE PCM Peaks",
			.access = SNDRV_CTL_ELEM_ACCESS_READ |
				SNDRV_CTL_ELEM_ACCESS_VOLATILE,
			.info = octa_pcm_peaks_info,
			.get = octa_pcm_peaks_get,
		},
	};
	struct octa_pcm_meter *meter;
	struct snd_kcontrol *control;
	unsigned int index;
	int err;

	meter = kzalloc(sizeof(*meter), GFP_KERNEL);
	if (!meter)
		return -ENOMEM;
	meter->chip = chip;
	spin_lock_init(&meter->lock);
	octa_pcm_monitor_init(meter);
	chip->octa_pcm_meter = meter;
	for (index = 0; index < ARRAY_SIZE(controls); index++) {
		control = snd_ctl_new1(&controls[index], chip);
		if (!control)
			return -ENOMEM;
		err = snd_ctl_add(chip->card, control);
		if (err < 0)
			return err;
	}
	return 0;
}

void snd_usb_octa_capture_free_pcm(struct snd_usb_audio *chip)
{
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;

	if (!meter)
		return;
	/* Called after endpoint_free_all has joined every URB callback. */
	meter = xchg(&chip->octa_pcm_meter, NULL);
	mutex_destroy(&meter->monitor_lock);
	kfree(meter);
}
