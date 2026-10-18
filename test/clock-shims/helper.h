/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef OCTA_CLOCK_TEST_HELPER_H
#define OCTA_CLOCK_TEST_HELPER_H
#include "linux/usb.h"

struct octa_test_request {
  struct usb_device *device;
  unsigned int pipe;
  u8 request;
  u8 request_type;
  u16 value;
  u16 index;
  void *data;
  u16 size;
};
#ifdef __cplusplus
extern "C" {
#endif
int octa_test_ctl_msg(const struct octa_test_request *request);
#ifdef __cplusplus
}
#else
#define snd_usb_ctl_msg(                                                                           \
    device_arg, pipe_arg, request_arg, type_arg, value_arg, index_arg, data_arg, size_arg)         \
  octa_test_ctl_msg(&(const struct octa_test_request){.device = (device_arg),                      \
                                                      .pipe = (pipe_arg),                          \
                                                      .request = (request_arg),                    \
                                                      .request_type = (type_arg),                  \
                                                      .value = (value_arg),                        \
                                                      .index = (index_arg),                        \
                                                      .data = (data_arg),                          \
                                                      .size = (size_arg)})
#endif
#endif
