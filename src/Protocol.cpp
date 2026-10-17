// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <ranges>
#include <span>
#include <vector>

namespace octa {
namespace {

constexpr std::array<std::uint8_t, 6> header = {0xf0, 0x41, 0x10, 0, 0, 0x4d};
constexpr std::uint8_t end_exclusive = 0xf7;
constexpr std::uint32_t midi_base = 128;
constexpr std::uint32_t maximum_address = 0x0fffffff;
constexpr std::size_t command_offset = header.size();
constexpr std::size_t address_offset = command_offset + 1;
constexpr std::size_t payload_offset = address_offset + Address{}.size();
constexpr std::size_t overhead = payload_offset + 2;

bool midi_data(std::span<const std::uint8_t> bytes) noexcept {
  return std::ranges::all_of(bytes, [](std::uint8_t byte) { return byte < midi_base; });
}

bool valid_command(Command command) noexcept {
  return command == Command::request || command == Command::data;
}

bool valid_request(std::span<const std::uint8_t> payload) noexcept {
  return payload.size() == Address{}.size() &&
         std::ranges::any_of(payload, [](std::uint8_t byte) { return byte != 0; });
}

}

std::uint8_t checksum(std::span<const std::uint8_t> bytes) noexcept {
  std::uint32_t residue = 0;
  for (const auto byte : bytes) { residue = (residue + byte) % midi_base; }
  return static_cast<std::uint8_t>((midi_base - residue) % midi_base);
}

std::expected<std::vector<std::uint8_t>, ProtocolError>
encode(Command command, Address address, std::span<const std::uint8_t> payload) {
  if (payload.empty() || payload.size() > maximum_payload) {
    return std::unexpected(ProtocolError::size);
  }
  if (!valid_command(command)) { return std::unexpected(ProtocolError::command); }
  if (!midi_data(address) || !midi_data(payload)) {
    return std::unexpected(ProtocolError::midi_data);
  }
  if (command == Command::request && !valid_request(payload)) {
    return std::unexpected(ProtocolError::request_size);
  }
  std::vector<std::uint8_t> result;
  result.reserve(overhead + payload.size());
  result.insert(result.end(), header.begin(), header.end());
  result.push_back(static_cast<std::uint8_t>(command));
  result.insert(result.end(), address.begin(), address.end());
  result.insert(result.end(), payload.begin(), payload.end());
  result.push_back(checksum(std::span(result).subspan(address_offset)));
  result.push_back(end_exclusive);
  return result;
}

std::expected<Message, ProtocolError> decode(std::span<const std::uint8_t> frame) {
  if (frame.size() <= overhead || frame.size() > overhead + maximum_payload) {
    return std::unexpected(ProtocolError::size);
  }
  if (!std::ranges::equal(frame.first(header.size()), header) || frame.back() != end_exclusive) {
    return std::unexpected(ProtocolError::header);
  }
  const auto command = static_cast<Command>(frame[command_offset]);
  if (!valid_command(command)) { return std::unexpected(ProtocolError::command); }
  const auto body = frame.subspan(address_offset, frame.size() - address_offset - 1);
  if (!midi_data(body)) { return std::unexpected(ProtocolError::midi_data); }
  if (checksum(body) != 0) { return std::unexpected(ProtocolError::checksum); }
  const auto payload = frame.subspan(payload_offset, frame.size() - overhead);
  if (command == Command::request && !valid_request(payload)) {
    return std::unexpected(ProtocolError::request_size);
  }
  Address address{};
  std::ranges::copy(frame.subspan(address_offset, address.size()), address.begin());
  return Message{
      .command = command, .address = address, .payload = {payload.begin(), payload.end()}};
}

std::expected<std::vector<std::uint8_t>, ProtocolError> request(Address address,
                                                                std::uint32_t length) {
  if (length == 0 || length > maximum_address) {
    return std::unexpected(ProtocolError::request_size);
  }
  Address size{};
  for (auto &byte : std::views::reverse(size)) {
    byte = static_cast<std::uint8_t>(length % midi_base);
    length /= midi_base;
  }
  return encode(Command::request, address, size);
}

}
