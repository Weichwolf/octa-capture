// SPDX-License-Identifier: GPL-2.0-only
#include "KernelStatusTransport.hpp"
#include "StatusTypes.h"
extern "C" {
#include "octa.h"
}

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <iostream>
#include <limits>
#include <span>
#include <string_view>

namespace {
using namespace octa::kernel_status_test;

bool require(bool condition, std::string_view message) {
  if (!condition) { std::cerr << "FAIL: " << message << '\n'; }
  return condition;
}

bool initialize() {
  if (!require(snd_usb_octa_capture_add_controls(&transport().chip) == 0,
               "create read-only status control")) {
    return false;
  }
  snd_ctl_elem_info info{};
  return require(transport().description.info(&transport().control, &info) == 0 &&
                     info.type == SNDRV_CTL_ELEM_TYPE_BYTES && info.count == packet_size &&
                     transport().pcm_calls == 1 && balanced(),
                 "status metadata, PCM-control delegation and ownership");
}

bool read_result(int expected) {
  std::array<std::uint8_t, output_size> output{};
  const auto result = get(output);
  const auto bytes = std::span(output);
  const auto untouched = result == 0 ? bytes.subspan(packet_size) : bytes;
  if (!require(result == expected && balanced() && transport().calls <= packet_limit &&
                   std::ranges::all_of(untouched, [](auto value) { return value == sentinel; }),
               "result, bounded reads, balanced resources and output preservation")) {
    return false;
  }
  return result != 0 ||
         require(std::ranges::equal(bytes.first(packet_size),
                                    transport().replies[transport().calls - 1].packet),
                 "successful status copies exactly the final eight raw bytes");
}

bool phases() {
  constexpr unsigned int combinations = 1U << packet_limit;
  for (unsigned int mask = 0; mask < combinations; ++mask) {
    reset();
    if (!initialize()) { return false; }
    bool zero = false;
    unsigned int expected_calls = packet_limit;
    int expected = -EAGAIN;
    for (unsigned int index = 0; index < packet_limit; ++index) {
      const auto phase = static_cast<std::uint8_t>((mask >> index) & 1U);
      transport().replies[index].packet = {phase, 6, 7, 0x80, rate_middle, 0, 0x40, 0x80};
      if (expected == 0) { continue; }
      if (zero && phase == 1) {
        expected = 0;
        expected_calls = index + 1;
      }
      zero = zero || phase == 0;
    }
    if (!read_result(expected) ||
        !require(transport().calls == expected_calls,
                 "all phase orderings stop at their first complete pair")) {
      return false;
    }
  }
  for (unsigned int phase = 2; phase <= std::numeric_limits<std::uint8_t>::max(); ++phase) {
    reset();
    if (!initialize()) { return false; }
    transport().replies[0].packet[0] = static_cast<std::uint8_t>(phase);
    if (!read_result(-EPROTO) ||
        !require(transport().calls == 1, "invalid phase fails immediately")) {
      return false;
    }
  }
  reset();
  if (!initialize()) { return false; }
  transport().replies[1].packet = {1, 0, 0, 0, 0, 0, 0, 0};
  return read_result(-ENODATA);
}

bool transfer_faults() {
  const std::array results = {
      -EIO, -ENODEV, -EPIPE, -ETIMEDOUT, -ECONNRESET, std::numeric_limits<int>::min()};
  const std::array lengths = {-1, 0, 1, 2, 3, 4, 5, 6, 7, 9, std::numeric_limits<int>::max()};
  for (unsigned int fault = 0; fault < packet_limit; ++fault) {
    for (const auto result : results) {
      reset();
      if (!initialize()) { return false; }
      transport().replies[1].packet[0] = 0;
      transport().replies.back().packet[0] = 1;
      transport().replies[fault].result = result;
      if (!read_result(result) ||
          !require(transport().calls == fault + 1, "USB failure stops without another read")) {
        return false;
      }
    }
    for (const auto length : lengths) {
      reset();
      if (!initialize()) { return false; }
      transport().replies[1].packet[0] = 0;
      transport().replies.back().packet[0] = 1;
      transport().replies[fault].actual = length;
      if (!read_result(-EIO) ||
          !require(transport().calls == fault + 1, "incorrect length stops before packet access")) {
        return false;
      }
    }
  }
  return true;
}

bool descriptors() {
  for (const auto field : {0, 1, 2, 3, 4, 5, 6}) {
    reset();
    if (!initialize()) { return false; }
    auto &state = transport();
    switch (field) {
      case 0: state.missing_interface = true; break;
      case 1: state.missing_alternate = true; break;
      case 2: state.active.desc.bNumEndpoints = 0; break;
      case 3: state.active.desc.bNumEndpoints = 2; break;
      case 4: state.endpoint.desc.bEndpointAddress = 2; break;
      case 5: state.endpoint.desc.bmAttributes = 2; break;
      case 6: state.endpoint.desc.wMaxPacketSize = packet_size - 1; break;
      default: return false;
    }
    if (!read_result(field == 0 ? -ENODEV : -EINVAL) ||
        !require(state.set_calls == 0 && state.calls == 0,
                 "invalid descriptor never selects or reads an endpoint")) {
      return false;
    }
  }
  reset();
  if (!initialize() || !read_result(0)) { return false; }
  transport().calls = 0;
  return read_result(0) &&
         require(transport().set_calls == 1, "already selected alternate is retained");
}

bool get_failures() {
  for (const auto field : {0, 1, 2, 3}) {
    reset();
    if (!initialize()) { return false; }
    auto &state = transport();
    int expected = 0;
    switch (field) {
      case 0: state.lock_error = expected = -EINTR; break;
      case 1: state.shutdown_error = expected = -ENODEV; break;
      case 2:
        state.fail_allocation = 2;
        expected = -ENOMEM;
        break;
      case 3: state.alternate_error = expected = -EPIPE; break;
      default: return false;
    }
    if (!read_result(expected) ||
        !require(state.calls == 0, "failed preparation never reads USB")) {
      return false;
    }
  }
  return true;
}

bool registration() {
  for (const auto field : {0, 1, 2, 3, 4}) {
    reset();
    auto &state = transport();
    int expected = -ENOMEM;
    if (field == 0) { state.fail_allocation = 1; }
    if (field == 1) { state.fail_control = true; }
    if (field == 2) { state.add_error = -ENOMEM; }
    if (field == 3) { state.pcm_error = expected = -EIO; }
    if (field == 4) {
      state.chip.usb_id = 0;
      expected = 0;
    }
    const auto result = snd_usb_octa_capture_add_controls(&state.chip);
    if (!require(result == expected && balanced() && state.calls == 0 &&
                     state.pcm_calls == (field == 3 ? 1U : 0U),
                 "control registration errors preserve ALSA ownership and exclude other devices")) {
      return false;
    }
    cleanup();
    if (!require(balanced(), "private control cleanup frees its state exactly once")) {
      return false;
    }
  }
  return true;
}
}

int main() {
  const bool passed =
      phases() && transfer_faults() && descriptors() && get_failures() && registration();
  octa::kernel_status_test::cleanup();
  if (!passed) { return 1; }
  std::cout << "PASS actual kernel status C: phase order, all invalid phases, "
               "transfer/descriptor/allocation/"
               "lock errors, bounded reads, output preservation and resource ownership\n";
}
