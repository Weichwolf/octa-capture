// SPDX-License-Identifier: GPL-2.0-only
/* Read-only OCTA-CAPTURE status pipe; independent of audio and CTRL MIDI. */
#include <linux/errno.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/usb.h>

#include <sound/control.h>
#include <sound/core.h>

#include "usbaudio.h"
#include "octa.h"

#define OCTA_STATUS_INTERFACE 3
#define OCTA_STATUS_ALTSETTING 1
#define OCTA_STATUS_ENDPOINT 0x82
#define OCTA_STATUS_BYTES 8
#define OCTA_STATUS_PACKETS 4
#define OCTA_STATUS_TIMEOUT_MS 250

struct octa_status {
	struct snd_usb_audio *chip;
	struct mutex lock; /* Serializes status reads and alternate selection. */
};

static int octa_status_prepare(struct snd_usb_audio *chip)
{
	struct usb_interface *intf;
	struct usb_host_interface *alt;
	const struct usb_endpoint_descriptor *ep;

	intf = usb_ifnum_to_if(chip->dev, OCTA_STATUS_INTERFACE);
	if (!intf)
		return -ENODEV;
	alt = usb_altnum_to_altsetting(intf, OCTA_STATUS_ALTSETTING);
	if (!alt || alt->desc.bNumEndpoints != 1)
		return -EINVAL;
	ep = &alt->endpoint[0].desc;
	if (!usb_endpoint_is_int_in(ep) ||
	    ep->bEndpointAddress != OCTA_STATUS_ENDPOINT ||
	    usb_endpoint_maxp(ep) != OCTA_STATUS_BYTES)
		return -EINVAL;

	if (intf->cur_altsetting->desc.bAlternateSetting == OCTA_STATUS_ALTSETTING)
		return 0;
	return usb_set_interface(chip->dev, OCTA_STATUS_INTERFACE,
				 OCTA_STATUS_ALTSETTING);
}

static int octa_status_read(struct snd_usb_audio *chip, u8 *packet)
{
	bool seen_zero = false;
	int attempt, actual, err;

	for (attempt = 0; attempt < OCTA_STATUS_PACKETS; attempt++) {
		actual = 0;
		err = usb_interrupt_msg(chip->dev,
					usb_rcvintpipe(chip->dev, OCTA_STATUS_ENDPOINT),
					packet, OCTA_STATUS_BYTES, &actual,
					OCTA_STATUS_TIMEOUT_MS);
		if (err < 0)
			return err;
		if (actual != OCTA_STATUS_BYTES)
			return -EIO;
		if (packet[0] > 1)
			return -EPROTO;
		if (!packet[0]) {
			seen_zero = true;
			continue;
		}
		if (seen_zero) {
			if (!(packet[3] | packet[4] | packet[5]))
				return -ENODATA;
			return 0;
		}
	}
	return -EAGAIN;
}

static int octa_status_info(struct snd_kcontrol *kcontrol,
			    struct snd_ctl_elem_info *uinfo)
{
	(void)kcontrol;
	uinfo->type = SNDRV_CTL_ELEM_TYPE_BYTES;
	uinfo->count = OCTA_STATUS_BYTES;
	return 0;
}

static int octa_status_get(struct snd_kcontrol *kcontrol,
			   struct snd_ctl_elem_value *ucontrol)
{
	struct octa_status *status = snd_kcontrol_chip(kcontrol);
	u8 *packet;
	int err;

	err = mutex_lock_interruptible(&status->lock);
	if (err)
		return err;
	err = snd_usb_lock_shutdown(status->chip);
	if (err < 0)
		goto unlock;

	/* USB DMA buffers must not live on the stack. */
	packet = kmalloc(OCTA_STATUS_BYTES, GFP_KERNEL);
	if (!packet) {
		err = -ENOMEM;
		goto release;
	}
	err = octa_status_prepare(status->chip);
	if (!err)
		err = octa_status_read(status->chip, packet);
	if (!err)
		memcpy(ucontrol->value.bytes.data, packet, OCTA_STATUS_BYTES);
	kfree(packet);
release:
	snd_usb_unlock_shutdown(status->chip);
unlock:
	mutex_unlock(&status->lock);
	return err;
}

static void octa_status_free(struct snd_kcontrol *kcontrol)
{
	kfree(snd_kcontrol_chip(kcontrol));
}

int snd_usb_octa_capture_add_controls(struct snd_usb_audio *chip)
{
	static const struct snd_kcontrol_new control = {
		.iface = SNDRV_CTL_ELEM_IFACE_CARD,
		.name = "OCTA-CAPTURE Status",
		.access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE,
		.info = octa_status_info,
		.get = octa_status_get,
	};
	struct octa_status *status;
	struct snd_kcontrol *kcontrol;
	int err;

	if (!snd_usb_octa_capture_id(chip->usb_id))
		return 0;
	status = kzalloc(sizeof(*status), GFP_KERNEL);
	if (!status)
		return -ENOMEM;
	status->chip = chip;
	mutex_init(&status->lock);
	kcontrol = snd_ctl_new1(&control, status);
	if (!kcontrol) {
		kfree(status);
		return -ENOMEM;
	}
	kcontrol->private_free = octa_status_free;
	/* snd_ctl_add owns/frees the control, including its private data. */
	err = snd_ctl_add(chip->card, kcontrol);
	if (err < 0)
		return err;
	return snd_usb_octa_capture_add_pcm_controls(chip);
}
