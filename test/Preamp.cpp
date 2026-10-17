// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Preamp.hpp"

#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <utility>
#include <vector>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

octa::Message fixture() {
  // Synthetic bit patterns exercise both groups of four channels independently.
  std::vector<std::uint8_t> payload(octa::bulk_size);
  constexpr std::array<std::pair<std::size_t, std::uint8_t>, 9> flags = {{{0x15, 5},
                                                                          {0x16, 10},
                                                                          {0x17, 15},
                                                                          {0x19, 10},
                                                                          {0x1a, 5},
                                                                          {0x1b, 3},
                                                                          {0x25, 5},
                                                                          {0x26, 15},
                                                                          {0x27, 10}}};
  for (const auto &[offset, value] : flags) { payload[offset] = value; }
  constexpr std::array<std::size_t, 7> offsets = {0x1d, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50};
  constexpr std::array<std::uint8_t, 7> bases = {64, 0, 8, 16, 24, 0, 48};
  for (std::size_t row = 0; row < offsets.size(); ++row) {
    for (std::uint8_t channel = 0; channel < 8; ++channel) {
      payload[offsets[row] + channel] = static_cast<std::uint8_t>(bases[row] + channel);
    }
  }
  return {.command = octa::Command::data, .address = octa::bulk_address, .payload = payload};
}

bool bulk_layout() {
  const auto state = octa::decode_preamps(fixture());
  if (!require(state.has_value(), "decode preamp bank")) { return false; }
  constexpr std::size_t threshold_base = 24;
  constexpr std::size_t gain_base = 48;
  constexpr std::array<bool, 8> phantom = {true, false, true, false, false, true, false, true};
  constexpr std::array<bool, 8> phase = {false, true, false, true, true, false, true, false};
  constexpr std::array<bool, 8> compressor = {true, true, true, true, false, true, false, true};
  for (std::size_t channel = 0; channel < state->channels.size(); ++channel) {
    const auto &preamp = state->channels[channel];
    const auto &comp = preamp.compressor;
    if (!require(preamp.phantom == phantom[channel] && preamp.phase_inverted == phase[channel] &&
                     preamp.hi_z == (channel < 2) && preamp.low_cut == (channel < 4) &&
                     preamp.sensitivity_index == 64 + channel &&
                     comp.enabled == compressor[channel] && comp.gate_index == channel &&
                     comp.attack_index == 8 + channel && comp.release_index == 16 + channel &&
                     comp.threshold_index == threshold_base + channel &&
                     comp.ratio_index == channel && comp.gain_index == gain_base + channel,
                 "channel flags and independent parameter columns")) {
      return false;
    }
  }
  return require(state->stereo_links == std::array{true, false, true, false}, "stereo pair flags");
}

bool notifications() {
  auto decoded = octa::decode_preamps(fixture());
  if (!decoded) { return false; }
  auto &bank = *decoded;
  const octa::Message low_cut = {
      .command = octa::Command::data, .address = {0, 5, 7, 1}, .payload = {1}};
  const auto changed = octa::apply_preamp_update(bank, low_cut);
  const auto repeated = octa::apply_preamp_update(bank, low_cut);
  if (!require(changed && *changed && repeated && !*repeated && bank.channels[7].low_cut,
               "low-cut notification and unchanged detection")) {
    return false;
  }
  const octa::Message link = {
      .command = octa::Command::data, .address = {0, 5, 3, 5}, .payload = {1}};
  const auto linked = octa::apply_preamp_update(bank, link);
  return require(linked && *linked && bank.stereo_links[1] && !bank.stereo_links[3],
                 "link notification addresses a stereo pair");
}

bool malformed() {
  auto message = fixture();
  message.payload.pop_back();
  if (!require(!octa::decode_preamps(message), "short bulk state")) { return false; }
  message = fixture();
  message.payload.back() = 0x80;
  if (!require(!octa::decode_preamps(message), "invalid MIDI data outside preamp fields")) {
    return false;
  }
  message = fixture();
  message.command = octa::Command::request;
  if (!require(!octa::decode_preamps(message), "request is not a state reply")) { return false; }
  octa::PreampBank bank{};
  const auto original = bank;
  message = {.command = octa::Command::data, .address = {0, 5, 8, 0}, .payload = {1}};
  if (!require(!octa::apply_preamp_update(bank, message) && bank == original,
               "out-of-range channel preserves state")) {
    return false;
  }
  message.address = {0, 5, 0, 13};
  return require(!octa::apply_preamp_update(bank, message) && bank == original,
                 "unknown parameter preserves state");
}

}

int main() {
  return bulk_layout() && notifications() && malformed() ? 0 : 1;
}
