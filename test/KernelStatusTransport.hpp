// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "StatusTypes.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace octa::kernel_status_test {
inline constexpr std::size_t packet_size = 8;
inline constexpr std::uint8_t endpoint_address = 0x82;
inline constexpr std::size_t packet_limit = 4;
inline constexpr std::size_t output_size = 512;
inline constexpr std::uint8_t sentinel = 0xa5;
inline constexpr std::uint8_t rate_middle = 0xbb;
using Packet = std::array<std::uint8_t, packet_size>;

struct Reply {
  Packet packet{};
  int actual = packet_size;
  int result = 0;
};

struct Transport {
  usb_device device{};
  snd_card card{};
  snd_usb_audio chip{.dev = &device, .usb_id = USB_ID(0x0582, 0x0120), .card = &card};
  usb_host_endpoint endpoint{.desc = {.bEndpointAddress = endpoint_address,
                                      .bmAttributes = 3,
                                      .wMaxPacketSize = packet_size}};
  usb_host_interface inactive{.desc = {}, .endpoint = nullptr};
  usb_host_interface active{.desc = {.bAlternateSetting = 1, .bNumEndpoints = 1},
                            .endpoint = &endpoint};
  usb_interface interface{&inactive};
  std::array<Reply, packet_limit> replies{};
  snd_kcontrol control{};
  snd_kcontrol_new description{};
  std::array<void *, 2> allocations{};
  std::array<std::size_t, 2> allocation_sizes{};
  bool missing_interface = false;
  bool missing_alternate = false;
  bool fail_control = false;
  unsigned int fail_allocation = 0;
  int lock_error = 0;
  int shutdown_error = 0;
  int alternate_error = 0;
  int add_error = 0;
  int pcm_error = 0;
  unsigned int calls = 0;
  unsigned int allocations_attempted = 0;
  unsigned int frees = 0;
  unsigned int set_calls = 0;
  unsigned int shutdown_depth = 0;
  unsigned int mutex_depth = 0;
  unsigned int pcm_calls = 0;
  bool invalid_request = false;
};

Transport &transport();
void reset();
void cleanup();
int get(std::array<std::uint8_t, output_size> &output);
bool balanced();
}
