// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "octa/PcmMeters.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace fixture {

enum Offset : std::uint8_t {
  flags = 8,
  error = 12,
  capture_rate = 16,
  playback_rate = 20,
  capture_channels = 24,
  playback_channels = 28,
  capture_age = 32,
  playback_age = 36,
  capture_sequence = 40,
  playback_sequence = 44,
  capture_peak = 48,
  playback_peak = 96
};

using Packet = std::array<std::uint8_t, octa::pcm_meter_bytes>;

inline void put(Packet &packet, std::size_t offset, std::uint32_t value) {
  constexpr unsigned int byte_bits = 8;
  constexpr std::uint32_t byte_mask = 255;
  for (unsigned int index = 0; index < sizeof(value); ++index) {
    packet[offset + index] = static_cast<std::uint8_t>((value >> (index * byte_bits)) & byte_mask);
  }
}

inline Packet packet(std::uint32_t rate) {
  Packet result = {'O', 'C', 'P', 'K', 1, 0, octa::pcm_meter_bytes, 0};
  constexpr std::uint32_t high_rate = 192000;
  constexpr unsigned int active_flags = 15;
  put(result, flags, active_flags);
  put(result, capture_rate, rate);
  put(result, playback_rate, rate);
  put(result, capture_channels, rate == high_rate ? 4 : 12);
  put(result, playback_channels, rate == high_rate ? 4 : 10);
  put(result, capture_peak, octa::pcm_meter_peak_max);
  put(result, playback_peak, octa::pcm_meter_peak_max);
  return result;
}

}
