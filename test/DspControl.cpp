// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DspControl.hpp"

#include "octa/Dsp.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool golden(const octa::DspChange &change, std::span<const std::uint8_t> reference) {
  const auto encoded = octa::encode_dsp_change(change);
  if (!require(encoded && std::ranges::equal(*encoded, reference), "exact reference wire frame")) {
    return false;
  }
  const auto message = octa::decode(*encoded);
  if (!message) { return false; }
  octa::DspState state{};
  const auto before = octa::matches_dsp_change(state, change);
  const auto applied = octa::apply_dsp_update(state, *message);
  const auto after = octa::matches_dsp_change(state, change);
  return require(before && !*before && applied && *applied && after && *after,
                 "typed write and independent notification decoder agree on readback");
}

bool reference_frames() {
  constexpr std::uint32_t unity = 0x200000;
  constexpr std::uint32_t right = 0x8000;
  constexpr octa::MixerChange pan = {.source = octa::MixerSource::input,
                                     .bus = 3,
                                     .channel = 9,
                                     .parameter = octa::MixerParameter::pan,
                                     .value = right};
  constexpr std::array<std::uint8_t, 17> pan_frame = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 6, 0x39, 4, 8, 0, 0, 0, 0x35, 0xf7};
  constexpr octa::MixerChange volume = {.source = octa::MixerSource::playback,
                                        .bus = 2,
                                        .channel = 8,
                                        .parameter = octa::MixerParameter::volume,
                                        .value = unity};
  constexpr std::array<std::uint8_t, 19> volume_frame = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 7, 0x28, 8, 2, 0, 0, 0, 0, 0, 0x47, 0xf7};
  constexpr octa::MasterChange master = {.source = octa::MixerSource::playback,
                                         .bus = 3,
                                         .channel = 1,
                                         .parameter = octa::MasterParameter::volume,
                                         .value = unity};
  constexpr std::array<std::uint8_t, 19> master_frame = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 9, 0x31, 1, 2, 0, 0, 0, 0, 0, 0x43, 0xf7};
  constexpr octa::ReverbChange reverb = {
      .parameter = octa::ReverbParameter::type, .bank = 0, .value = 3};
  constexpr std::array<std::uint8_t, 14> reverb_frame = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 4, 0, 0, 3, 0x79, 0xf7};
  constexpr octa::RouteChange route = {.pair = 4, .selection = 8};
  constexpr std::array<std::uint8_t, 14> route_frame = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 3, 0, 4, 8, 0x71, 0xf7};
  return golden(pan, pan_frame) && golden(volume, volume_frame) && golden(master, master_frame) &&
         golden(reverb, reverb_frame) && golden(route, route_frame);
}

bool accepts(const octa::DspChange &change, bool valid) {
  const auto frame = octa::encode_dsp_change(change);
  const auto matches = octa::matches_dsp_change(octa::DspState{}, change);
  return require(frame.has_value() == valid && matches.has_value() == valid,
                 "encoding and readback reject invalid typed controls before hardware access");
}

bool mixer_bounds() {
  struct Range {
    octa::MixerParameter parameter;
    std::uint32_t maximum;
  };

  constexpr std::array<Range, 7> ranges = {
      {{.parameter = octa::MixerParameter::stereo_link, .maximum = 1},
       {.parameter = octa::MixerParameter::mono, .maximum = 1},
       {.parameter = octa::MixerParameter::solo, .maximum = 1},
       {.parameter = octa::MixerParameter::mute, .maximum = 1},
       {.parameter = octa::MixerParameter::pan, .maximum = 0x8000},
       {.parameter = octa::MixerParameter::volume, .maximum = 0x7fffff},
       {.parameter = octa::MixerParameter::reverb_send, .maximum = 0x7fffff}}};
  for (const auto &range : ranges) {
    octa::MixerChange change = {.source = octa::MixerSource::input,
                                .bus = 0,
                                .channel = 8,
                                .parameter = range.parameter,
                                .value = 0};
    if (!accepts(change, true)) { return false; }
    change.value = range.maximum;
    if (!accepts(change, true)) { return false; }
    change.value = range.maximum + 1;
    if (!accepts(change, false)) { return false; }
  }
  octa::MixerChange change = {.source = octa::MixerSource::input,
                              .bus = 3,
                              .channel = 9,
                              .parameter = octa::MixerParameter::stereo_link,
                              .value = 1};
  if (!accepts(change, false)) { return false; }
  change.parameter = octa::MixerParameter::volume;
  if (!accepts(change, true)) { return false; }
  change.bus = 4;
  if (!accepts(change, false)) { return false; }
  change.bus = 0;
  change.channel = 10;
  if (!accepts(change, false)) { return false; }
  change.channel = 0;
  change.source = std::bit_cast<octa::MixerSource>(std::uint8_t{2});
  if (!accepts(change, false)) { return false; }
  change.source = octa::MixerSource::playback;
  change.parameter = octa::MixerParameter::reverb_send;
  if (!accepts(change, false)) { return false; }
  change.source = octa::MixerSource::input;
  change.bus = 1;
  if (!accepts(change, false)) { return false; }
  change.bus = 0;
  change.parameter = std::bit_cast<octa::MixerParameter>(std::uint8_t{5});
  return accepts(change, false);
}

bool master_bounds() {
  constexpr std::uint32_t maximum = 0x7fffff;
  octa::MasterChange change = {.source = octa::MixerSource::input,
                               .bus = 3,
                               .channel = 1,
                               .parameter = octa::MasterParameter::volume,
                               .value = maximum};
  if (!accepts(change, true)) { return false; }
  change.value = maximum + 1;
  if (!accepts(change, false)) { return false; }
  change.value = 1;
  change.parameter = octa::MasterParameter::stereo_link;
  if (!accepts(change, false)) { return false; }
  change.channel = 0;
  if (!accepts(change, true)) { return false; }
  change.value = 2;
  if (!accepts(change, false)) { return false; }
  change.value = 1;
  change.parameter = octa::MasterParameter::reverb_return;
  if (!accepts(change, false)) { return false; }
  change.bus = 0;
  if (!accepts(change, true)) { return false; }
  change.source = octa::MixerSource::playback;
  if (!accepts(change, false)) { return false; }
  change.parameter = octa::MasterParameter::input_output_link;
  if (!accepts(change, false)) { return false; }
  change.source = octa::MixerSource::input;
  if (!accepts(change, true)) { return false; }
  change.channel = 2;
  return accepts(change, false);
}

bool effect_bounds() {
  constexpr std::uint8_t maximum_time = 49;
  constexpr std::uint8_t excessive_time = 50;
  const std::array<octa::DspChange, 7> valid = {
      {octa::ReverbChange{.parameter = octa::ReverbParameter::type, .bank = 0, .value = 0},
       octa::ReverbChange{.parameter = octa::ReverbParameter::type, .bank = 0, .value = 4},
       octa::ReverbChange{.parameter = octa::ReverbParameter::pre_delay, .bank = 1, .value = 12},
       octa::ReverbChange{
           .parameter = octa::ReverbParameter::time, .bank = 4, .value = maximum_time},
       octa::RouteChange{.pair = 0, .selection = 0},
       octa::RouteChange{.pair = 4, .selection = 8},
       octa::ReverbChange{.parameter = octa::ReverbParameter::time, .bank = 1, .value = 0}}};
  for (const auto &change : valid) {
    if (!accepts(change, true)) { return false; }
  }
  const std::array<octa::DspChange, 9> invalid = {
      {octa::ReverbChange{.parameter = octa::ReverbParameter::type, .bank = 0, .value = 5},
       octa::ReverbChange{.parameter = octa::ReverbParameter::type, .bank = 1, .value = 0},
       octa::ReverbChange{.parameter = octa::ReverbParameter::pre_delay, .bank = 0, .value = 1},
       octa::ReverbChange{.parameter = octa::ReverbParameter::pre_delay, .bank = 1, .value = 13},
       octa::ReverbChange{
           .parameter = octa::ReverbParameter::time, .bank = 4, .value = excessive_time},
       octa::ReverbChange{.parameter = octa::ReverbParameter::time, .bank = 5, .value = 0},
       octa::RouteChange{.pair = 5, .selection = 0},
       octa::RouteChange{.pair = 0, .selection = 9},
       octa::ReverbChange{.parameter = std::bit_cast<octa::ReverbParameter>(std::uint8_t{3}),
                          .bank = 1,
                          .value = 0}}};
  return std::ranges::all_of(invalid, [](const auto &change) { return accepts(change, false); });
}

}

int main() {
  return reference_frames() && mixer_bounds() && master_bounds() && effect_bounds() ? 0 : 1;
}
