// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PresetState.hpp"

#include "octa/Dsp.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>

namespace octa {
namespace {

PresetPreamp preamp(const PreampState &source) {
  const auto &compressor = source.compressor;
  return {.compressor = {.enabled = compressor.enabled,
                         .indices = {compressor.gate_index,
                                     compressor.attack_index,
                                     compressor.release_index,
                                     compressor.threshold_index,
                                     compressor.ratio_index,
                                     compressor.gain_index}},
          .phantom = source.phantom,
          .phase_inverted = source.phase_inverted,
          .hi_z = source.hi_z,
          .low_cut = source.low_cut,
          .sensitivity_index = source.sensitivity_index};
}

std::array<PresetMixerPair, 5> group(const MixerGroup &source) {
  std::array<PresetMixerPair, 5> result{};
  for (std::size_t pair = 0; pair < result.size(); ++pair) {
    result[pair].linked = source.pairs[pair].linked;
    for (std::size_t channel = 0; channel < result[pair].channels.size(); ++channel) {
      const auto &input = source.channels[(pair * result[pair].channels.size()) + channel];
      result[pair].channels[channel] = {.mute = input.mute,
                                        .solo = input.solo,
                                        .reverb_send = 0,
                                        .pan = input.pan,
                                        .volume = input.volume};
    }
  }
  return result;
}

PresetMixer mixer(const DirectMixer &source) {
  constexpr std::uint32_t reference_return_default = 0x200000;
  return {.input_output_link = source.input_output_link,
          .input = group(source.input),
          .playback = group(source.playback),
          .input_master = {.linked = source.input_master.linked,
                           .reverb_return = reference_return_default,
                           .volume = source.input_master.volume},
          .playback_master = {.linked = source.playback_master.linked,
                              .reverb_return = 0,
                              .volume = source.playback_master.volume}};
}

}

std::expected<Preset, PresetStateError> preset_from_bulk(const Message &message) {
  if (message.command != Command::data || message.address != bulk_address) {
    return std::unexpected(PresetStateError::message);
  }
  const auto preamps = decode_preamps(message);
  const auto dsp = decode_dsp(message);
  if (!preamps || !dsp) { return std::unexpected(PresetStateError::data); }
  Preset result{};
  std::ranges::copy(dsp->output_routes, result.output_routes.begin());
  result.reverb.type = dsp->reverb.type;
  std::ranges::copy(dsp->reverb.pre_delay_indices, result.reverb.pre_delay_indices.begin());
  std::ranges::copy(dsp->reverb.time_indices, result.reverb.time_indices.begin());
  for (std::size_t pair = 0; pair < result.preamps.size(); ++pair) {
    result.preamps[pair].linked = preamps->stereo_links[pair];
    for (std::size_t channel = 0; channel < result.preamps[pair].channels.size(); ++channel) {
      result.preamps[pair].channels[channel] =
          preamp(preamps->channels[(pair * result.preamps[pair].channels.size()) + channel]);
    }
  }
  for (std::size_t bus = 0; bus < result.mixers.size(); ++bus) {
    result.mixers[bus] = mixer(dsp->mixers[bus]);
  }
  auto &first = result.mixers.front();
  for (std::size_t channel = 0; channel < dsp->reverb.input_send.size(); ++channel) {
    first.input[channel / 2].channels[channel % 2].reverb_send = dsp->reverb.input_send[channel];
  }
  first.input_master.reverb_return = dsp->reverb.master_return;
  return result;
}

}
