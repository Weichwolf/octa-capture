// SPDX-License-Identifier: GPL-2.0-only
#include "octa/AutoSense.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <utility>
#include <vector>

namespace octa {
namespace {

constexpr Address selection_address = {0, 2, 1, 4};
constexpr Address completion_address = {0, 2, 1, 5};
constexpr std::uint8_t nibble_mask = 15;
constexpr unsigned int nibble_bits = 4;
constexpr std::uint8_t midi_limit = 128;

}

std::expected<std::vector<std::uint8_t>, AutoSenseError>
encode_auto_sense_start(std::uint8_t channels) {
  if (channels == 0) { return std::unexpected(AutoSenseError::data); }
  const std::array payload = {static_cast<std::uint8_t>(channels & nibble_mask),
                              static_cast<std::uint8_t>(channels >> nibble_bits)};
  auto frame = encode(Command::data, selection_address, payload);
  if (!frame) { return std::unexpected(AutoSenseError::data); }
  return std::move(*frame);
}

std::expected<std::vector<std::uint8_t>, AutoSenseError>
encode_auto_sense_stop(AutoSenseAction action) {
  if (action != AutoSenseAction::finish && action != AutoSenseAction::cancel) {
    return std::unexpected(AutoSenseError::data);
  }
  const std::array payload = {static_cast<std::uint8_t>(action)};
  auto frame = encode(Command::data, completion_address, payload);
  if (!frame) { return std::unexpected(AutoSenseError::data); }
  return std::move(*frame);
}

std::expected<bool, AutoSenseError> apply_auto_sense_update(AutoSenseChannels &channels,
                                                            const Message &message) {
  if (message.command != Command::data ||
      (message.address != selection_address && message.address != completion_address)) {
    return std::unexpected(AutoSenseError::message);
  }
  const bool selection = message.address == selection_address;
  const std::size_t size = selection ? 2 : 1;
  if (message.payload.size() != size) { return std::unexpected(AutoSenseError::size); }
  if (!std::ranges::all_of(message.payload, [](std::uint8_t byte) { return byte < midi_limit; })) {
    return std::unexpected(AutoSenseError::data);
  }
  AutoSenseChannels next{};
  if (selection) {
    constexpr std::size_t flags_per_byte = 4;
    for (std::size_t channel = 0; channel < next.size(); ++channel) {
      next[channel] =
          (message.payload[channel / flags_per_byte] & (1U << (channel % flags_per_byte))) != 0;
    }
  }
  const bool changed = next != channels;
  channels = next;
  return changed;
}

}
