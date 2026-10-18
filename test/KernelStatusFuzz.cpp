// SPDX-License-Identifier: GPL-2.0-only
#include "KernelStatusTransport.hpp"
#include "StatusTypes.h"
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
using namespace octa::kernel_status_test;
constexpr std::size_t configuration_size = 8;
constexpr std::size_t reply_size = packet_size + 2;
constexpr unsigned int upper_byte_shift = 8;

void configure(std::span<const std::uint8_t> bytes) {
  auto &state = transport();
  state.missing_interface = (bytes[0] & 1) != 0;
  state.missing_alternate = (bytes[0] & 2) != 0;
  state.fail_control = (bytes[0] & 4) != 0;
  state.fail_allocation = (bytes[0] & 0x10) != 0 ? 2U : 0U;
  if ((bytes[0] & 8) != 0) { state.fail_allocation = 1; }
  state.lock_error = (bytes[0] & 0x20) != 0 ? -EINTR : 0;
  state.shutdown_error = (bytes[0] & 0x40) != 0 ? -ENODEV : 0;
  if ((bytes[0] & 0x80) != 0) { state.interface.cur_altsetting = &state.active; }
  state.active.desc.bNumEndpoints = bytes[1];
  state.endpoint.desc.bEndpointAddress = bytes[2];
  state.endpoint.desc.bmAttributes = bytes[3];
  state.endpoint.desc.wMaxPacketSize =
      static_cast<std::uint16_t>(static_cast<unsigned int>(bytes[4]) |
                                 (static_cast<unsigned int>(bytes[5]) << upper_byte_shift));
  switch (bytes[6] % 4) {
    case 0: break;
    case 1: state.alternate_error = -EPIPE; break;
    case 2: state.add_error = -ENOMEM; break;
    case 3: state.pcm_error = -EIO; break;
    default: __builtin_trap();
  }
  if ((bytes[7] & 1) != 0) { state.chip.usb_id = 0; }
  const std::array lengths = {8,
                              0,
                              1,
                              2,
                              3,
                              4,
                              5,
                              6,
                              7,
                              9,
                              -1,
                              std::numeric_limits<int>::max(),
                              std::numeric_limits<int>::min()};
  const std::array results = {
      0, -EIO, -ETIMEDOUT, -ENODEV, -ECONNRESET, -EPIPE, -EINTR, std::numeric_limits<int>::min()};
  for (std::size_t index = 0; index < packet_limit; ++index) {
    const auto reply = bytes.subspan(configuration_size + (index * reply_size), reply_size);
    std::ranges::copy(reply.first(packet_size), state.replies[index].packet.begin());
    state.replies[index].actual = lengths[reply[packet_size] % lengths.size()];
    state.replies[index].result = results[reply[packet_size + 1] % results.size()];
  }
}

void check_read() {
  auto &state = transport();
  std::array<std::uint8_t, output_size> output{};
  const auto result = get(output);
  const auto bytes = std::span(output);
  if (result > 0 || state.calls > packet_limit || !balanced()) { __builtin_trap(); }
  if (result != 0) {
    if (!std::ranges::all_of(bytes, [](auto value) { return value == sentinel; })) {
      __builtin_trap();
    }
    return;
  }
  if (state.calls < 2) { __builtin_trap(); }
  const auto &last = state.replies[state.calls - 1].packet;
  if (last[0] != 1 || (last[3] | last[4] | last[5]) == 0 ||
      !std::ranges::equal(bytes.first(packet_size), last) ||
      !std::ranges::all_of(bytes.subspan(packet_size),
                           [](auto value) { return value == sentinel; })) {
    __builtin_trap();
  }
  bool zero = false;
  for (std::size_t index = 0; index < state.calls; ++index) {
    const auto &reply = state.replies[index];
    if (reply.actual != packet_size || reply.result != 0 || reply.packet[0] > 1) {
      __builtin_trap();
    }
    if (index + 1 < state.calls && reply.packet[0] == 0) { zero = true; }
  }
  if (!zero) { __builtin_trap(); }
}
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  if (size < configuration_size + packet_limit * reply_size) { return 0; }
  octa::kernel_status_test::reset();
  configure(std::span(data, size));
  auto &state = octa::kernel_status_test::transport();
  const auto result = snd_usb_octa_capture_add_controls(&state.chip);
  if (result > 0 || !octa::kernel_status_test::balanced()) { __builtin_trap(); }
  if (result == 0 && state.control.private_data != nullptr) { check_read(); }
  octa::kernel_status_test::cleanup();
  if (!octa::kernel_status_test::balanced()) { __builtin_trap(); }
  return 0;
}
