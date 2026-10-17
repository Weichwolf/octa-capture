/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __USBAUDIO_OCTA_H
#define __USBAUDIO_OCTA_H

struct snd_usb_audio;

int snd_usb_octa_capture_set_rate(struct snd_usb_audio *chip, int rate);

int snd_usb_octa_capture_add_controls(struct snd_usb_audio *chip);

#endif /* __USBAUDIO_OCTA_H */
