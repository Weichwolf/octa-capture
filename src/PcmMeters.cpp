// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PcmMeters.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <limits>
#include <ostream>
#include <span>

namespace octa {
namespace {

constexpr unsigned int byte_bits = 8;
constexpr std::uint32_t byte_mask = 0xff;
constexpr unsigned int version_offset = 4;
constexpr unsigned int size_offset = 6;
constexpr unsigned int flags_offset = 8;
constexpr unsigned int error_offset = 12;
constexpr std::uint32_t known_flags = 15;
constexpr std::uint32_t maximum_errno = 4095;
constexpr std::array<std::uint32_t, 4> rates = {44100, 48000, 96000, 192000};
constexpr std::size_t capture_offset = 48;
constexpr std::size_t playback_offset = 96;

std::uint32_t word(std::span<const std::uint8_t> packet, std::size_t offset) {
  std::uint32_t result = 0;
  for (unsigned int index = 0; index < sizeof(result); ++index) {
    result |= static_cast<std::uint32_t>(packet[offset + index]) << (index * byte_bits);
  }
  return result;
}

bool valid_direction(const PcmMeterDirection &direction, std::uint32_t maximum) {
  if (direction.rate == 0 && direction.channels == 0) {
    return !direction.fresh && direction.age_ms == std::numeric_limits<std::uint32_t>::max();
  }
  bool valid_rate = false;
  for (const auto rate : rates) { valid_rate = valid_rate || direction.rate == rate; }
  const auto channels = direction.rate == rates.back() ? 4 : maximum;
  return valid_rate && direction.channels == channels &&
         direction.fresh == (direction.age_ms <= pcm_meter_stale_ms);
}

PcmMeterDirection
direction(std::span<const std::uint8_t> packet, bool capture, std::uint32_t flags) {
  constexpr std::size_t rate_offset = 16;
  constexpr std::size_t channels_offset = 24;
  constexpr std::size_t age_offset = 32;
  constexpr std::size_t sequence_offset = 40;
  const std::size_t displacement = capture ? 0 : sizeof(std::uint32_t);
  PcmMeterDirection result{.rate = word(packet, rate_offset + displacement),
                           .channels = word(packet, channels_offset + displacement),
                           .age_ms = word(packet, age_offset + displacement),
                           .sequence = word(packet, sequence_offset + displacement),
                           .fresh = (flags & (capture ? 4U : 8U)) != 0,
                           .peaks = {}};
  const std::size_t maximum = capture ? 12 : 10;
  const auto offset = capture ? capture_offset : playback_offset;
  for (std::size_t index = 0; index < maximum; ++index) {
    result.peaks[index] = word(packet, offset + (index * sizeof(std::uint32_t)));
  }
  return result;
}

bool valid_peaks(const PcmMeterDirection &direction) {
  for (std::size_t index = 0; index < direction.peaks.size(); ++index) {
    const auto peak = direction.peaks[index];
    if (peak > pcm_meter_peak_max || (peak & byte_mask) != 0 ||
        (index >= direction.channels && peak != 0)) {
      return false;
    }
  }
  return true;
}

void write_direction(std::ostream &output,
                     const PcmMeterDirection &direction,
                     std::size_t maximum) {
  output << R"({"rate":)" << direction.rate << R"(,"channels":)" << direction.channels
         << R"(,"age_ms":)" << direction.age_ms << R"(,"sequence":)" << direction.sequence
         << R"(,"fresh":)" << (direction.fresh ? "true" : "false") << R"(,"peaks":[)";
  for (std::size_t index = 0; index < maximum; ++index) {
    if (index != 0) { output << ','; }
    output << direction.peaks[index];
  }
  output << "]}";
}

}

std::expected<PcmMeters, PcmMeterError> decode_pcm_meters(std::span<const std::uint8_t> packet) {
  if (packet.size() != pcm_meter_bytes) { return std::unexpected(PcmMeterError::size); }
  if (packet[0] != 'O' || packet[1] != 'C' || packet[2] != 'P' || packet[3] != 'K' ||
      packet[version_offset] != 1 || packet[version_offset + 1] != 0 ||
      packet[size_offset] != pcm_meter_bytes || packet[size_offset + 1] != 0) {
    return std::unexpected(PcmMeterError::header);
  }
  const auto flags = word(packet, flags_offset);
  const auto error = word(packet, error_offset);
  if ((flags & ~known_flags) != 0 || error > maximum_errno) {
    return std::unexpected(PcmMeterError::flags);
  }
  PcmMeters result{.enabled = (flags & 1U) != 0,
                   .idle_stream = (flags & 2U) != 0,
                   .error = error,
                   .capture = direction(packet, true, flags),
                   .playback = direction(packet, false, flags)};
  if (!valid_direction(result.capture, 12) || !valid_direction(result.playback, 10)) {
    return std::unexpected(PcmMeterError::direction);
  }
  if (!valid_peaks(result.capture) || !valid_peaks(result.playback)) {
    return std::unexpected(PcmMeterError::peak);
  }
  return result;
}

void write_pcm_meters_json(std::ostream &output, const PcmMeters &meters) {
  output << R"({"enabled":)" << (meters.enabled ? "true" : "false") << R"(,"idle_stream":)"
         << (meters.idle_stream ? "true" : "false") << R"(,"error":)" << meters.error
         << R"(,"capture":)";
  write_direction(output, meters.capture, 12);
  output << R"(,"playback":)";
  write_direction(output, meters.playback, 10);
  output << '}';
}

}
