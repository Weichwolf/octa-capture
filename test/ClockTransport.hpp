// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "linux/usb.h"
#include "usbaudio.h"

#include <array>
#include <cstdint>

namespace octa::clock_test {
inline constexpr std::array<unsigned int, 4> rates = {44100, 48000, 96000, 192000};
inline constexpr unsigned int maximum_calls = 82;
inline constexpr unsigned int maximum_sleeps = 80;

struct Transport {
  usb_device device{};
  snd_usb_audio chip{&device};
  unsigned int rate = rates[1];
  std::uint8_t flags = 0;
  unsigned int busy_replies = 0;
  unsigned int ready_delay = 0;
  std::uint8_t ready_value = 1;
  unsigned int fault_call = 0;
  int fault_result = 0;
  bool wrong_rate = false;
  bool invalid_after_write = false;
  unsigned int calls = 0;
  unsigned int writes = 0;
  unsigned int sleeps = 0;
  unsigned int busy_queries = 0;
  unsigned int ready_queries = 0;
  bool invalid_request = false;
  std::array<std::uint8_t, 4> frame{};
};

Transport &transport();
void reset(unsigned int rate);
}
