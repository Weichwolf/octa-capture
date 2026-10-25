/* SPDX-License-Identifier: GPL-2.0-only */
/* Included inside this module's USB ID table. */
{
	USB_DEVICE(0x0582, 0x0120),
	.driver_info = (unsigned long)&(const struct snd_usb_audio_quirk) {
		.vendor_name = "Roland",
		.product_name = "OCTA-CAPTURE",
		.ifnum = QUIRK_ANY_INTERFACE,
		.type = QUIRK_COMPOSITE,
		.data = &(const struct snd_usb_audio_quirk[]) {
			{
				.ifnum = 0,
				.type = QUIRK_AUDIO_STANDARD_INTERFACE
			},
			{
				.ifnum = 1,
				.type = QUIRK_AUDIO_STANDARD_INTERFACE
			},
			{
				.ifnum = 2,
				.type = QUIRK_MIDI_FIXED_ENDPOINT,
				.data = &(const struct snd_usb_midi_endpoint_info) {
					.out_cables = 0x0003,
					.in_cables = 0x0003
				}
			},
			{ .ifnum = 3, .type = QUIRK_IGNORE_INTERFACE },
			{ .ifnum = 4, .type = QUIRK_IGNORE_INTERFACE },
			{ .ifnum = -1 }
		}
	}
},
{
	USB_DEVICE(0x0582, 0x0121),
	.driver_info = (unsigned long)&(const struct snd_usb_audio_quirk) {
		.vendor_name = "Roland",
		.product_name = "OCTA-CAPTURE EXP",
		.ifnum = QUIRK_ANY_INTERFACE,
		.type = QUIRK_COMPOSITE,
		.data = &(const struct snd_usb_audio_quirk[]) {
			{
				.ifnum = 0,
				.type = QUIRK_AUDIO_STANDARD_INTERFACE
			},
			{
				.ifnum = 1,
				.type = QUIRK_AUDIO_STANDARD_INTERFACE
			},
			{
				.ifnum = 2,
				.type = QUIRK_MIDI_FIXED_ENDPOINT,
				.data = &(const struct snd_usb_midi_endpoint_info) {
					.out_cables = 0x0003,
					.in_cables = 0x0003
				}
			},
			{ .ifnum = 3, .type = QUIRK_IGNORE_INTERFACE },
			{ .ifnum = 4, .type = QUIRK_IGNORE_INTERFACE },
			{ .ifnum = -1 }
		}
	}
},
