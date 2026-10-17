// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>

namespace octa {
namespace {

using Payload = std::span<const std::uint8_t>;
constexpr std::size_t link_offset = 0x58;
constexpr std::size_t mono_offset = 0x60;
constexpr std::size_t solo_offset = 0x68;
constexpr std::size_t mute_offset = 0x74;
constexpr std::size_t pan_offset = 0x80;
constexpr std::size_t volume_offset = 0x120;
constexpr std::size_t send_offset = 0x210;
constexpr std::size_t playback_offset = 0x1f4;
constexpr std::size_t input_master_offset = 0x404;
constexpr std::size_t playback_master_offset = 0x446;
constexpr std::size_t return_offset = 0x438;
constexpr std::size_t input_output_link_offset = 0x43e;
constexpr std::size_t reverb_offset = 0x0a;
constexpr std::size_t routing_offset = 5;
constexpr std::size_t pan_digits = 4;
constexpr std::size_t volume_digits = 6;
constexpr std::size_t pair_flag_bytes = 2;
constexpr std::size_t channel_flag_bytes = 3;
constexpr std::size_t master_channels = 2;
constexpr unsigned int nibble_bits = 4;
constexpr std::uint8_t nibble_limit = 16;
constexpr std::uint8_t midi_limit = 128;

bool valid_numbers(Payload payload) {
  struct Region {
    std::size_t begin;
    std::size_t end;
  };

  constexpr std::array<Region, 4> regions = {{{.begin = 0x80, .end = 0x24c},
                                              {.begin = 0x274, .end = 0x404},
                                              {.begin = 0x408, .end = 0x43e},
                                              {.begin = 0x44a, .end = 0x47a}}};
  return std::ranges::all_of(regions, [payload](Region region) {
    return std::ranges::all_of(payload.subspan(region.begin, region.end - region.begin),
                               [](std::uint8_t byte) { return byte < nibble_limit; });
  });
}

std::uint32_t number(Payload payload, std::size_t offset, std::size_t digits) {
  std::uint32_t result = 0;
  for (const auto digit : payload.subspan(offset, digits)) {
    result = (result << nibble_bits) | digit;
  }
  return result;
}

bool flag(Payload payload, std::size_t offset, std::size_t index) {
  constexpr std::size_t flags_per_byte = 4;
  return (payload[offset + (index / flags_per_byte)] & (1U << (index % flags_per_byte))) != 0;
}

MixerGroup group(Payload payload, std::size_t bus, std::size_t base) {
  MixerGroup result{};
  for (std::size_t channel = 0; channel < result.channels.size(); ++channel) {
    result.channels[channel] = {
        .mute = flag(payload, base + mute_offset + (bus * channel_flag_bytes), channel),
        .solo = flag(payload, base + solo_offset + (bus * channel_flag_bytes), channel),
        .pan = static_cast<std::uint16_t>(
            number(payload,
                   base + pan_offset + ((bus * result.channels.size() + channel) * pan_digits),
                   pan_digits)),
        .volume = number(payload,
                         base + volume_offset +
                             ((bus * result.channels.size() + channel) * volume_digits),
                         volume_digits)};
  }
  for (std::size_t pair = 0; pair < result.pairs.size(); ++pair) {
    result.pairs[pair] = {
        .linked = flag(payload, base + link_offset + (bus * pair_flag_bytes), pair),
        .mono = flag(payload, base + mono_offset + (bus * pair_flag_bytes), pair)};
  }
  return result;
}

MixerMaster master(Payload payload, std::size_t bus, std::size_t base) {
  MixerMaster result{};
  result.linked = (payload[base + bus] & 1U) != 0;
  constexpr std::size_t bus_count = 4;
  for (std::size_t channel = 0; channel < result.volume.size(); ++channel) {
    result.volume[channel] =
        number(payload,
               base + bus_count + ((bus * master_channels + channel) * volume_digits),
               volume_digits);
  }
  return result;
}

}

std::expected<DspState, DspError> decode_dsp(const Message &message) {
  if (message.command != Command::data || message.address != bulk_address) {
    return std::unexpected(DspError::message);
  }
  if (message.payload.size() != bulk_size) { return std::unexpected(DspError::size); }
  const Payload payload(message.payload);
  if (!std::ranges::all_of(payload, [](std::uint8_t byte) { return byte < midi_limit; }) ||
      !valid_numbers(payload)) {
    return std::unexpected(DspError::data);
  }
  DspState state{};
  for (std::size_t bus = 0; bus < state.mixers.size(); ++bus) {
    state.mixers[bus] = {.input = group(payload, bus, 0),
                         .playback = group(payload, bus, playback_offset),
                         .input_master = master(payload, bus, input_master_offset),
                         .playback_master = master(payload, bus, playback_master_offset),
                         .input_output_link = (payload[input_output_link_offset + bus] & 1U) != 0};
  }
  state.reverb.type = payload[reverb_offset];
  for (std::size_t type = 0; type < state.reverb.pre_delay_indices.size(); ++type) {
    state.reverb.pre_delay_indices[type] = payload[reverb_offset + 1 + type];
    state.reverb.time_indices[type] =
        payload[reverb_offset + 1 + state.reverb.pre_delay_indices.size() + type];
  }
  for (std::size_t channel = 0; channel < state.reverb.input_send.size(); ++channel) {
    state.reverb.input_send[channel] =
        number(payload, send_offset + (channel * volume_digits), volume_digits);
  }
  state.reverb.master_return = number(payload, return_offset, volume_digits);
  for (std::size_t pair = 0; pair < state.output_routes.size(); ++pair) {
    state.output_routes[pair] = payload[routing_offset + pair];
  }
  return state;
}

}
