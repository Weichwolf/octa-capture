// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DeviceStatus.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool captured_rates() {
  constexpr std::array<std::uint32_t, 4> rates = {44100, 48000, 96000, 192000};
  constexpr std::array<std::array<std::uint8_t, 8>, 4> packets = {{{1, 0, 0, 0x44, 0xac, 0, 0, 1},
                                                                   {1, 0, 0, 0x80, 0xbb, 0, 0, 1},
                                                                   {1, 0, 0, 0, 0x77, 1, 0, 1},
                                                                   {1, 0, 0, 0, 0xee, 2, 0, 1}}};
  for (std::size_t index = 0; index < rates.size(); ++index) {
    const auto status = octa::decode_device_status(packets[index]);
    if (!require(status && status->raw == packets[index] &&
                     status->sample_rate_hz == rates[index] &&
                     status->clock_source == octa::ClockSource::internal && status->clock_active,
                 "captured status packets at all four hardware-checked rates")) {
      return false;
    }
  }
  return true;
}

bool flags() {
  constexpr std::uint8_t maximum_byte = 255;
  std::array<std::uint8_t, 8> packet = {1, maximum_byte, maximum_byte, 1, 0, 0, 0, 0};
  constexpr unsigned int byte_values = 256;
  constexpr std::array<std::uint8_t, 4> activity = {0, 1, 2, 255};
  for (unsigned int source = 0; source < byte_values; ++source) {
    packet[6] = static_cast<std::uint8_t>(source);
    for (const auto active : activity) {
      packet[7] = active;
      const auto status = octa::decode_device_status(packet);
      const auto expected =
          source % 2 == 0 ? octa::ClockSource::internal : octa::ClockSource::external;
      if (!require(status && status->raw == packet && status->clock_source == expected &&
                       status->clock_active == (active != 0),
                   "reference source bit/boolean activation and untouched reserved bytes")) {
        return false;
      }
    }
  }
  return true;
}

bool boundaries() {
  std::array<std::uint8_t, 9> packet = {1, 0, 0, 1, 0, 0, 0, 0, 0};
  for (std::size_t size = 0; size <= packet.size(); ++size) {
    const auto decoded = octa::decode_device_status(std::span(packet).first(size));
    if (!require(decoded.has_value() == (size == octa::device_status_bytes),
                 "exact status length")) {
      return false;
    }
  }
  packet[0] = 0;
  const auto phase = octa::decode_device_status(std::span(packet).first(8));
  if (!require(!phase && phase.error() == octa::StatusError::phase,
               "phase zero is not a final status value")) {
    return false;
  }
  packet[0] = 1;
  packet[3] = 0;
  const auto zero = octa::decode_device_status(std::span(packet).first(8));
  return require(!zero && zero.error() == octa::StatusError::rate,
                 "zero rate is not a coherent status value");
}

}

int main() {
  return captured_rates() && flags() && boundaries() ? 0 : 1;
}
