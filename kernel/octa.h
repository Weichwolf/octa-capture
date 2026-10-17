/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __USBAUDIO_OCTA_H
#define __USBAUDIO_OCTA_H

struct snd_usb_audio;
struct snd_usb_endpoint;
struct snd_urb_ctx;

int snd_usb_octa_capture_set_rate(struct snd_usb_audio *chip, int rate);

int snd_usb_octa_capture_add_controls(struct snd_usb_audio *chip);
int snd_usb_octa_capture_current_rate(struct snd_usb_audio *chip,
				      unsigned int *rate);
int snd_usb_octa_capture_add_pcm_controls(struct snd_usb_audio *chip);
void snd_usb_octa_capture_pcm_retire(struct snd_usb_endpoint *ep,
				     struct snd_urb_ctx *ctx);
void snd_usb_octa_capture_pcm_yield(struct snd_usb_audio *chip);
void snd_usb_octa_capture_pcm_suspend(struct snd_usb_audio *chip, bool automatic);
void snd_usb_octa_capture_pcm_resume(struct snd_usb_audio *chip);
void snd_usb_octa_capture_pcm_shutdown(struct snd_usb_audio *chip);
void snd_usb_octa_capture_free_pcm(struct snd_usb_audio *chip);

#endif /* __USBAUDIO_OCTA_H */
