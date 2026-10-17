// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Dsp.hpp"

#include <cstdint>
#include <expected>
#include <variant>
#include <vector>

namespace octa {

enum class MixerSource : std::uint8_t { input, playback };
enum class MixerParameter : std::uint8_t {
  stereo_link = 0,
  mono = 1,
  solo = 2,
  mute = 3,
  pan = 4,
  volume = 8,
  reverb_send = 14
};

struct MixerChange {
  MixerSource source;
  std::uint8_t bus;
  // Stereo link and mono use the first channel of the pair (0, 2, 4, 6, 8).
  std::uint8_t channel;
  MixerParameter parameter;
  std::uint32_t value;
};

enum class MasterParameter : std::uint8_t {
  stereo_link = 0,
  volume = 1,
  reverb_return = 7,
  input_output_link = 13
};

struct MasterChange {
  MixerSource source;
  std::uint8_t bus;
  // Left=0, right=1 for volume; other controls require 0.
  std::uint8_t channel;
  MasterParameter parameter;
  std::uint32_t value;
};

enum class ReverbParameter : std::uint8_t { type, pre_delay, time };

struct ReverbChange {
  ReverbParameter parameter;
  // Type changes require bank 0. Pre-delay/time require an active type (1..4).
  std::uint8_t bank;
  std::uint8_t value;
};

struct RouteChange {
  std::uint8_t pair;
  std::uint8_t selection;
};

using DspChange = std::variant<MixerChange, MasterChange, ReverbChange, RouteChange>;

// Writes enforce reference GUI bounds; coefficients and table indices are wire values.
[[nodiscard]] std::expected<std::vector<std::uint8_t>, DspError>
encode_dsp_change(const DspChange &change);
[[nodiscard]] std::expected<bool, DspError> matches_dsp_change(const DspState &state,
                                                               const DspChange &change);

}
