// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool write_limits() {
  constexpr std::array<unsigned int, 13> reference_maxima = {
      1, 1, 1, 1, 100, 1, 1, 50, 25, 45, 50, 8, 74};
  constexpr unsigned int byte_values = 256;
  octa::PreampBank bank{};
  for (unsigned int parameter = 0; parameter < byte_values; ++parameter) {
    for (unsigned int value = 0; value < byte_values; ++value) {
      const auto kind = static_cast<octa::PreampParameter>(parameter);
      const auto byte = static_cast<std::uint8_t>(value);
      const auto frame =
          octa::encode_preamp_change({.channel = 7, .parameter = kind, .value = byte});
      const bool valid =
          parameter < reference_maxima.size() && value <= reference_maxima[parameter];
      if (!require(frame.has_value() == valid, "exhaustive parameter/value write bounds")) {
        return false;
      }
      if (!frame) { continue; }
      const auto message = octa::decode(*frame);
      if (!require(message.has_value(), "write frame is valid SysEx")) { return false; }
      const auto applied = octa::apply_preamp_update(bank, *message);
      const auto observed = octa::preamp_value(bank, 7, kind);
      if (!require(applied && observed && *observed == value, "notification/write consistency")) {
        return false;
      }
    }
  }
  return true;
}

bool channels() {
  constexpr unsigned int byte_values = 256;
  for (unsigned int channel = 0; channel < byte_values; ++channel) {
    const octa::PreampChange change = {.channel = static_cast<std::uint8_t>(channel),
                                       .parameter = octa::PreampParameter::phase,
                                       .value = 1};
    const auto frame = octa::encode_preamp_change(change);
    if (!require(frame.has_value() == (channel < 8), "channel limits")) { return false; }
    if (!frame) { continue; }
    octa::PreampBank bank{};
    const auto message = octa::decode(*frame);
    if (!require(message.has_value(), "channel frame")) { return false; }
    const auto updated = octa::apply_preamp_update(bank, *message);
    if (!require(updated && *updated, "apply to selected channel")) { return false; }
    for (std::size_t index = 0; index < bank.channels.size(); ++index) {
      if (!require(bank.channels[index].phase_inverted == (index == channel),
                   "other channels remain unchanged")) {
        return false;
      }
    }
  }
  return true;
}

}

int main() {
  return write_limits() && channels() ? 0 : 1;
}
