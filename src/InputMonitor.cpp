// SPDX-License-Identifier: GPL-2.0-only
#include "octa/InputMonitor.hpp"

#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <vector>

namespace {

void write_nibbles(std::span<std::uint8_t> destination, std::uint32_t value) {
  constexpr unsigned int nibble_bits = 4;
  constexpr std::uint32_t nibble_mask = 0x0f;
  for (std::size_t index = destination.size(); index > 0; --index) {
    destination[index - 1] = static_cast<std::uint8_t>(value & nibble_mask);
    value >>= nibble_bits;
  }
}

}

namespace octa {

std::expected<std::vector<std::uint8_t>, ProtocolError>
encode_input_monitor(InputMonitorChange change) {
  constexpr std::uint8_t reference_channels = 12;
  constexpr std::uint8_t all_channels = 0x7f;
  constexpr std::uint8_t composite_parameter = 0x14;
  constexpr std::uint16_t maximum_pan = 0x8000;
  constexpr std::uint32_t maximum_volume = 0x7fffff;
  if ((change.channel && *change.channel >= reference_channels) || change.pan > maximum_pan ||
      change.volume > maximum_volume) {
    return std::unexpected(ProtocolError::midi_data);
  }
  constexpr std::size_t pan_digits = 4;
  constexpr std::size_t volume_digits = 6;
  std::array<std::uint8_t, 1 + pan_digits + volume_digits> payload{};
  payload[0] = static_cast<std::uint8_t>(!change.enabled);
  write_nibbles(std::span(payload).subspan(1, pan_digits), change.pan);
  write_nibbles(std::span(payload).last(volume_digits), change.volume);
  return encode(
      Command::data, {0, 6, change.channel.value_or(all_channels), composite_parameter}, payload);
}

}
