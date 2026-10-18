/* SPDX-License-Identifier: GPL-2.0-only */
/* Offline adapters only. These layouts are not a Linux ABI and never enter Kbuild. */
#ifndef OCTA_STATUS_TEST_TYPES_H
#define OCTA_STATUS_TEST_TYPES_H
#ifdef __cplusplus
#include <cstddef>
#include <cstdint>
using u8 = std::uint8_t;
using u16 = std::uint16_t;
#else
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
#endif

#ifdef __cplusplus
extern "C" {
#endif

#ifdef __cplusplus
inline constexpr unsigned int GFP_KERNEL = 0;
inline constexpr unsigned int SNDRV_CTL_ELEM_TYPE_BYTES = 4;
inline constexpr unsigned int SNDRV_CTL_ELEM_IFACE_CARD = 0;
inline constexpr unsigned int SNDRV_CTL_ELEM_ACCESS_READ = 1;
inline constexpr unsigned int SNDRV_CTL_ELEM_ACCESS_VOLATILE = 4;
#else
enum {
  GFP_KERNEL = 0,
  SNDRV_CTL_ELEM_TYPE_BYTES = 4,
  SNDRV_CTL_ELEM_IFACE_CARD = 0,
  SNDRV_CTL_ELEM_ACCESS_READ = 1,
  SNDRV_CTL_ELEM_ACCESS_VOLATILE = 4
};
#endif

#define USB_ID(vendor, product) (((vendor) << 16) | (product))

struct usb_device {
  unsigned int identity;
};

struct usb_endpoint_descriptor {
  u8 bEndpointAddress;
  u8 bmAttributes;
  u16 wMaxPacketSize;
};

struct usb_host_endpoint {
  struct usb_endpoint_descriptor desc;
};

struct usb_interface_descriptor {
  u8 bAlternateSetting;
  u8 bNumEndpoints;
};

struct usb_host_interface {
  struct usb_interface_descriptor desc;
  struct usb_host_endpoint *endpoint;
};

struct usb_interface {
  struct usb_host_interface *cur_altsetting;
};

struct snd_card {
  unsigned int identity;
};

struct snd_usb_audio {
  struct usb_device *dev;
  unsigned int usb_id;
  struct snd_card *card;
};

struct mutex {
  bool held;
};

struct snd_ctl_elem_info {
  unsigned int type;
  unsigned int count;
};

struct snd_ctl_elem_value {
  struct {
    struct {
      u8 data[512];
    } bytes;
  } value;
};

struct snd_kcontrol {
  void *private_data;
  void (*private_free)(struct snd_kcontrol *);
};

struct snd_kcontrol_new {
  unsigned int iface;
  const char *name;
  unsigned int access;
  int (*info)(struct snd_kcontrol *, struct snd_ctl_elem_info *);
  int (*get)(struct snd_kcontrol *, struct snd_ctl_elem_value *);
};

struct octa_test_interrupt {
  struct usb_device *device;
  unsigned int pipe;
  void *data;
  int length;
  int *actual;
  int timeout;
};

void mutex_init(struct mutex *lock);
int mutex_lock_interruptible(struct mutex *lock);
void mutex_unlock(struct mutex *lock);
void *kmalloc(size_t size, unsigned int flags);
void *kzalloc(size_t size, unsigned int flags);
void kfree(const void *pointer);
struct usb_interface *usb_ifnum_to_if(struct usb_device *device, unsigned int index);
struct usb_host_interface *usb_altnum_to_altsetting(struct usb_interface *interface,
                                                    unsigned int index);
int usb_endpoint_is_int_in(const struct usb_endpoint_descriptor *endpoint);
unsigned int usb_endpoint_maxp(const struct usb_endpoint_descriptor *endpoint);
int usb_set_interface(struct usb_device *device, int index, int alternate);
unsigned int usb_rcvintpipe(struct usb_device *device, unsigned int endpoint);
int octa_test_interrupt_msg(const struct octa_test_interrupt *request);
int snd_usb_lock_shutdown(struct snd_usb_audio *chip);
void snd_usb_unlock_shutdown(struct snd_usb_audio *chip);
void *snd_kcontrol_chip(struct snd_kcontrol *control);
struct snd_kcontrol *snd_ctl_new1(const struct snd_kcontrol_new *description, void *data);
int snd_ctl_add(struct snd_card *card, struct snd_kcontrol *control);
#ifdef __cplusplus
}
#else
#define usb_interrupt_msg(device_arg, pipe_arg, data_arg, length_arg, actual_arg, timeout_arg)     \
  octa_test_interrupt_msg(&(const struct octa_test_interrupt){.device = (device_arg),              \
                                                              .pipe = (pipe_arg),                  \
                                                              .data = (data_arg),                  \
                                                              .length = (length_arg),              \
                                                              .actual = (actual_arg),              \
                                                              .timeout = (timeout_arg)})
#endif
#endif
