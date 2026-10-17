// SPDX-License-Identifier: GPL-2.0-only
/* Roland OCTA-CAPTURE vendor clock control. */

#include <linux/delay.h>
#include <linux/usb.h>

#include <sound/core.h>

#include "usbaudio.h"
#include "helper.h"
#include "octa.h"

#define OCTA_CLOCK_REQUEST 3
#define OCTA_POLL_ATTEMPTS 40
#define OCTA_POLL_DELAY_MS 200

static int octa_read_status(struct snd_usb_audio *chip, u16 value,
			    u8 *data, u16 size)
{
	int err;

	err = snd_usb_ctl_msg(chip->dev, usb_rcvctrlpipe(chip->dev, 0),
			      OCTA_CLOCK_REQUEST,
			      USB_DIR_IN | USB_TYPE_VENDOR | USB_RECIP_DEVICE,
			      value, 0, data, size);
	if (err < 0)
		return err;
	return err == size ? 0 : -EIO;
}

static int octa_get_rate(struct snd_usb_audio *chip, int *rate, u8 *flags)
{
	u8 data[4];
	int err;

	err = octa_read_status(chip, 1, data, sizeof(data));
	if (err < 0)
		return err;
	*rate = data[0] | (data[1] << 8) | (data[2] << 16);
	*flags = data[3];
	return 0;
}

static int octa_wait_ready(struct snd_usb_audio *chip)
{
	u8 ready;
	int attempt, err;

	for (attempt = 0; attempt < OCTA_POLL_ATTEMPTS; attempt++) {
		err = octa_read_status(chip, 2, &ready, sizeof(ready));
		if (err < 0)
			return err;
		if (ready == 1)
			return 0;
		msleep(OCTA_POLL_DELAY_MS);
	}
	return -ETIMEDOUT;
}

int snd_usb_octa_capture_current_rate(struct snd_usb_audio *chip,
				      unsigned int *rate)
{
	u8 flags, ready;
	int current_rate, err;

	err = octa_get_rate(chip, &current_rate, &flags);
	if (err < 0)
		return err;
	if (flags & 3)
		return -EAGAIN;
	if (current_rate != 44100 && current_rate != 48000 && current_rate != 96000 &&
	    current_rate != 192000)
		return -ERANGE;
	err = octa_read_status(chip, 2, &ready, sizeof(ready));
	if (err < 0)
		return err;
	if (ready != 1)
		return -EAGAIN;
	*rate = current_rate;
	return 0;
}

int snd_usb_octa_capture_set_rate(struct snd_usb_audio *chip, int rate)
{
	u8 data[4], flags;
	int current_rate, attempt, err;

	switch (rate) {
	case 44100:
	case 48000:
	case 96000:
	case 192000:
		break;
	default:
		return -EINVAL;
	}

	err = octa_get_rate(chip, &current_rate, &flags);
	if (err < 0)
		return err;
	if (flags & 2)
		return -EIO;
	if (current_rate == rate && !(flags & 1))
		return octa_wait_ready(chip);

	/* 0x40 changes the clock; 0x01 only negotiates the current rate. */
	data[0] = current_rate == rate ? 1 : 0x40;
	data[1] = rate;
	data[2] = rate >> 8;
	data[3] = rate >> 16;
	err = snd_usb_ctl_msg(chip->dev, usb_sndctrlpipe(chip->dev, 0),
			      OCTA_CLOCK_REQUEST,
			      USB_DIR_OUT | USB_TYPE_VENDOR | USB_RECIP_DEVICE,
			      8, 0, data, sizeof(data));
	if (err < 0)
		return err;
	if (err != sizeof(data))
		return -EIO;

	for (attempt = 0; attempt < OCTA_POLL_ATTEMPTS; attempt++) {
		err = octa_get_rate(chip, &current_rate, &flags);
		if (err < 0)
			return err;
		if (current_rate != rate || (flags & 2))
			return -EIO;
		if (!(flags & 1))
			return octa_wait_ready(chip);
		msleep(OCTA_POLL_DELAY_MS);
	}
	return -ETIMEDOUT;
}
