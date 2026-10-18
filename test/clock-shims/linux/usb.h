/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef OCTA_CLOCK_TEST_USB_H
#define OCTA_CLOCK_TEST_USB_H
#include "ClockTypes.h"

#ifdef __cplusplus
inline constexpr u8 USB_DIR_IN = 0x80;
inline constexpr u8 USB_DIR_OUT = 0;
inline constexpr u8 USB_TYPE_VENDOR = 0x40;
inline constexpr u8 USB_RECIP_DEVICE = 0;
#else
enum { USB_DIR_IN = 0x80, USB_DIR_OUT = 0, USB_TYPE_VENDOR = 0x40, USB_RECIP_DEVICE = 0 };
#endif

struct usb_device {
  unsigned int identity;
};
#ifdef __cplusplus
extern "C" {
#endif
unsigned int usb_rcvctrlpipe(struct usb_device *device, unsigned int endpoint);
unsigned int usb_sndctrlpipe(struct usb_device *device, unsigned int endpoint);
#ifdef __cplusplus
}
#endif
#endif
