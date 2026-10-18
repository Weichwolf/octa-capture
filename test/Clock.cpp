// SPDX-License-Identifier: GPL-2.0-only
#include "ClockTransport.hpp"
extern "C" {
#include "octa.h"
}

#include <array>
#include <cerrno>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string_view>

namespace {
using octa::clock_test::reset;
using octa::clock_test::transport;
constexpr unsigned int rate_44100 = 44100;
constexpr unsigned int rate_48000 = 48000;
constexpr unsigned int invalid_rate = 44101;
constexpr unsigned int sentinel_rate = 17;
constexpr unsigned int busy_limit = 40;
constexpr unsigned int nearly_busy_limit = busy_limit - 1;
constexpr unsigned int byte_shift = 8;
constexpr unsigned int high_shift = byte_shift * 2;

bool require(bool condition, std::string_view message) {
  if (!condition) { std::cerr << "FAIL: " << message << '\n'; }
  return condition;
}

bool queries() {
  for (const auto rate : octa::clock_test::rates) {
    reset(rate);
    unsigned int observed = sentinel_rate;
    if (!require(snd_usb_octa_capture_current_rate(&transport().chip, &observed) == 0 &&
                     observed == rate && transport().calls == 2 && transport().writes == 0,
                 "supported, ready clock query")) {
      return false;
    }
    for (const auto flags : {1, 2, 3}) {
      reset(rate);
      transport().flags = static_cast<std::uint8_t>(flags);
      observed = sentinel_rate;
      if (!require(snd_usb_octa_capture_current_rate(&transport().chip, &observed) == -EAGAIN &&
                       observed == sentinel_rate && transport().calls == 1,
                   "unstable status preserves caller output and never checks readiness")) {
        return false;
      }
    }
  }
  reset(invalid_rate);
  unsigned int observed = sentinel_rate;
  if (!require(snd_usb_octa_capture_current_rate(&transport().chip, &observed) == -ERANGE &&
                   observed == sentinel_rate,
               "unsupported reported rate is rejected")) {
    return false;
  }
  reset(rate_48000);
  transport().ready_delay = 1;
  if (!require(snd_usb_octa_capture_current_rate(&transport().chip, &observed) == -EAGAIN &&
                   observed == sentinel_rate,
               "not-ready status preserves caller output")) {
    return false;
  }
  for (const auto ready : {2, 255}) {
    reset(rate_48000);
    transport().ready_value = static_cast<std::uint8_t>(ready);
    if (!require(snd_usb_octa_capture_current_rate(&transport().chip, &observed) == -EAGAIN &&
                     observed == sentinel_rate,
                 "noncanonical readiness is not accepted")) {
      return false;
    }
  }
  return true;
}

struct RateCase {
  unsigned int initial;
  unsigned int requested;
  std::uint8_t flags;
};

bool rate_case(const RateCase &test) {
  reset(test.initial);
  transport().flags = test.flags;
  const auto result =
      snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(test.requested));
  const unsigned int writes = test.initial == test.requested && test.flags == 0 ? 0U : 1U;
  if (!require(result == 0 && transport().rate == test.requested && transport().writes == writes &&
                   !transport().invalid_request,
               "all rate pairs and same-rate negotiation")) {
    return false;
  }
  if (writes == 0) { return true; }
  const std::array<std::uint8_t, 4> expected = {
      static_cast<std::uint8_t>(test.initial == test.requested ? 1 : 0x40),
      static_cast<std::uint8_t>(test.requested),
      static_cast<std::uint8_t>(test.requested >> byte_shift),
      static_cast<std::uint8_t>(test.requested >> high_shift)};
  return require(transport().frame == expected, "reference clock request bytes");
}

bool rate_changes() {
  for (const auto initial : octa::clock_test::rates) {
    for (const auto requested : octa::clock_test::rates) {
      for (const auto flags : {0, 1}) {
        if (!rate_case({.initial = initial,
                        .requested = requested,
                        .flags = static_cast<std::uint8_t>(flags)})) {
          return false;
        }
      }
    }
  }
  return true;
}

bool invalid_requests() {
  for (const int rate : {0,
                         -1,
                         static_cast<int>(invalid_rate),
                         std::numeric_limits<int>::max(),
                         std::numeric_limits<int>::min()}) {
    reset(rate_48000);
    if (!require(snd_usb_octa_capture_set_rate(&transport().chip, rate) == -EINVAL &&
                     transport().calls == 0,
                 "unsupported requests fail before USB")) {
      return false;
    }
  }
  reset(rate_44100);
  transport().flags = 2;
  return require(snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000)) ==
                         -EIO &&
                     transport().writes == 0,
                 "invalid clock flags reject a rate change before writing");
}

bool faults() {
  for (unsigned int call = 1; call <= 4; ++call) {
    const int expected_size = call == 4 ? 1 : 4;
    for (const int result : {-EIO, -ENODEV, 0, 1, 2, 3, 4, 5}) {
      if (result == expected_size) { continue; }
      reset(rate_44100);
      transport().fault_call = call;
      transport().fault_result = result;
      const int actual =
          snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000));
      if (!require(actual == (result < 0 ? result : -EIO),
                   "USB error, short and oversized response propagation at every stage")) {
        return false;
      }
    }
  }
  reset(rate_44100);
  transport().wrong_rate = true;
  if (!require(snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000)) ==
                   -EIO,
               "post-write rate mismatch is not accepted")) {
    return false;
  }
  reset(rate_44100);
  transport().invalid_after_write = true;
  return require(snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000)) ==
                     -EIO,
                 "new invalid clock flags after a write are not accepted");
}

bool bounded_polling() {
  reset(rate_44100);
  transport().busy_replies = busy_limit;
  if (!require(snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000)) ==
                       -ETIMEDOUT &&
                   transport().sleeps == busy_limit && transport().calls == busy_limit + 2,
               "busy clock polling has a fixed attempt bound")) {
    return false;
  }
  reset(rate_44100);
  transport().busy_replies = nearly_busy_limit;
  transport().ready_delay = nearly_busy_limit;
  if (!require(snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000)) ==
                       0 &&
                   transport().calls == octa::clock_test::maximum_calls &&
                   transport().sleeps == nearly_busy_limit * 2 && !transport().invalid_request,
               "last allowed busy and readiness responses succeed")) {
    return false;
  }
  reset(rate_48000);
  transport().ready_delay = busy_limit;
  return require(snd_usb_octa_capture_set_rate(&transport().chip, static_cast<int>(rate_48000)) ==
                         -ETIMEDOUT &&
                     transport().calls == busy_limit + 1 && transport().sleeps == busy_limit,
                 "same-rate readiness timeout is bounded without a write");
}
}

int main() {
  if (!queries() || !rate_changes() || !invalid_requests() || !faults() || !bounded_polling()) {
    return 1;
  }
  std::cout
      << "PASS actual clock C source: rate pairs, negotiation, USB faults and polling bounds\n";
}
