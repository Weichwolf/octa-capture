// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Meters.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <vector>

namespace octa {
namespace {

constexpr Address meter_address = {0, 10, 0, 1};
constexpr Address meter_switch_address = {0, 10, 0, 0};
constexpr std::uint8_t midi_limit = 128;
constexpr unsigned int midi_bits = 7;
constexpr std::uint16_t full_scale = 16383;
constexpr double decibel_multiplier = 20.0;
constexpr double silence_db = -96.0;

}

std::expected<InputMeters, MeterError> decode_input_meters(const Message &message) {
  if (message.command != Command::data || message.address != meter_address) {
    return std::unexpected(MeterError::message);
  }
  InputMeters result{};
  constexpr std::size_t bytes_per_level = 2;
  if (message.payload.size() != result.size() * bytes_per_level) {
    return std::unexpected(MeterError::size);
  }
  if (!std::ranges::all_of(message.payload, [](std::uint8_t byte) { return byte < midi_limit; })) {
    return std::unexpected(MeterError::data);
  }
  for (std::size_t channel = 0; channel < result.size(); ++channel) {
    const auto offset = channel * bytes_per_level;
    result[channel] = static_cast<std::uint16_t>((message.payload[offset] << midi_bits) |
                                                 message.payload[offset + 1]);
  }
  return result;
}

std::expected<double, MeterError> input_meter_db(std::uint16_t value) {
  if (value > full_scale) { return std::unexpected(MeterError::data); }
  if (value == 0) { return silence_db; }
  return decibel_multiplier * std::log10(static_cast<double>(value) / full_scale);
}

std::expected<std::vector<std::uint8_t>, ProtocolError> encode_meter_switch(bool enabled) {
  const std::array payload = {static_cast<std::uint8_t>(enabled)};
  return encode(Command::data, meter_switch_address, payload);
}

}
