// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DirectOut.hpp"

#include "octa/DspControl.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <vector>

namespace octa {
namespace {

constexpr std::uint32_t unity = 0x200000;
constexpr std::uint32_t center = 0x4000;
constexpr std::uint32_t right = 0x8000;
constexpr std::size_t link_offset = 0x58;
constexpr std::size_t solo_offset = 0x68;
constexpr std::size_t mute_offset = 0x74;
constexpr std::size_t pan_offset = 0x80;
constexpr std::size_t volume_offset = 0x120;
constexpr std::size_t send_offset = 0x210;
constexpr std::size_t master_offset = 0x404;
constexpr unsigned int nibble_bits = 4;
constexpr std::uint32_t nibble_mask = 15;

struct FlagLocation {
  std::size_t offset;
  std::size_t index;
};

void flag(std::span<std::uint8_t> payload, FlagLocation location, bool value) {
  const auto offset = location.offset + (location.index / nibble_bits);
  const auto mask = static_cast<std::uint8_t>(1U << (location.index % nibble_bits));
  payload[offset] =
      value ? static_cast<std::uint8_t>(payload[offset] | mask)
            : static_cast<std::uint8_t>(payload[offset] & static_cast<std::uint8_t>(~mask));
}

void number(std::span<std::uint8_t> destination, std::uint32_t value) {
  for (std::size_t digit = destination.size(); digit > 0; --digit) {
    destination[digit - 1] = static_cast<std::uint8_t>(value & nibble_mask);
    value >>= nibble_bits;
  }
}

struct MixerPosition {
  std::size_t bus;
  std::size_t channel;
};

std::uint32_t pan(MixerPosition position) {
  const auto channel = position.channel;
  const auto bus = position.bus;
  if (channel / 2 == bus || channel / 2 == 4) { return channel % 2 == 0 ? 0 : right; }
  return center;
}

void prepare_bus(std::span<std::uint8_t> payload, std::size_t bus) {
  constexpr std::size_t channels = 10;
  constexpr std::size_t volume_digits = 6;
  constexpr std::size_t pan_digits = 4;
  for (std::size_t channel = 0; channel < channels; ++channel) {
    const auto index = (bus * channels) + channel;
    number(payload.subspan(pan_offset + (index * pan_digits), pan_digits),
           pan({.bus = bus, .channel = channel}));
    number(payload.subspan(volume_offset + (index * volume_digits), volume_digits),
           channel / 2 == bus ? unity : 0);
    flag(payload, {.offset = solo_offset + (bus * 3), .index = channel}, false);
    flag(payload, {.offset = mute_offset + (bus * 3), .index = channel}, false);
  }
  for (std::size_t pair = 0; pair < 5; ++pair) {
    flag(payload, {.offset = link_offset + (bus * 2), .index = pair}, pair == 4);
  }
  payload[master_offset + bus] = static_cast<std::uint8_t>(payload[master_offset + bus] | 1U);
  for (std::size_t channel = 0; channel < 2; ++channel) {
    number(
        payload.subspan(master_offset + 4 + (((bus * 2) + channel) * volume_digits), volume_digits),
        unity);
  }
}

}

std::vector<DspChange> direct_out_changes() {
  std::vector<DspChange> changes;
  changes.reserve(direct_out_change_count);
  for (std::uint8_t pair = 0; pair < 4; ++pair) {
    changes.emplace_back(RouteChange{.pair = pair, .selection = pair});
  }
  for (std::uint8_t bus = 0; bus < 4; ++bus) {
    for (std::uint8_t channel = 0; channel < 10; ++channel) {
      const auto mixer = [&changes, bus, channel](MixerParameter parameter, std::uint32_t value) {
        changes.emplace_back(MixerChange{.source = MixerSource::input,
                                         .bus = bus,
                                         .channel = channel,
                                         .parameter = parameter,
                                         .value = value});
      };
      mixer(MixerParameter::volume, channel / 2 == bus ? unity : 0);
      mixer(MixerParameter::pan, pan({.bus = bus, .channel = channel}));
      if (channel % 2 == 0) { mixer(MixerParameter::stereo_link, channel / 2 == 4 ? 1U : 0U); }
      mixer(MixerParameter::mute, 0);
      mixer(MixerParameter::solo, 0);
      if (bus == 0) { mixer(MixerParameter::reverb_send, 0); }
    }
    for (std::uint8_t channel = 0; channel < 2; ++channel) {
      changes.emplace_back(MasterChange{.source = MixerSource::input,
                                        .bus = bus,
                                        .channel = channel,
                                        .parameter = MasterParameter::volume,
                                        .value = unity});
    }
    changes.emplace_back(MasterChange{.source = MixerSource::input,
                                      .bus = bus,
                                      .channel = 0,
                                      .parameter = MasterParameter::stereo_link,
                                      .value = 1});
  }
  return changes;
}

std::expected<Message, DirectOutError> direct_out_target(const Message &current) {
  if (current.command != Command::data || current.address != bulk_address ||
      current.payload.size() != bulk_size) {
    return std::unexpected(DirectOutError::message);
  }
  if (!validate_bulk_write(current)) { return std::unexpected(DirectOutError::range); }
  if (current.payload[2] >= 3) { return std::unexpected(DirectOutError::rate); }
  auto target = current;
  auto payload = std::span(target.payload);
  for (std::size_t bus = 0; bus < 4; ++bus) {
    payload[5 + bus] = static_cast<std::uint8_t>(bus);
    prepare_bus(payload, bus);
  }
  constexpr std::size_t sends = 10;
  constexpr std::size_t volume_digits = 6;
  for (std::size_t channel = 0; channel < sends; ++channel) {
    number(payload.subspan(send_offset + (channel * volume_digits), volume_digits), 0);
  }
  return target;
}

}
