// SPDX-License-Identifier: GPL-2.0-only
#include "ClockTransport.hpp"
extern "C" {
#include "octa.h"
}

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size);

namespace {
struct Case {
  unsigned int initial;
  int requested;
};

unsigned int initial_rate(std::span<const std::uint8_t> bytes) {
  constexpr unsigned int arbitrary = 0x80;
  constexpr unsigned int shift = 8;
  constexpr unsigned int high_shift = shift * 2;
  constexpr std::size_t extended_size = 11;
  if (bytes.size() >= extended_size && (bytes[0] & arbitrary) != 0) {
    return static_cast<unsigned int>(bytes[8]) | (static_cast<unsigned int>(bytes[9]) << shift) |
           (static_cast<unsigned int>(bytes[10]) << high_shift);
  }
  return octa::clock_test::rates[bytes[0] % octa::clock_test::rates.size()];
}

void check_write(const Case &test, int result) {
  const auto &context = octa::clock_test::transport();
  const bool valid =
      std::ranges::find(octa::clock_test::rates, static_cast<unsigned int>(test.requested)) !=
      octa::clock_test::rates.end();
  if ((!valid && (result != -EINVAL || context.calls != 0)) ||
      (result == 0 && context.rate != static_cast<unsigned int>(test.requested))) {
    __builtin_trap();
  }
  if (context.writes == 0) { return; }
  constexpr unsigned int byte_shift = 8;
  constexpr unsigned int high_shift = byte_shift * 2;
  const std::array<std::uint8_t, 4> expected = {
      0x40,
      static_cast<std::uint8_t>(test.requested),
      static_cast<std::uint8_t>(static_cast<unsigned int>(test.requested) >> byte_shift),
      static_cast<std::uint8_t>(static_cast<unsigned int>(test.requested) >> high_shift)};
  if (context.frame != expected) { __builtin_trap(); }
}
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  constexpr std::size_t input_size = 8;
  if (size < input_size) { return 0; }
  const std::span bytes(data, size);
  const std::array requested = {
      44100, 48000, 96000, 192000, 0, -1, 44101, std::numeric_limits<int>::max()};
  const std::array fault_results = {-EIO,
                                    -ENODEV,
                                    0,
                                    1,
                                    2,
                                    3,
                                    4,
                                    5,
                                    -EPIPE,
                                    -ETIMEDOUT,
                                    -ECONNRESET,
                                    std::numeric_limits<int>::min(),
                                    std::numeric_limits<int>::max()};
  const auto initial = initial_rate(bytes);
  const int desired = requested[bytes[1] % requested.size()];
  octa::clock_test::reset(initial);
  auto &context = octa::clock_test::transport();
  context.flags = bytes[2];
  context.busy_replies = bytes[3];
  context.ready_delay = bytes[4];
  constexpr std::size_t ready_index = 11;
  if (bytes.size() > ready_index) { context.ready_value = bytes[ready_index]; }
  context.fault_call = bytes[5];
  context.fault_result = fault_results[bytes[6] % fault_results.size()];
  context.wrong_rate = (bytes[7] & 1) != 0;
  context.invalid_after_write = (bytes[7] & 4) != 0;
  int result = 0;
  if ((bytes[7] & 2) != 0) {
    constexpr unsigned int sentinel = 17;
    unsigned int observed = sentinel;
    result = snd_usb_octa_capture_current_rate(&context.chip, &observed);
    if ((result == 0 && observed != initial) || (result != 0 && observed != sentinel) ||
        context.writes != 0) {
      __builtin_trap();
    }
  } else {
    result = snd_usb_octa_capture_set_rate(&context.chip, desired);
    check_write({.initial = initial, .requested = desired}, result);
  }
  if (result > 0 || context.invalid_request || context.calls > octa::clock_test::maximum_calls ||
      context.sleeps > octa::clock_test::maximum_sleeps || context.writes > 1) {
    __builtin_trap();
  }
  return 0;
}
