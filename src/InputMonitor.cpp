// SPDX-License-Identifier: GPL-2.0-only
#include "octa/InputMonitor.hpp"

#include "octa/Dsp.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <vector>

namespace {

constexpr std::uint8_t reference_channels = 12;
constexpr std::uint8_t all_channels = 0x7f;
constexpr std::uint8_t composite_parameter = 0x14;
constexpr std::uint16_t maximum_pan = 0x8000;
constexpr std::uint32_t maximum_volume = 0x7fffff;

bool valid(octa::InputMonitorChange change) {
  return (!change.channel || *change.channel < reference_channels) && change.pan <= maximum_pan &&
         change.volume <= maximum_volume;
}

void write_nibbles(std::span<std::uint8_t> destination, std::uint32_t value) {
  constexpr unsigned int nibble_bits = 4;
  constexpr std::uint32_t nibble_mask = 0x0f;
  for (std::size_t index = destination.size(); index > 0; --index) {
    destination[index - 1] = static_cast<std::uint8_t>(value & nibble_mask);
    value >>= nibble_bits;
  }
}

void update_input(std::span<std::uint8_t> payload,
                  std::size_t channel,
                  octa::InputMonitorChange change,
                  bool update_pan) {
  constexpr std::size_t mute_offset = 0x74;
  constexpr std::size_t pan_offset = 0x80;
  constexpr std::size_t volume_offset = 0x120;
  constexpr std::size_t flags_per_byte = 4;
  constexpr std::size_t pan_digits = 4;
  constexpr std::size_t volume_digits = 6;
  auto &flags = payload[mute_offset + (channel / flags_per_byte)];
  const auto mask = 1U << (channel % flags_per_byte);
  flags = static_cast<std::uint8_t>((flags & ~mask) | (change.enabled ? 0U : mask));
  if (update_pan) {
    write_nibbles(payload.subspan(pan_offset + (channel * pan_digits), pan_digits), change.pan);
  }
  write_nibbles(payload.subspan(volume_offset + (channel * volume_digits), volume_digits),
                change.volume);
}

}

namespace octa {

std::expected<std::vector<std::uint8_t>, ProtocolError>
encode_input_monitor(InputMonitorChange change) {
  if (!valid(change)) { return std::unexpected(ProtocolError::midi_data); }
  constexpr std::size_t pan_digits = 4;
  constexpr std::size_t volume_digits = 6;
  std::array<std::uint8_t, 1 + pan_digits + volume_digits> payload{};
  payload[0] = static_cast<std::uint8_t>(!change.enabled);
  write_nibbles(std::span(payload).subspan(1, pan_digits), change.pan);
  write_nibbles(std::span(payload).last(volume_digits), change.volume);
  return encode(
      Command::data, {0, 6, change.channel.value_or(all_channels), composite_parameter}, payload);
}

std::expected<Message, ProtocolError> input_monitor_target(const Message &before,
                                                           InputMonitorChange change) {
  const auto state = decode_dsp(before);
  if (!state || !valid(change)) { return std::unexpected(ProtocolError::midi_data); }
  constexpr std::size_t rate_offset = 2;
  constexpr std::uint8_t highest_rate = 3;
  constexpr std::size_t stored_channels = 10;
  const auto rate_index = before.payload[rate_offset];
  if (rate_index > highest_rate) { return std::unexpected(ProtocolError::midi_data); }
  const std::size_t active = rate_index == highest_rate ? 4 : stored_channels;
  if (change.channel && *change.channel >= active) {
    return std::unexpected(ProtocolError::midi_data);
  }
  auto target = before;
  if (!change.channel) {
    for (std::size_t channel = 0; channel < stored_channels; ++channel) {
      update_input(target.payload, channel, change, true);
    }
  } else {
    const auto channel = *change.channel;
    update_input(target.payload, channel, change, true);
    if (state->mixers[0].input.pairs[channel / 2].linked) {
      update_input(target.payload, channel ^ 1U, change, false);
    }
  }
  return target;
}

}
