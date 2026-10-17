// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <type_traits>
#include <variant>

namespace octa {
namespace {

using Field = std::variant<bool *, std::uint8_t *, std::uint16_t *, std::uint32_t *>;
using Selection = std::expected<Field, DspError>;
constexpr std::uint8_t input_group = 6;
constexpr std::uint8_t playback_group = 7;
constexpr std::uint8_t input_master_group = 8;
constexpr std::uint8_t playback_master_group = 9;
constexpr unsigned int bus_shift = 4;
constexpr std::uint8_t channel_mask = 15;
constexpr std::uint8_t midi_limit = 128;
constexpr std::uint8_t nibble_limit = 16;
constexpr std::uint8_t send_parameter = 0x0e;
constexpr std::uint8_t io_link_parameter = 0x0d;

Selection channel_field(DspState &state, Address address) {
  const std::size_t bus = address[2] >> bus_shift;
  const std::size_t channel = address[2] & channel_mask;
  if (bus >= state.mixers.size()) { return std::unexpected(DspError::message); }
  auto &mixer = state.mixers[bus];
  auto &group = address[1] == input_group ? mixer.input : mixer.playback;
  if (channel >= group.channels.size()) { return std::unexpected(DspError::message); }
  auto &strip = group.channels[channel];
  auto &pair = group.pairs[channel / 2];
  switch (address[3]) {
    case 0: return &pair.linked;
    case 1: return &pair.mono;
    case 2: return &strip.solo;
    case 3: return &strip.mute;
    case 4: return &strip.pan;
    case 8: return &strip.volume;
    case send_parameter:
      if (address[1] == input_group && bus == 0) { return &state.reverb.input_send[channel]; }
      return std::unexpected(DspError::message);
    default: return std::unexpected(DspError::message);
  }
}

Selection master_field(DspState &state, Address address) {
  const std::size_t bus = address[2] >> bus_shift;
  const std::size_t channel = address[2] & channel_mask;
  if (bus >= state.mixers.size() || channel >= 2) { return std::unexpected(DspError::message); }
  auto &mixer = state.mixers[bus];
  auto &master = address[1] == input_master_group ? mixer.input_master : mixer.playback_master;
  if (address[3] == 1) { return &master.volume[channel]; }
  if (channel != 0) { return std::unexpected(DspError::message); }
  if (address[3] == 0) { return &master.linked; }
  if (address[1] != input_master_group) { return std::unexpected(DspError::message); }
  if (address[3] == io_link_parameter) { return &mixer.input_output_link; }
  if (address[3] == 7 && bus == 0) { return &state.reverb.master_return; }
  return std::unexpected(DspError::message);
}

Selection reverb_field(ReverbState &state, Address address) {
  const std::size_t type = address[2];
  if (type >= state.pre_delay_indices.size()) { return std::unexpected(DspError::message); }
  switch (address[3]) {
    case 0:
      if (type == 0) { return &state.type; }
      return std::unexpected(DspError::message);
    case 1: return &state.pre_delay_indices[type];
    case 2: return &state.time_indices[type];
    default: return std::unexpected(DspError::message);
  }
}

Selection select_field(DspState &state, Address address) {
  if (address[0] != 0) { return std::unexpected(DspError::message); }
  switch (address[1]) {
    case 3:
      if (address[2] == 0 && address[3] < state.output_routes.size()) {
        return &state.output_routes[address[3]];
      }
      return std::unexpected(DspError::message);
    case 4: return reverb_field(state.reverb, address);
    case input_group:
    case playback_group: return channel_field(state, address);
    case input_master_group:
    case playback_master_group: return master_field(state, address);
    default: return std::unexpected(DspError::message);
  }
}

template <typename Value>
std::expected<Value, DspError> decode_value(std::span<const std::uint8_t> payload) {
  if constexpr (std::is_same_v<Value, bool> || std::is_same_v<Value, std::uint8_t>) {
    if (payload.size() != 1) { return std::unexpected(DspError::size); }
    if constexpr (std::is_same_v<Value, bool>) { return payload.front() != 0; }
    return payload.front();
  } else {
    constexpr std::size_t digits = std::is_same_v<Value, std::uint16_t> ? 4 : 6;
    if (payload.size() != digits) { return std::unexpected(DspError::size); }
    if (!std::ranges::all_of(payload, [](std::uint8_t byte) { return byte < nibble_limit; })) {
      return std::unexpected(DspError::data);
    }
    std::uint32_t result = 0;
    for (const auto digit : payload) { result = (result << bus_shift) | digit; }
    return static_cast<Value>(result);
  }
}

}

std::expected<bool, DspError> apply_dsp_update(DspState &state, const Message &message) {
  if (message.command != Command::data) { return std::unexpected(DspError::message); }
  const auto valid_byte = [](std::uint8_t byte) { return byte < midi_limit; };
  if (!std::ranges::all_of(message.address, valid_byte) ||
      !std::ranges::all_of(message.payload, valid_byte)) {
    return std::unexpected(DspError::data);
  }
  auto field = select_field(state, message.address);
  if (!field) { return std::unexpected(field.error()); }
  return std::visit(
      [&message](auto *destination) -> std::expected<bool, DspError> {
        using Value = std::remove_pointer_t<decltype(destination)>;
        const auto value = decode_value<Value>(message.payload);
        if (!value) { return std::unexpected(value.error()); }
        const bool changed = *destination != *value;
        *destination = *value;
        return changed;
      },
      *field);
}

}
