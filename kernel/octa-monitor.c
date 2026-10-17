// SPDX-License-Identifier: GPL-2.0-only
/* Idle meter engine; a real PCM open always takes priority over this engine. */
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/usb.h>

#include <sound/core.h>
#include <sound/pcm.h>
#include <sound/pcm_params.h>

#include "usbaudio.h"
#include "card.h"
#include "endpoint.h"
#include "power.h"
#include "octa.h"
#include "octa-peaks-internal.h"

#define OCTA_MONITOR_RETRY_MS 100
#define OCTA_MONITOR_PERIODS_PER_SECOND 100
#define OCTA_MONITOR_BUFFER_PERIODS 4

/* Hidden work has no open PCM file pinning the driver during disconnect.
 * Balance its shared active reference even after shutdown is marked, without
 * invoking a runtime suspend callback synchronously from disconnect/work.
 */
static void octa_monitor_put_pm(struct snd_usb_audio *chip)
{
	int interface;

	if (WARN_ON_ONCE(atomic_read(&chip->active) <= 0))
		return;
	if (!atomic_dec_and_test(&chip->active))
		return;
	for (interface = 0; interface < chip->num_interfaces; interface++)
		usb_autopm_put_interface_async(chip->intf[interface]);
}

static void octa_monitor_unlock_shutdown(struct snd_usb_audio *chip)
{
	octa_monitor_put_pm(chip);
	if (atomic_dec_and_test(&chip->usage_count))
		wake_up(&chip->shutdown_wait);
}

void octa_pcm_monitor_queue(struct octa_pcm_meter *meter, unsigned long delay)
{
	if (!READ_ONCE(meter->closing))
		queue_delayed_work(system_freezable_wq, &meter->work, delay);
}

static bool octa_monitor_wanted(struct octa_pcm_meter *meter)
{
	unsigned long flags;
	bool wanted;

	spin_lock_irqsave(&meter->lock, flags);
	if (meter->enabled && time_after_eq(jiffies, meter->lease))
		meter->enabled = false;
	wanted = meter->enabled && !meter->closing && !meter->suspended;
	spin_unlock_irqrestore(&meter->lock, flags);
	return wanted;
}

/* Caller drops the returned PM reference only after releasing monitor_lock. */
static bool octa_monitor_stop(struct octa_pcm_meter *meter, bool keep_pm)
{
	struct snd_usb_endpoint *ep;
	bool release_pm;
	int stream;

	for (stream = 0; stream < 2; stream++) {
		ep = meter->endpoint[stream];
		if (ep && meter->started[stream]) {
			snd_usb_endpoint_stop(ep, false);
			meter->started[stream] = false;
		}
	}
	for (stream = 0; stream < 2; stream++) {
		ep = meter->endpoint[stream];
		if (!ep)
			continue;
		snd_usb_endpoint_sync_pending_stop(ep);
		snd_usb_endpoint_set_sync(meter->chip, ep, NULL);
		snd_usb_endpoint_close(meter->chip, ep);
		meter->endpoint[stream] = NULL;
	}
	meter->monitor_rate = 0;
	release_pm = meter->monitor_pm && !keep_pm;
	if (release_pm)
		meter->monitor_pm = false;
	return release_pm;
}

static bool octa_pcm_files_open(struct snd_usb_audio *chip)
{
	struct snd_usb_stream *stream;
	bool opened = false;

	mutex_lock(&chip->mutex);
	list_for_each_entry(stream, &chip->pcm_list, list) {
		if (READ_ONCE(stream->substream[0].pcm_substream) ||
		    READ_ONCE(stream->substream[1].pcm_substream)) {
			opened = true;
			break;
		}
	}
	mutex_unlock(&chip->mutex);
	return opened;
}

static bool octa_monitor_healthy(struct octa_pcm_meter *meter)
{
	unsigned long flags, deadline;
	bool healthy;

	deadline = msecs_to_jiffies(OCTA_PCM_METER_STALE_MS);
	if (time_before(jiffies, meter->monitor_since + deadline))
		return true;
	spin_lock_irqsave(&meter->lock, flags);
	healthy = meter->direction[0].seen && meter->direction[1].seen &&
		time_before(jiffies, meter->direction[0].last_data + deadline) &&
		time_before(jiffies, meter->direction[1].last_data + deadline);
	spin_unlock_irqrestore(&meter->lock, flags);
	return healthy;
}

static const struct audioformat *octa_monitor_format(struct snd_usb_audio *chip,
						     unsigned int rate,
						     int direction)
{
	struct snd_usb_stream *stream;
	struct snd_usb_substream *subs;
	struct audioformat *format;
	const struct audioformat *found = NULL;
	unsigned int channels = rate == 192000 ? 4 : direction ? 12 : 10;

	mutex_lock(&chip->mutex);
	list_for_each_entry(stream, &chip->pcm_list, list) {
		subs = &stream->substream[direction];
		if (!subs->num_formats)
			continue;
		list_for_each_entry(format, &subs->fmt_list, list) {
			if (format->channels == channels &&
			    format->formats & SNDRV_PCM_FMTBIT_S24_3LE &&
			    format->rate_min == rate && format->rate_max == rate) {
				found = format;
				goto out;
			}
		}
	}
out:
	mutex_unlock(&chip->mutex);
	return found;
}

static void octa_monitor_interval(struct snd_pcm_hw_params *params,
				  snd_pcm_hw_param_t parameter, unsigned int value)
{
	struct snd_interval *interval = hw_param_interval(params, parameter);

	memset(interval, 0, sizeof(*interval));
	interval->min = value;
	interval->max = value;
	interval->integer = 1;
}

static int octa_monitor_open(struct octa_pcm_meter *meter, unsigned int rate)
{
	const struct audioformat *formats[2];
	struct snd_pcm_hw_params *params;
	struct snd_usb_endpoint *ep;
	int stream, err = -ENODEV;

	formats[0] = octa_monitor_format(meter->chip, rate, 0);
	formats[1] = octa_monitor_format(meter->chip, rate, 1);
	if (!formats[0] || !formats[1])
		return -ENODEV;
	params = kzalloc(sizeof(*params), GFP_KERNEL);
	if (!params)
		return -ENOMEM;
	_snd_pcm_hw_params_any(params);
	snd_mask_none(hw_param_mask(params, SNDRV_PCM_HW_PARAM_FORMAT));
	snd_mask_set_format(hw_param_mask(params, SNDRV_PCM_HW_PARAM_FORMAT),
			    SNDRV_PCM_FORMAT_S24_3LE);
	octa_monitor_interval(params, SNDRV_PCM_HW_PARAM_RATE, rate);
	octa_monitor_interval(params, SNDRV_PCM_HW_PARAM_PERIOD_SIZE,
			      rate / OCTA_MONITOR_PERIODS_PER_SECOND);
	octa_monitor_interval(params, SNDRV_PCM_HW_PARAM_PERIODS,
			      OCTA_MONITOR_BUFFER_PERIODS);
	for (stream = 1; stream >= 0; stream--) {
		octa_monitor_interval(params, SNDRV_PCM_HW_PARAM_CHANNELS,
				      formats[stream]->channels);
		/* A meter must never renegotiate/change the device-wide clock. */
		ep = snd_usb_endpoint_open(meter->chip, formats[stream], params,
					   false, true);
		if (!ep) {
			err = -ENODEV;
			goto out;
		}
		meter->endpoint[stream] = ep;
		snd_usb_endpoint_set_callback(ep, NULL, NULL, NULL);
		ep->lowlatency_playback = false;
	}
	snd_usb_endpoint_set_sync(meter->chip, meter->endpoint[0], meter->endpoint[1]);
	for (stream = 1; stream >= 0; stream--) {
		ep = meter->endpoint[stream];
		err = snd_usb_endpoint_set_params(meter->chip, ep);
		if (err < 0)
			goto out;
		err = snd_usb_endpoint_prepare(meter->chip, ep);
		if (err < 0)
			goto out;
	}
	/* Playback queues zeros and uses the normal capture implicit feedback. */
	for (stream = 0; stream < 2; stream++) {
		err = snd_usb_endpoint_start(meter->endpoint[stream]);
		if (err < 0)
			goto out;
		meter->started[stream] = true;
	}
	meter->monitor_rate = rate;
	meter->monitor_since = jiffies;
	err = 0;
out:
	kfree(params);
	return err;
}

static void octa_monitor_work(struct work_struct *work)
{
	struct octa_pcm_meter *meter = container_of(to_delayed_work(work),
						   struct octa_pcm_meter, work);
	struct snd_usb_audio *chip = meter->chip;
	unsigned int rate;
	bool release_pm = false;
	int err;

	/* Runtime resume callbacks must not run while monitor_lock is held. */
	err = snd_usb_lock_shutdown(chip);
	if (err < 0) {
		WRITE_ONCE(meter->last_error, -err);
		goto retry;
	}
	mutex_lock(&meter->monitor_lock);
	if (!octa_monitor_wanted(meter) || octa_pcm_files_open(chip)) {
		release_pm = octa_monitor_stop(meter, false);
		err = 0;
		goto unlock;
	}
	err = snd_usb_octa_capture_current_rate(chip, &rate);
	if (err < 0)
		goto stop;
	if (meter->monitor_rate == rate && meter->started[0] && meter->started[1] &&
	    octa_monitor_healthy(meter))
		goto unlock;
	/* Keep an existing PM reference across a live rate transition. */
	octa_monitor_stop(meter, true);
	err = octa_monitor_open(meter, rate);
	if (err < 0)
		goto stop;
	if (!meter->monitor_pm) {
		/* lock_shutdown already keeps active nonzero: this cannot resume. */
		err = snd_usb_autoresume(chip);
		if (err < 0)
			goto stop;
		meter->monitor_pm = true;
	}
	goto unlock;
stop:
	release_pm = octa_monitor_stop(meter, false);
unlock:
	WRITE_ONCE(meter->last_error, err < 0 ? -err : 0);
	mutex_unlock(&meter->monitor_lock);
	if (release_pm)
		octa_monitor_put_pm(chip);
	octa_monitor_unlock_shutdown(chip);
retry:
	if (octa_monitor_wanted(meter) && !atomic_read(&chip->shutdown))
		octa_pcm_monitor_queue(meter, msecs_to_jiffies(OCTA_MONITOR_RETRY_MS));
}

void octa_pcm_monitor_init(struct octa_pcm_meter *meter)
{
	mutex_init(&meter->monitor_lock);
	INIT_DELAYED_WORK(&meter->work, octa_monitor_work);
}

void snd_usb_octa_capture_pcm_yield(struct snd_usb_audio *chip)
{
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;
	bool release_pm;

	if (!meter)
		return;
	mutex_lock(&meter->monitor_lock);
	release_pm = octa_monitor_stop(meter, false);
	mutex_unlock(&meter->monitor_lock);
	if (release_pm)
		octa_monitor_put_pm(chip);
}

void snd_usb_octa_capture_pcm_suspend(struct snd_usb_audio *chip, bool automatic)
{
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;
	unsigned long flags;

	/* A runtime callback may be caused by this work's final PM put itself. */
	if (!meter || automatic)
		return;
	spin_lock_irqsave(&meter->lock, flags);
	meter->suspended = true;
	spin_unlock_irqrestore(&meter->lock, flags);
	cancel_delayed_work_sync(&meter->work);
	mutex_lock(&meter->monitor_lock);
	/* Retain usage across system suspend; release outside PM callbacks later. */
	octa_monitor_stop(meter, true);
	mutex_unlock(&meter->monitor_lock);
}

void snd_usb_octa_capture_pcm_resume(struct snd_usb_audio *chip)
{
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;
	unsigned long flags;

	if (!meter)
		return;
	spin_lock_irqsave(&meter->lock, flags);
	meter->suspended = false;
	spin_unlock_irqrestore(&meter->lock, flags);
	if (octa_monitor_wanted(meter))
		octa_pcm_monitor_queue(meter, 0);
}

void snd_usb_octa_capture_pcm_shutdown(struct snd_usb_audio *chip)
{
	struct octa_pcm_meter *meter = chip->octa_pcm_meter;
	unsigned long flags;
	bool release_pm;

	if (!meter)
		return;
	spin_lock_irqsave(&meter->lock, flags);
	meter->closing = true;
	meter->enabled = false;
	spin_unlock_irqrestore(&meter->lock, flags);
	cancel_delayed_work_sync(&meter->work);
	mutex_lock(&meter->monitor_lock);
	release_pm = octa_monitor_stop(meter, false);
	mutex_unlock(&meter->monitor_lock);
	if (release_pm)
		octa_monitor_put_pm(chip);
}
