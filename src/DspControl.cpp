// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DspControl.hpp"

#include "octa/Dsp.hpp"
#include "octa/Protocol.hpp"

#include <cstddef>
#include <cstdint>
#include <expected>
#include <utility>
#include <variant>
#include <vector>

namespace octa {
namespace {

constexpr std::uint32_t maximum_pan = 0x8000;
constexpr std::uint32_t maximum_volume = 0x7fffff;
constexpr std::uint8_t maximum_pre_delay = 12;
constexpr std::uint8_t maximum_time = 49;
constexpr std::uint8_t reverb_types = 5;
constexpr std::uint8_t output_pairs = 5;
constexpr std::uint8_t route_selections = 9;
constexpr std::uint8_t bus_count = 4;
constexpr std::uint8_t channel_count = 10;
constexpr unsigned int nibble_bits = 4;
constexpr std::uint32_t nibble_mask = 15;

bool valid_source(MixerSource source) {
  return source == MixerSource::input || source == MixerSource::playback;
}

template <std::size_t digits> Message scalar(Address address, std::uint32_t value) {
  std::vector<std::uint8_t> payload(digits);
  for (std::size_t digit = digits; digit > 0; --digit) {
    payload[digit - 1] = static_cast<std::uint8_t>(value & nibble_mask);
    value >>= nibble_bits;
  }
  return {.command = Command::data, .address = address, .payload = std::move(payload)};
}

Message byte_value(Address address, std::uint8_t value) {
  return {.command = Command::data, .address = address, .payload = {value}};
}

std::expected<Message, DspError> boolean(Address address, std::uint32_t value) {
  if (value > 1) { return std::unexpected(DspError::data); }
  return byte_value(address, static_cast<std::uint8_t>(value));
}

std::expected<Message, DspError> volume(Address address, std::uint32_t value) {
  if (value > maximum_volume) { return std::unexpected(DspError::data); }
  return scalar<6>(address, value);
}

std::expected<Message, DspError> make_update(MixerChange change) {
  if (!valid_source(change.source) || change.bus >= bus_count || change.channel >= channel_count) {
    return std::unexpected(DspError::message);
  }
  const Address address = {0,
                           static_cast<std::uint8_t>(6 + static_cast<std::uint8_t>(change.source)),
                           static_cast<std::uint8_t>((change.bus << nibble_bits) | change.channel),
                           static_cast<std::uint8_t>(change.parameter)};
  switch (change.parameter) {
    case MixerParameter::stereo_link:
    case MixerParameter::mono:
      if (change.channel % 2 != 0) { return std::unexpected(DspError::message); }
      return boolean(address, change.value);
    case MixerParameter::solo:
    case MixerParameter::mute: return boolean(address, change.value);
    case MixerParameter::pan:
      if (change.value > maximum_pan) { return std::unexpected(DspError::data); }
      return scalar<4>(address, change.value);
    case MixerParameter::volume: return volume(address, change.value);
    case MixerParameter::reverb_send:
      if (change.source != MixerSource::input || change.bus != 0) {
        return std::unexpected(DspError::message);
      }
      return volume(address, change.value);
    default: return std::unexpected(DspError::message);
  }
}

std::expected<Message, DspError> make_update(MasterChange change) {
  if (!valid_source(change.source) || change.bus >= bus_count || change.channel >= 2) {
    return std::unexpected(DspError::message);
  }
  const Address address = {0,
                           static_cast<std::uint8_t>(8 + static_cast<std::uint8_t>(change.source)),
                           static_cast<std::uint8_t>((change.bus << nibble_bits) | change.channel),
                           static_cast<std::uint8_t>(change.parameter)};
  if (change.parameter != MasterParameter::volume && change.channel != 0) {
    return std::unexpected(DspError::message);
  }
  switch (change.parameter) {
    case MasterParameter::stereo_link: return boolean(address, change.value);
    case MasterParameter::input_output_link:
      if (change.source == MixerSource::input) { return boolean(address, change.value); }
      return std::unexpected(DspError::message);
    case MasterParameter::reverb_return:
      if (change.source == MixerSource::input && change.bus == 0) {
        return volume(address, change.value);
      }
      return std::unexpected(DspError::message);
    case MasterParameter::volume: return volume(address, change.value);
    default: return std::unexpected(DspError::message);
  }
}

std::expected<Message, DspError> make_update(ReverbChange change) {
  const Address address = {0, 4, change.bank, static_cast<std::uint8_t>(change.parameter)};
  if (change.parameter == ReverbParameter::type) {
    if (change.bank != 0) { return std::unexpected(DspError::message); }
    if (change.value >= reverb_types) { return std::unexpected(DspError::data); }
    return byte_value(address, change.value);
  }
  if (change.bank == 0 || change.bank >= reverb_types) {
    return std::unexpected(DspError::message);
  }
  switch (change.parameter) {
    case ReverbParameter::pre_delay:
      if (change.value > maximum_pre_delay) { return std::unexpected(DspError::data); }
      return byte_value(address, change.value);
    case ReverbParameter::time:
      if (change.value > maximum_time) { return std::unexpected(DspError::data); }
      return byte_value(address, change.value);
    case ReverbParameter::type:
    default: return std::unexpected(DspError::message);
  }
}

std::expected<Message, DspError> make_update(RouteChange change) {
  if (change.pair >= output_pairs) { return std::unexpected(DspError::message); }
  if (change.selection >= route_selections) { return std::unexpected(DspError::data); }
  return byte_value({0, 3, 0, change.pair}, change.selection);
}

std::expected<Message, DspError> make_update(const DspChange &change) {
  return std::visit([](const auto &value) { return make_update(value); }, change);
}

}

std::expected<std::vector<std::uint8_t>, DspError> encode_dsp_change(const DspChange &change) {
  const auto message = make_update(change);
  if (!message) { return std::unexpected(message.error()); }
  auto frame = encode(message->command, message->address, message->payload);
  if (!frame) { return std::unexpected(DspError::data); }
  return std::move(*frame);
}

std::expected<bool, DspError> matches_dsp_change(const DspState &state, const DspChange &change) {
  const auto message = make_update(change);
  if (!message) { return std::unexpected(message.error()); }
  auto updated = state;
  const auto changed = apply_dsp_update(updated, *message);
  if (!changed) { return std::unexpected(changed.error()); }
  return !*changed;
}

}
