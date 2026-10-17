// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <vector>

namespace octa {

using Address = std::array<std::uint8_t, 4>;

enum class Command : std::uint8_t { request = 0x11, data = 0x12 };

enum class ProtocolError : std::uint8_t {
  size,
  header,
  command,
  midi_data,
  checksum,
  request_size
};

struct Message {
  Command command;
  Address address;
  std::vector<std::uint8_t> payload;
  bool operator==(const Message &) const = default;
};

inline constexpr std::size_t maximum_payload = 65535;
inline constexpr Address bulk_address = {1, 0, 0, 0};
inline constexpr std::uint32_t bulk_size = 1146;

[[nodiscard]] std::uint8_t checksum(std::span<const std::uint8_t> bytes) noexcept;
[[nodiscard]] std::expected<std::vector<std::uint8_t>, ProtocolError>
encode(Command command, Address address, std::span<const std::uint8_t> payload);
[[nodiscard]] std::expected<Message, ProtocolError> decode(std::span<const std::uint8_t> frame);
[[nodiscard]] std::expected<std::vector<std::uint8_t>, ProtocolError> request(Address address,
                                                                              std::uint32_t length);

}
