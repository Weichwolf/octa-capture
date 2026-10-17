// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Preset.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>
#include <vector>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

std::vector<std::uint8_t> fixture() {
  constexpr auto header = std::to_array("OCTA-CAPTURE Control Panel Data\0Ver100");
  std::vector<std::uint8_t> bytes(octa::preset_size);
  std::ranges::transform(
      header, bytes.begin(), [](char byte) { return static_cast<std::uint8_t>(byte); });
  return bytes;
}

std::vector<std::uint8_t> distinctive_fixture() {
  auto bytes = fixture();
  // Independent file offsets derived from the reference layout, not our codec.
  constexpr std::array<std::size_t, 20> flags = {103, 104, 129, 130,  131,  132, 304,
                                                 338, 371, 372, 373,  374,  517, 518,
                                                 519, 662, 675, 1322, 1613, 1626};
  for (const auto offset : flags) { bytes[offset] = 1; }
  constexpr std::array<std::size_t, 22> words = {39,  55,  59,  63,  95,  99,  105, 125,
                                                 133, 367, 375, 379, 383, 513, 520, 524,
                                                 528, 663, 671, 676, 684, 1635};
  constexpr std::array<std::uint8_t, 4> little_endian = {0x12, 0x34, 0x56, 0x78};
  for (const auto offset : words) {
    std::ranges::copy(little_endian, std::span(bytes).subspan(offset).begin());
  }
  return bytes;
}

bool reference_layout() {
  const auto bytes = distinctive_fixture();
  const auto decoded = octa::decode_preset(bytes);
  if (!require(decoded.has_value(), "reference-shaped Ver100 file decodes")) { return false; }
  constexpr std::uint32_t value = 0x78563412;
  const auto &preamp = decoded->preamps.front().channels.front();
  const auto &mixer = decoded->mixers.front();
  return require(decoded->output_routes.front() == value &&
                     decoded->output_routes.back() == value && decoded->reverb.type == value &&
                     decoded->reverb.pre_delay_indices.front() == value &&
                     decoded->reverb.pre_delay_indices.back() == value &&
                     decoded->reverb.time_indices.back() == value,
                 "routing and interleaved reverb banks use 32-bit little-endian fields") &&
         require(decoded->preamps.front().linked && preamp.compressor.enabled && preamp.phantom &&
                     preamp.phase_inverted && preamp.hi_z && preamp.low_cut &&
                     preamp.compressor.indices.front() == value &&
                     preamp.compressor.indices.back() == value &&
                     preamp.sensitivity_index == value &&
                     decoded->preamps.back().channels.back().sensitivity_index == value,
                 "paired preamp records include compressor then "
                 "phantom/phase/Hi-Z/low-cut/sensitivity") &&
         require(mixer.input_output_link && mixer.input.front().linked &&
                     mixer.input.front().channels.front().mute &&
                     mixer.input.front().channels.front().solo &&
                     mixer.input.front().channels.front().reverb_send == value &&
                     mixer.input.front().channels.front().pan == value &&
                     mixer.input.front().channels.front().volume == value &&
                     mixer.input.back().channels.back().volume == value &&
                     mixer.playback.front().channels.front().reverb_send == value &&
                     mixer.playback.front().channels.front().pan == value &&
                     mixer.playback.front().channels.front().volume == value &&
                     mixer.input_master.reverb_return == value &&
                     mixer.input_master.volume.back() == value &&
                     mixer.playback_master.reverb_return == value &&
                     mixer.playback_master.volume.back() == value &&
                     decoded->mixers.back().playback_master.volume.back() == value,
                 "all mixer groups, unused send fields and both masters retain file values") &&
         require(octa::encode_preset(*decoded) == bytes,
                 "canonical file round trip is byte-identical");
}

bool invalid_files() {
  auto bytes = fixture();
  for (std::size_t size = 0; size < bytes.size(); ++size) {
    const auto decoded = octa::decode_preset(std::span(bytes).first(size));
    if (!require(!decoded && decoded.error() == octa::PresetError::size,
                 "every truncated file rejected")) {
      return false;
    }
  }
  bytes.push_back(0);
  const auto trailing = octa::decode_preset(bytes);
  bytes.pop_back();
  bytes.front() ^= 1;
  const auto header = octa::decode_preset(bytes);
  bytes.front() ^= 1;
  constexpr std::size_t version_offset = 32;
  bytes[version_offset] ^= 1;
  const auto version = octa::decode_preset(bytes);
  return require(!trailing && trailing.error() == octa::PresetError::size && !header &&
                     header.error() == octa::PresetError::header && !version &&
                     version.error() == octa::PresetError::version,
                 "trailing data, wrong magic and unsupported version rejected distinctly");
}

bool canonical_flags() {
  auto bytes = fixture();
  constexpr std::size_t header_size = 39;
  constexpr std::uint8_t all_bits = 255;
  std::ranges::fill(std::span(bytes).subspan(header_size), all_bits);
  const auto decoded = octa::decode_preset(bytes);
  if (!decoded) { return false; }
  const auto canonical = octa::encode_preset(*decoded);
  const auto repeated = octa::decode_preset(canonical);
  constexpr std::ptrdiff_t flag_count = 256;
  return require(repeated && *repeated == *decoded &&
                     std::ranges::count(std::span(canonical).subspan(header_size),
                                        std::uint8_t{1}) == flag_count &&
                     octa::encode_preset(*repeated) == canonical,
                 "256 nonzero flags normalize to one while DWORDs remain full width");
}

}

int main() {
  return reference_layout() && invalid_files() && canonical_flags() ? 0 : 1;
}
