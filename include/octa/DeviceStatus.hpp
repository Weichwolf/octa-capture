// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>

namespace octa {

inline constexpr std::size_t device_status_bytes = 8;
enum class ClockSource : std::uint8_t { internal, external };
enum class StatusError : std::uint8_t { size, phase, rate };

struct DeviceStatus {
  std::array<std::uint8_t, device_status_bytes> raw;
  std::uint32_t sample_rate_hz;
  ClockSource clock_source;
  // Reference display activation; this is not a measured clock-quality result.
  bool clock_active;
  bool operator==(const DeviceStatus &) const = default;
};

// The kernel control returns the phase-1 packet after seeking phases 0 then 1.
// Unknown/reserved bytes remain intact, including bits above the source bit.
[[nodiscard]] std::expected<DeviceStatus, StatusError>
decode_device_status(std::span<const std::uint8_t> packet);

}
