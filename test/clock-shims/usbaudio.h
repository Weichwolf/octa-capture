/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef OCTA_CLOCK_TEST_AUDIO_H
#define OCTA_CLOCK_TEST_AUDIO_H
#include "linux/usb.h"

struct snd_usb_audio {
  struct usb_device *dev;
};
#endif
