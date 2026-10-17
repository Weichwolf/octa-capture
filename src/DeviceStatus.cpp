// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DeviceStatus.hpp"

#include <algorithm>
#include <cstdint>
#include <expected>
#include <span>

namespace octa {

std::expected<DeviceStatus, StatusError>
decode_device_status(std::span<const std::uint8_t> packet) {
  if (packet.size() != device_status_bytes) { return std::unexpected(StatusError::size); }
  if (packet[0] != 1) { return std::unexpected(StatusError::phase); }
  constexpr unsigned int middle_shift = 8;
  constexpr unsigned int high_shift = 16;
  const auto rate = static_cast<std::uint32_t>(packet[3]) |
                    (static_cast<std::uint32_t>(packet[4]) << middle_shift) |
                    (static_cast<std::uint32_t>(packet[5]) << high_shift);
  if (rate == 0) { return std::unexpected(StatusError::rate); }
  DeviceStatus result{.raw = {},
                      .sample_rate_hz = rate,
                      .clock_source =
                          (packet[6] & 1U) != 0 ? ClockSource::external : ClockSource::internal,
                      .clock_active = packet[7] != 0};
  std::ranges::copy(packet, result.raw.begin());
  return result;
}

}
