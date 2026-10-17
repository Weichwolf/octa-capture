// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <iosfwd>
#include <span>

namespace octa {

inline constexpr std::size_t pcm_meter_bytes = 136;
inline constexpr std::uint32_t pcm_meter_peak_max = 0x7fffff00;
inline constexpr std::uint32_t pcm_meter_stale_ms = 500;
enum class PcmMeterError : std::uint8_t { size, header, flags, direction, peak };

struct PcmMeterDirection {
  std::uint32_t rate = 0;
  std::uint32_t channels = 0;
  std::uint32_t age_ms = 0;
  std::uint32_t sequence = 0;
  bool fresh = false;
  std::array<std::uint32_t, 12> peaks{};
  bool operator==(const PcmMeterDirection &) const = default;
};

struct PcmMeters {
  bool enabled = false;
  bool idle_stream = false;
  std::uint32_t error = 0;
  PcmMeterDirection capture;
  PcmMeterDirection playback;
  bool operator==(const PcmMeters &) const = default;
};

// Fixed, versioned ALSA CARD value; rejects unknown formats and inconsistent data.
[[nodiscard]] std::expected<PcmMeters, PcmMeterError>
decode_pcm_meters(std::span<const std::uint8_t> packet);
void write_pcm_meters_json(std::ostream &output, const PcmMeters &meters);

}
