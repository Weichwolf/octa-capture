// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Preamp.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>

namespace octa {
namespace {

constexpr std::size_t phantom_offset = 0x15;
constexpr std::size_t low_cut_offset = 0x17;
constexpr std::size_t phase_offset = 0x19;
constexpr std::size_t hi_z_offset = 0x1b;
constexpr std::size_t sensitivity_offset = 0x1d;
constexpr std::size_t stereo_link_offset = 0x25;
constexpr std::size_t compressor_enable_offset = 0x26;
constexpr std::size_t gate_offset = 0x28;
constexpr std::size_t attack_offset = 0x30;
constexpr std::size_t release_offset = 0x38;
constexpr std::size_t threshold_offset = 0x40;
constexpr std::size_t ratio_offset = 0x48;
constexpr std::size_t gain_offset = 0x50;
constexpr std::uint8_t preamp_address = 5;
constexpr std::uint8_t midi_limit = 128;

bool packed_flag(std::span<const std::uint8_t> payload, std::size_t offset, std::size_t channel) {
  constexpr std::size_t flags_per_byte = 4;
  return (payload[offset + (channel / flags_per_byte)] & (1U << (channel % flags_per_byte))) != 0;
}

bool valid_data(const Message &message) {
  return message.command == Command::data &&
         std::ranges::all_of(message.payload, [](auto byte) { return byte < midi_limit; });
}

struct ParameterUpdate {
  std::uint8_t channel;
  std::uint8_t parameter;
  std::uint8_t value;
};

std::expected<void, PreampError> set_parameter(PreampBank &bank, ParameterUpdate update) {
  const auto channel = update.channel;
  const auto parameter = update.parameter;
  const auto value = update.value;
  auto &preamp = bank.channels[channel];
  auto &compressor = preamp.compressor;
  switch (parameter) {
    case 0: preamp.phantom = value != 0; break;
    case 1: preamp.low_cut = value != 0; break;
    case 2: preamp.phase_inverted = value != 0; break;
    case 3: preamp.hi_z = value != 0; break;
    case 4: preamp.sensitivity_index = value; break;
    case 5: bank.stereo_links[channel / 2] = value != 0; break;
    case 6: compressor.enabled = value != 0; break;
    case 7: compressor.gate_index = value; break;
    case 8: compressor.attack_index = value; break;
    case 9: compressor.release_index = value; break;
    case 10: compressor.threshold_index = value; break;
    case 11: compressor.ratio_index = value; break;
    case 12: compressor.gain_index = value; break;
    default: return std::unexpected(PreampError::parameter);
  }
  return {};
}

}

std::expected<PreampBank, PreampError> decode_preamps(const Message &message) {
  if (message.command != Command::data || message.address != bulk_address) {
    return std::unexpected(PreampError::message);
  }
  if (message.payload.size() != bulk_size) { return std::unexpected(PreampError::size); }
  if (!valid_data(message)) { return std::unexpected(PreampError::data); }
  const std::span payload(message.payload);
  PreampBank bank{};
  for (std::size_t channel = 0; channel < bank.channels.size(); ++channel) {
    bank.channels[channel] = {
        .phantom = packed_flag(payload, phantom_offset, channel),
        .phase_inverted = packed_flag(payload, phase_offset, channel),
        .hi_z = packed_flag(payload, hi_z_offset, channel),
        .low_cut = packed_flag(payload, low_cut_offset, channel),
        .sensitivity_index = payload[sensitivity_offset + channel],
        .compressor = {.enabled = packed_flag(payload, compressor_enable_offset, channel),
                       .gate_index = payload[gate_offset + channel],
                       .attack_index = payload[attack_offset + channel],
                       .release_index = payload[release_offset + channel],
                       .threshold_index = payload[threshold_offset + channel],
                       .ratio_index = payload[ratio_offset + channel],
                       .gain_index = payload[gain_offset + channel]}};
  }
  for (std::size_t pair = 0; pair < bank.stereo_links.size(); ++pair) {
    bank.stereo_links[pair] = (payload[stereo_link_offset] & (1U << pair)) != 0;
  }
  return bank;
}

std::expected<bool, PreampError> apply_preamp_update(PreampBank &bank, const Message &message) {
  if (message.command != Command::data || message.address[0] != 0 ||
      message.address[1] != preamp_address) {
    return std::unexpected(PreampError::message);
  }
  if (message.payload.size() != 1) { return std::unexpected(PreampError::size); }
  if (!valid_data(message)) { return std::unexpected(PreampError::data); }
  const auto channel = message.address[2];
  if (channel >= bank.channels.size()) { return std::unexpected(PreampError::channel); }
  const auto previous = bank;
  const auto updated = set_parameter(
      bank,
      {.channel = channel, .parameter = message.address[3], .value = message.payload.front()});
  if (!updated) { return std::unexpected(updated.error()); }
  return previous != bank;
}

}
