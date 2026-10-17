// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <vector>

namespace octa {

// Disk fields are DWORDs, including values narrower on the device. Keep them
// intact here; validate device bounds separately before applying a preset.
struct PresetCompressor {
  bool enabled;
  // Gate, attack, release, threshold, ratio, gain, in file order.
  std::array<std::uint32_t, 6> indices;
  bool operator==(const PresetCompressor &) const = default;
};

struct PresetPreamp {
  PresetCompressor compressor;
  bool phantom;
  bool phase_inverted;
  bool hi_z;
  bool low_cut;
  std::uint32_t sensitivity_index;
  bool operator==(const PresetPreamp &) const = default;
};

struct PresetPreampPair {
  bool linked;
  std::array<PresetPreamp, 2> channels;
  bool operator==(const PresetPreampPair &) const = default;
};

struct PresetMixerChannel {
  bool mute;
  bool solo;
  std::uint32_t reverb_send;
  std::uint32_t pan;
  std::uint32_t volume;
  bool operator==(const PresetMixerChannel &) const = default;
};

struct PresetMixerPair {
  bool linked;
  std::array<PresetMixerChannel, 2> channels;
  bool operator==(const PresetMixerPair &) const = default;
};

struct PresetMaster {
  bool linked;
  std::uint32_t reverb_return;
  std::array<std::uint32_t, 2> volume;
  bool operator==(const PresetMaster &) const = default;
};

struct PresetMixer {
  bool input_output_link;
  std::array<PresetMixerPair, 5> input;
  std::array<PresetMixerPair, 5> playback;
  PresetMaster input_master;
  PresetMaster playback_master;
  bool operator==(const PresetMixer &) const = default;
};

struct PresetReverb {
  std::uint32_t type;
  std::array<std::uint32_t, 5> pre_delay_indices;
  std::array<std::uint32_t, 5> time_indices;
  bool operator==(const PresetReverb &) const = default;
};

struct Preset {
  std::array<std::uint32_t, 5> output_routes;
  PresetReverb reverb;
  std::array<PresetPreampPair, 4> preamps;
  std::array<PresetMixer, 4> mixers;
  bool operator==(const Preset &) const = default;
};

enum class PresetError : std::uint8_t { size, header, version };
inline constexpr std::size_t preset_size = 1639;

// Ver100 only, exact length. Nonzero flag bytes decode as true, matching the
// reference loader. Encoding emits canonical 0/1 flag bytes. No device I/O.
[[nodiscard]] std::expected<Preset, PresetError> decode_preset(std::span<const std::uint8_t> bytes);
[[nodiscard]] std::vector<std::uint8_t> encode_preset(const Preset &preset);

}
