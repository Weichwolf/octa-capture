// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <array>
#include <cstdint>
#include <expected>

namespace octa {

struct MixerChannel {
  bool mute;
  bool solo;
  std::uint16_t pan;
  std::uint32_t volume;
  bool operator==(const MixerChannel &) const = default;
};

struct MixerPair {
  bool linked;
  bool mono;
  bool operator==(const MixerPair &) const = default;
};

struct MixerGroup {
  std::array<MixerChannel, 10> channels;
  std::array<MixerPair, 5> pairs;
  bool operator==(const MixerGroup &) const = default;
};

struct MixerMaster {
  bool linked;
  std::array<std::uint32_t, 2> volume;
  bool operator==(const MixerMaster &) const = default;
};

struct DirectMixer {
  MixerGroup input;
  MixerGroup playback;
  MixerMaster input_master;
  MixerMaster playback_master;
  bool input_output_link;
  bool operator==(const DirectMixer &) const = default;
};

struct ReverbState {
  std::uint8_t type;
  // Parameter banks include the off slot, matching the reference bulk layout.
  std::array<std::uint8_t, 5> pre_delay_indices;
  std::array<std::uint8_t, 5> time_indices;
  std::array<std::uint32_t, 10> input_send;
  std::uint32_t master_return;
  bool operator==(const ReverbState &) const = default;
};

struct DspState {
  std::array<DirectMixer, 4> mixers;
  ReverbState reverb;
  std::array<std::uint8_t, 5> output_routes;
  bool operator==(const DspState &) const = default;
};

enum class DspError : std::uint8_t { message, size, data };

// Retains wire values. Numeric DSP coefficients use big-endian nibbles.
[[nodiscard]] std::expected<DspState, DspError> decode_dsp(const Message &message);

// Applies known individual DT1 updates atomically; malformed/unknown messages
// leave the complete state untouched. Returns whether the selected value changed.
[[nodiscard]] std::expected<bool, DspError> apply_dsp_update(DspState &state,
                                                             const Message &message);

}
