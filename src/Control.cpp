// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <utility>
#include <vector>

namespace octa {

std::expected<std::vector<std::uint8_t>, PreampError> encode_preamp_change(PreampChange change) {
  constexpr std::array<std::uint8_t, 13> maxima = {1, 1, 1, 1, 100, 1, 1, 50, 25, 45, 50, 8, 74};
  if (change.channel >= PreampBank{}.channels.size()) {
    return std::unexpected(PreampError::channel);
  }
  const auto parameter = static_cast<std::size_t>(change.parameter);
  if (parameter >= maxima.size()) { return std::unexpected(PreampError::parameter); }
  if (change.value > maxima[parameter]) { return std::unexpected(PreampError::data); }
  const std::array value = {change.value};
  auto frame =
      encode(Command::data, {0, 5, change.channel, static_cast<std::uint8_t>(parameter)}, value);
  if (!frame) { return std::unexpected(PreampError::data); }
  return std::move(*frame);
}

std::expected<std::uint8_t, PreampError>
preamp_value(const PreampBank &bank, std::uint8_t channel, PreampParameter parameter) {
  if (channel >= bank.channels.size()) { return std::unexpected(PreampError::channel); }
  const auto &preamp = bank.channels[channel];
  const auto &compressor = preamp.compressor;
  switch (parameter) {
    case PreampParameter::phantom: return preamp.phantom;
    case PreampParameter::low_cut: return preamp.low_cut;
    case PreampParameter::phase: return preamp.phase_inverted;
    case PreampParameter::hi_z: return preamp.hi_z;
    case PreampParameter::sensitivity: return preamp.sensitivity_index;
    case PreampParameter::stereo_link: return bank.stereo_links[channel / 2];
    case PreampParameter::compressor: return compressor.enabled;
    case PreampParameter::gate: return compressor.gate_index;
    case PreampParameter::attack: return compressor.attack_index;
    case PreampParameter::release: return compressor.release_index;
    case PreampParameter::threshold: return compressor.threshold_index;
    case PreampParameter::ratio: return compressor.ratio_index;
    case PreampParameter::gain: return compressor.gain_index;
    default: return std::unexpected(PreampError::parameter);
  }
}

}
