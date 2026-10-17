// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DspControl.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <vector>

namespace octa {
namespace {

using Payload = std::span<std::uint8_t>;
constexpr std::uint32_t maximum_midi = 127;

void flag(Payload payload, std::size_t offset, std::size_t index, bool enabled) {
  if (enabled) { payload[offset + (index / 4)] |= static_cast<std::uint8_t>(1U << (index % 4)); }
}

template <std::size_t digits>
void number(Payload payload, std::size_t offset, std::uint32_t value) {
  constexpr unsigned int nibble_bits = 4;
  constexpr std::uint32_t nibble_mask = 15;
  for (std::size_t digit = digits; digit > 0; --digit) {
    payload[offset + digit - 1] = static_cast<std::uint8_t>(value & nibble_mask);
    value >>= nibble_bits;
  }
}

bool preamp(Payload payload, std::uint8_t channel, const PresetPreamp &source) {
  constexpr std::array<PreampParameter, 7> parameters = {PreampParameter::sensitivity,
                                                         PreampParameter::gate,
                                                         PreampParameter::attack,
                                                         PreampParameter::release,
                                                         PreampParameter::threshold,
                                                         PreampParameter::ratio,
                                                         PreampParameter::gain};
  constexpr std::array<std::size_t, 7> offsets = {0x1d, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50};
  std::array<std::uint32_t, 7> values{};
  values.front() = source.sensitivity_index;
  for (std::size_t index = 0; index < source.compressor.indices.size(); ++index) {
    values[index + 1] = source.compressor.indices[index];
  }
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (values[index] > maximum_midi) { return false; }
    const auto value = static_cast<std::uint8_t>(values[index]);
    if (!encode_preamp_change(
            {.channel = channel, .parameter = parameters[index], .value = value})) {
      return false;
    }
    payload[offsets[index] + channel] = value;
  }
  constexpr std::size_t phantom = 0x15;
  constexpr std::size_t low_cut = 0x17;
  constexpr std::size_t phase = 0x19;
  constexpr std::size_t hi_z = 0x1b;
  constexpr std::size_t compressor = 0x26;
  flag(payload, phantom, channel, source.phantom);
  flag(payload, low_cut, channel, source.low_cut);
  flag(payload, phase, channel, source.phase_inverted);
  flag(payload, hi_z, channel, source.hi_z);
  flag(payload, compressor, channel, source.compressor.enabled);
  return true;
}

bool reverb(Payload payload, const PresetReverb &source) {
  if (source.type > maximum_midi ||
      !encode_dsp_change(ReverbChange{.parameter = ReverbParameter::type,
                                      .bank = 0,
                                      .value = static_cast<std::uint8_t>(source.type)})) {
    return false;
  }
  constexpr std::size_t type_offset = 0x0a;
  payload[type_offset] = static_cast<std::uint8_t>(source.type);
  for (std::size_t bank = 0; bank < source.pre_delay_indices.size(); ++bank) {
    const auto delay = source.pre_delay_indices[bank];
    const auto time = source.time_indices[bank];
    if (delay > maximum_midi || time > maximum_midi) { return false; }
    if (bank != 0 && (!encode_dsp_change(ReverbChange{.parameter = ReverbParameter::pre_delay,
                                                      .bank = static_cast<std::uint8_t>(bank),
                                                      .value = static_cast<std::uint8_t>(delay)}) ||
                      !encode_dsp_change(ReverbChange{.parameter = ReverbParameter::time,
                                                      .bank = static_cast<std::uint8_t>(bank),
                                                      .value = static_cast<std::uint8_t>(time)}))) {
      return false;
    }
    payload[type_offset + 1 + bank] = static_cast<std::uint8_t>(delay);
    payload[type_offset + 1 + source.pre_delay_indices.size() + bank] =
        static_cast<std::uint8_t>(time);
  }
  return true;
}

bool group(Payload payload,
           const std::array<PresetMixerPair, 5> &source,
           MixerSource kind,
           std::uint8_t bus) {
  constexpr std::size_t playback_offset = 0x1f4;
  const std::size_t base = kind == MixerSource::input ? 0 : playback_offset;
  const std::size_t bus_index = bus;
  constexpr std::size_t link_offset = 0x58;
  constexpr std::size_t solo_offset = 0x68;
  constexpr std::size_t mute_offset = 0x74;
  constexpr std::size_t pan_offset = 0x80;
  constexpr std::size_t volume_offset = 0x120;
  constexpr std::size_t send_offset = 0x210;
  for (std::size_t pair = 0; pair < source.size(); ++pair) {
    flag(payload, base + link_offset + (bus_index * 2), pair, source[pair].linked);
    for (std::size_t side = 0; side < source[pair].channels.size(); ++side) {
      const std::size_t channel_index = (pair * 2) + side;
      const auto channel = static_cast<std::uint8_t>(channel_index);
      const auto &input = source[pair].channels[side];
      if (!encode_dsp_change(MixerChange{.source = kind,
                                         .bus = bus,
                                         .channel = channel,
                                         .parameter = MixerParameter::pan,
                                         .value = input.pan}) ||
          !encode_dsp_change(MixerChange{.source = kind,
                                         .bus = bus,
                                         .channel = channel,
                                         .parameter = MixerParameter::volume,
                                         .value = input.volume})) {
        return false;
      }
      flag(payload, base + mute_offset + (bus_index * 3), channel, input.mute);
      flag(payload, base + solo_offset + (bus_index * 3), channel, input.solo);
      number<4>(payload, base + pan_offset + (((bus_index * 10) + channel_index) * 4), input.pan);
      number<6>(
          payload, base + volume_offset + (((bus_index * 10) + channel_index) * 6), input.volume);
      if (kind == MixerSource::input && bus == 0) {
        if (!encode_dsp_change(MixerChange{.source = kind,
                                           .bus = bus,
                                           .channel = channel,
                                           .parameter = MixerParameter::reverb_send,
                                           .value = input.reverb_send})) {
          return false;
        }
        number<6>(payload, send_offset + (channel_index * 6), input.reverb_send);
      }
    }
  }
  return true;
}

bool master(Payload payload, const PresetMaster &source, MixerSource kind, std::uint8_t bus) {
  constexpr std::size_t input_offset = 0x404;
  constexpr std::size_t playback_offset = 0x446;
  constexpr std::size_t return_offset = 0x438;
  const std::size_t base = kind == MixerSource::input ? input_offset : playback_offset;
  const std::size_t bus_index = bus;
  payload[base + bus] = static_cast<std::uint8_t>(source.linked);
  for (std::size_t side = 0; side < source.volume.size(); ++side) {
    if (!encode_dsp_change(MasterChange{.source = kind,
                                        .bus = bus,
                                        .channel = static_cast<std::uint8_t>(side),
                                        .parameter = MasterParameter::volume,
                                        .value = source.volume[side]})) {
      return false;
    }
    number<6>(payload, base + 4 + (((bus_index * 2) + side) * 6), source.volume[side]);
  }
  if (kind == MixerSource::input && bus == 0) {
    if (!encode_dsp_change(MasterChange{.source = kind,
                                        .bus = bus,
                                        .channel = 0,
                                        .parameter = MasterParameter::reverb_return,
                                        .value = source.reverb_return})) {
      return false;
    }
    number<6>(payload, return_offset, source.reverb_return);
  }
  return true;
}

bool populate(Payload payload, const Preset &source) {
  constexpr std::size_t routing_offset = 5;
  for (std::size_t pair = 0; pair < source.output_routes.size(); ++pair) {
    const auto route = source.output_routes[pair];
    if (route > maximum_midi ||
        !encode_dsp_change(RouteChange{.pair = static_cast<std::uint8_t>(pair),
                                       .selection = static_cast<std::uint8_t>(route)})) {
      return false;
    }
    payload[routing_offset + pair] = static_cast<std::uint8_t>(route);
  }
  if (!reverb(payload, source.reverb)) { return false; }
  constexpr std::size_t preamp_link_offset = 0x25;
  for (std::size_t pair = 0; pair < source.preamps.size(); ++pair) {
    flag(payload, preamp_link_offset, pair, source.preamps[pair].linked);
    for (std::size_t side = 0; side < source.preamps[pair].channels.size(); ++side) {
      if (!preamp(payload,
                  static_cast<std::uint8_t>((pair * 2) + side),
                  source.preamps[pair].channels[side])) {
        return false;
      }
    }
  }
  constexpr std::size_t input_output_link_offset = 0x43e;
  for (std::size_t bus = 0; bus < source.mixers.size(); ++bus) {
    const auto &mixer = source.mixers[bus];
    const auto index = static_cast<std::uint8_t>(bus);
    if (!group(payload, mixer.input, MixerSource::input, index) ||
        !group(payload, mixer.playback, MixerSource::playback, index) ||
        !master(payload, mixer.input_master, MixerSource::input, index) ||
        !master(payload, mixer.playback_master, MixerSource::playback, index)) {
      return false;
    }
    payload[input_output_link_offset + bus] = static_cast<std::uint8_t>(mixer.input_output_link);
  }
  return true;
}

}

std::expected<Message, PresetStateError> preset_to_bulk(const Preset &preset,
                                                        const Message &current) {
  const auto validated = preset_from_bulk(current);
  if (!validated) { return std::unexpected(validated.error()); }
  Message result = {.command = Command::data,
                    .address = bulk_address,
                    .payload = std::vector<std::uint8_t>(bulk_size)};
  // MakeExBulk retains cached device properties and canonicalizes its flag bits.
  result.payload[0] = static_cast<std::uint8_t>(current.payload[0] & 1U);
  result.payload[1] = current.payload[1];
  result.payload[2] = current.payload[2];
  result.payload[3] = static_cast<std::uint8_t>(current.payload[3] & 1U);
  result.payload[4] = current.payload[2] == 3 ? 0 : current.payload[4];
  if (!populate(result.payload, preset)) { return std::unexpected(PresetStateError::range); }
  return result;
}

}
