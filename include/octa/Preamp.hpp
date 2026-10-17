// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <array>
#include <cstdint>
#include <expected>
#include <vector>

namespace octa {

struct CompressorState {
  bool enabled;
  std::uint8_t gate_index;
  std::uint8_t attack_index;
  std::uint8_t release_index;
  std::uint8_t threshold_index;
  std::uint8_t ratio_index;
  std::uint8_t gain_index;
  bool operator==(const CompressorState &) const = default;
};

struct PreampState {
  bool phantom;
  bool phase_inverted;
  bool hi_z;
  bool low_cut;
  std::uint8_t sensitivity_index;
  CompressorState compressor;
  bool operator==(const PreampState &) const = default;
};

struct PreampBank {
  std::array<PreampState, 8> channels;
  std::array<bool, 4> stereo_links;
  bool operator==(const PreampBank &) const = default;
};

enum class PreampError : std::uint8_t { message, size, data, channel, parameter };

enum class PreampParameter : std::uint8_t {
  phantom,
  low_cut,
  phase,
  hi_z,
  sensitivity,
  stereo_link,
  compressor,
  gate,
  attack,
  release,
  threshold,
  ratio,
  gain
};

struct PreampChange {
  std::uint8_t channel;
  PreampParameter parameter;
  std::uint8_t value;
};

// Writes use reference GUI bounds, unlike incoming raw state indices.
[[nodiscard]] std::expected<std::vector<std::uint8_t>, PreampError>
encode_preamp_change(PreampChange change);
[[nodiscard]] std::expected<std::uint8_t, PreampError>
preamp_value(const PreampBank &bank, std::uint8_t channel, PreampParameter parameter);

// Retains wire indices; display units and parameter write validation are separate.
[[nodiscard]] std::expected<PreampBank, PreampError> decode_preamps(const Message &message);
[[nodiscard]] std::expected<bool, PreampError> apply_preamp_update(PreampBank &bank,
                                                                   const Message &message);

}
